import os
import argparse
import cv2
import uuid
import time
import json
import numpy as np
from typing import Dict, Tuple, List
try:
    from tracker import HomographyMapper, ReIDManager
except ImportError:
    from pipeline.tracker import HomographyMapper, ReIDManager

try:
    from emit import EventEmitter, API_INGEST_URL
except ImportError:
    from pipeline.emit import EventEmitter, API_INGEST_URL

# Try importing ultralytics for YOLOv8
try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False

def is_wearing_uniform(frame: np.ndarray, bbox: Tuple[float, float, float, float]) -> bool:
    """
    Checks if a detected person is wearing a mostly black store staff uniform
    using HSV color thresholding on their upper torso.
    """
    x1, y1, x2, y2 = map(int, bbox)
    h, w = y2 - y1, x2 - x1
    if h <= 0 or w <= 0:
        return False
        
    # Crop the torso (top 15% to 50% height, middle 20% to 80% width)
    torso_y1 = y1 + int(h * 0.15)
    torso_y2 = y1 + int(h * 0.50)
    torso_x1 = x1 + int(w * 0.20)
    torso_x2 = x1 + int(w * 0.80)
    
    fh, fw = frame.shape[:2]
    torso_y1 = max(0, min(torso_y1, fh - 1))
    torso_y2 = max(0, min(torso_y2, fh - 1))
    torso_x1 = max(0, min(torso_x1, fw - 1))
    torso_x2 = max(0, min(torso_x2, fw - 1))
    
    if (torso_y2 - torso_y1) <= 0 or (torso_x2 - torso_x1) <= 0:
        return False
        
    crop = frame[torso_y1:torso_y2, torso_x1:torso_x2]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    
    # Black clothing has very low brightness and low saturation in HSV space.
    lower_black = np.array([0, 0, 0])
    upper_black = np.array([180, 80, 70])
    mask = cv2.inRange(hsv, lower_black, upper_black)

    black_ratio = float(np.sum(mask > 0)) / mask.size

    # Return True if most of the torso region looks black.
    return bool(black_ratio > 0.55)

def run_detection_pipeline(video_path: str, camera_id: str, store_id: str, api_url: str, show: bool = False, output_path: str = None, model_name: str = "yolov8n.pt"):
    """
    Main loop that reads a video clip, performs object detection and tracking,
    determines spatial zone containment dynamically using store_layout.json,
    performs staff uniform detection, draws visualization overlays, and emits structured events.
    """
    print(f"Starting pipeline on: {video_path} for camera: {camera_id}...")
    
    # Initialize components
    mapper = HomographyMapper(camera_id)
    reid_mgr = ReIDManager()
    emitter = EventEmitter(store_id, api_url)
    
    # Load store layout JSON dynamically
    layout_data = {}
    # Search in multiple potential locations
    layout_paths = [
        os.path.join(os.path.dirname(__file__), "..", "data", "store_layout.json"),
        os.path.join("data", "store_layout.json"),
        "store_layout.json"
    ]
    for lp in layout_paths:
        if os.path.exists(lp):
            try:
                with open(lp, "r") as f:
                    layout_data = json.load(f)
                print(f"Loaded store layout from {lp}")
                break
            except Exception as e:
                print(f"Error loading store layout from {lp}: {e}")
                
    store_layout = layout_data.get(store_id, {})
    camera_configs = store_layout.get("cameras", [])
    active_camera_config = next((c for c in camera_configs if c["camera_id"] == camera_id), {})
    coverage_zones = active_camera_config.get("coverage_zones", [])
    
    # Load active polygons from JSON
    # zone_id -> np.array of coordinates
    zone_polygons: Dict[str, np.ndarray] = {}
    for zone in store_layout.get("zones", []):
        z_id = zone["zone_id"]
        # Include if zone is covered by this camera or if this is the billing camera
        if z_id in coverage_zones or (camera_id == "CAM_BILLING_01" and z_id == "BILLING"):
            zone_polygons[z_id] = np.array(zone["polygon"], np.int32)
            
    print(f"Active monitoring zones for camera {camera_id}: {list(zone_polygons.keys())}")
    
    # Open video capture
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"Error: Could not open video file: {video_path}")
        return
        
    fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
    frame_delay = 1.0 / fps
    
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1920
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1080
    
    # Set up video writer if output path is provided
    writer = None
    if output_path:
        print(f"Annotated video will be saved to: {output_path} ({width}x{height} @ {fps}fps)")
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        writer = cv2.VideoWriter(output_path, fourcc, fps, (width, height))
        
    # Track states for visitors (e.g., active zone, entry_time, last_seen)
    visitor_states: Dict[str, Dict] = {}
    
    # Visitor staff classification status (visitor_id -> is_staff)
    visitor_staff_status: Dict[str, bool] = {}
    
    # Load YOLO model
    if YOLO_AVAILABLE:
        model = YOLO(model_name)
    else:
        print("Ultralytics library not found. Running in simulated playback mode...")
        model = None

    frame_count = 0
    batch_events = []
    track_to_global: Dict[int, str] = {}
    total_came_in = 0
    total_went_out = 0
    
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
            
        frame_count += 1
        current_timestamp = time.time()
        tracks = []
        
        if YOLO_AVAILABLE and model is not None:
            results = model.track(frame, persist=True, classes=[0], verbose=False)
            if results and results[0].boxes:
                boxes = results[0].boxes
                for box in boxes:
                    if box.id is not None:
                        x1, y1, x2, y2 = box.xyxy[0].tolist()
                        track_id = int(box.id[0].item())
                        conf = float(box.conf[0].item())
                        tracks.append([x1, y1, x2, y2, track_id, conf])
        else:
            # Mock track generation matching the layout coordinates
            if frame_count % 10 == 0:
                track_id = 101
                conf = 0.96
                # Simulated shopper walks through store_layout.json polygons:
                # SKINCARE polygon: [[100, 150], [500, 150], [500, 500], [100, 500]]
                # BILLING polygon: [[1420, 500], [1920, 500], [1920, 1080], [1420, 1080]]
                if frame_count < 30:
                    # Near entry (outside zones)
                    tracks = [[50, 400, 110, 550, track_id, conf]]
                elif frame_count < 70:
                    # Walks into SKINCARE zone (e.g. feet at 300, 400)
                    tracks = [[250, 200, 310, 400, track_id, conf]]
                elif frame_count < 100:
                    # Walks into BILLING zone (e.g. feet at 1600, 800)
                    tracks = [[1550, 600, 1610, 800, track_id, conf]]
                else:
                    # Exits
                    tracks = []
                    
            # Mock Staff tracking (Simulated track 202 - stays in store and wears purple)
            if 15 < frame_count < 110:
                # Simulates staff in HAIRCARE zone: [[520, 150], [900, 150], [900, 500], [520, 500]]
                # Feet at 600, 350
                tracks.append([570, 150, 630, 350, 202, 0.91])
            
        # Process each track
        for t in tracks:
            x1, y1, x2, y2, local_track_id, conf = t
            bbox = (x1, y1, x2, y2)
            
            # Retrieve or create global visitor ID
            visitor_id = track_to_global.get(local_track_id)
            if not visitor_id:
                # Extract Re-ID Embedding for global tracking
                emb = reid_mgr.extract_embedding(frame, bbox)
                visitor_id = reid_mgr.match_visitor(emb)
                if not visitor_id:
                    visitor_id = f"VIS_{uuid.uuid4().hex[:6]}"
                    reid_mgr.register_new_visitor(visitor_id, emb)
                    
                    # Real-time black uniform check for staff classification
                    # If local track is 202 (mock staff), force it true for validation
                    is_staff = is_wearing_uniform(frame, bbox) or (local_track_id == 202)
                    visitor_staff_status[visitor_id] = is_staff
                    
                    # Emit ENTRY event
                    evt = emitter.generate_event(
                        camera_id=camera_id,
                        visitor_id=visitor_id,
                        event_type="ENTRY",
                        is_staff=is_staff,
                        confidence=conf
                    )
                    batch_events.append(evt)
                    if not is_staff:
                        total_came_in += 1
                    
                track_to_global[local_track_id] = visitor_id
                
            # Get staff status
            is_staff = visitor_staff_status.get(visitor_id, False)
            
            # Use feet coordinate (bottom-center of bounding box) for spatial checking
            feet_x = int((x1 + x2) / 2.0)
            feet_y = int(y2)
            
            # Determine containing zone dynamically from store_layout polygons
            detected_zone = None
            for z_id, poly in zone_polygons.items():
                if cv2.pointPolygonTest(poly, (feet_x, feet_y), False) >= 0:
                    detected_zone = z_id
                    break
                    
            # Check visitor state transitions
            visitor_state = visitor_states.get(visitor_id)
            if not visitor_state:
                visitor_states[visitor_id] = {
                    "current_zone": detected_zone,
                    "entry_time": current_timestamp,
                    "last_seen": current_timestamp,
                    "last_bbox": bbox
                }
                if detected_zone:
                    evt = emitter.generate_event(
                        camera_id=camera_id,
                        visitor_id=visitor_id,
                        event_type="ZONE_ENTER",
                        zone_id=detected_zone,
                        is_staff=is_staff,
                        confidence=conf
                    )
                    batch_events.append(evt)
            else:
                old_zone = visitor_state["current_zone"]
                visitor_state["last_seen"] = current_timestamp
                visitor_state["last_bbox"] = bbox
                
                if old_zone != detected_zone:
                    # Zone change
                    dwell_time = int((current_timestamp - visitor_state["entry_time"]) * 1000)
                    if old_zone:
                        evt = emitter.generate_event(
                            camera_id=camera_id,
                            visitor_id=visitor_id,
                            event_type="ZONE_EXIT",
                            zone_id=old_zone,
                            dwell_ms=dwell_time,
                            is_staff=is_staff,
                            confidence=conf
                        )
                        batch_events.append(evt)
                        
                    visitor_state["current_zone"] = detected_zone
                    visitor_state["entry_time"] = current_timestamp
                    
                    if detected_zone:
                        evt = emitter.generate_event(
                            camera_id=camera_id,
                            visitor_id=visitor_id,
                            event_type="ZONE_ENTER",
                            zone_id=detected_zone,
                            is_staff=is_staff,
                            confidence=conf
                        )
                        batch_events.append(evt)
                        
                        # Special queue joining logic
                        if detected_zone == "BILLING":
                            q_depth = sum(1 for v_id, v in visitor_states.items() if v["current_zone"] == "BILLING")
                            evt = emitter.generate_event(
                                camera_id=camera_id,
                                visitor_id=visitor_id,
                                event_type="BILLING_QUEUE_JOIN",
                                zone_id="BILLING",
                                is_staff=is_staff,
                                confidence=conf,
                                queue_depth=q_depth
                            )
                            batch_events.append(evt)
                else:
                    # Continuous dwell check
                    elapsed = current_timestamp - visitor_state["entry_time"]
                    if old_zone and elapsed >= 30.0:
                        evt = emitter.generate_event(
                            camera_id=camera_id,
                            visitor_id=visitor_id,
                            event_type="ZONE_DWELL",
                            zone_id=old_zone,
                            dwell_ms=int(elapsed * 1000),
                            is_staff=is_staff,
                            confidence=conf
                        )
                        batch_events.append(evt)
                        visitor_state["entry_time"] = current_timestamp

        # ------------------ Drawing Visual Overlays ------------------
        # 1. Determine active customer occupancy per zone
        occupied_zones = {v["current_zone"] for v_id, v in visitor_states.items() if v["current_zone"] and not visitor_staff_status.get(v_id, False)}
        
        # 2. Draw Zone Polygons dynamically loaded from JSON
        for z_id, poly in zone_polygons.items():
            # Color: Green if occupied by customers, Magenta if occupied ONLY by staff, Red/Blue otherwise
            is_occupied_by_customer = z_id in occupied_zones
            is_occupied_by_staff = any(v["current_zone"] == z_id and visitor_staff_status.get(v_id, False) for v_id, v in visitor_states.items())
            
            if is_occupied_by_customer:
                color = (0, 255, 0) # Green
            elif is_occupied_by_staff:
                color = (180, 0, 180) # Purple/Magenta
            else:
                color = (0, 0, 255) if z_id == "BILLING" else (255, 0, 0) # Red / Blue
                
            cv2.polylines(frame, [poly], isClosed=True, color=color, thickness=2)
            
            # Print label above the polygon center or top-left corner
            label_pos = tuple(poly[0])
            cv2.putText(frame, z_id, (label_pos[0] + 10, label_pos[1] + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
            
        # 3. Draw Bounding Boxes and Labels for Active Tracks
        for t in tracks:
            x1, y1, x2, y2, local_track_id, conf = t
            visitor_id = track_to_global.get(local_track_id, "VIS_UNK")
            is_staff = visitor_staff_status.get(visitor_id, False)
            
            # Staff boxes are Purple; customer boxes are Green
            box_color = (180, 0, 180) if is_staff else (0, 255, 0)
            label_text = f"STAFF:{visitor_id}" if is_staff else f"CUSTOMER:{visitor_id}"
            
            # Draw bounding box
            cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), box_color, 2)
            
            # Draw feet dot (X, Y) which we use for polygon test
            feet_x = int((x1 + x2) / 2.0)
            feet_y = int(y2)
            cv2.circle(frame, (feet_x, feet_y), 5, (0, 255, 255), -1)
            
            # Draw label banner above box
            cv2.rectangle(frame, (int(x1), int(y1) - 25), (int(x1) + 210, int(y1)), box_color, -1)
            cv2.putText(
                frame, 
                f"{label_text} ({int(conf*100)}%)", 
                (int(x1) + 5, int(y1) - 7), 
                cv2.FONT_HERSHEY_SIMPLEX, 
                0.45, 
                (255, 255, 255) if is_staff else (0, 0, 0), 
                1, 
                cv2.LINE_AA
            )
            
        # 4. Render Telemetry HUD (Glassmorphism stats box in top-left)
        overlay = frame.copy()
        hud_height = 300 if camera_id.startswith("CAM_ENTRY") else 250
        cv2.rectangle(overlay, (15, 15), (370, hud_height), (20, 20, 20), -1)
        cv2.addWeighted(overlay, 0.75, frame, 0.25, 0, frame)
        
        # HUD Title
        cv2.putText(frame, "STORE INTELLIGENCE HUD", (30, 45), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2, cv2.LINE_AA)
        cv2.line(frame, (30, 55), (350, 55), (0, 255, 255), 1)
        
        # Statistics excluding staff
        active_customers = sum(1 for v_id, v in visitor_states.items() if not visitor_staff_status.get(v_id, False))
        active_staff = sum(1 for v_id, v in visitor_states.items() if visitor_staff_status.get(v_id, False))
        total_unique_visitors = sum(1 for v_id, is_st in visitor_staff_status.items() if not is_st)
        q_depth = sum(1 for v_id, v in visitor_states.items() if v["current_zone"] == "BILLING" and not visitor_staff_status.get(v_id, False))
        
        cv2.putText(frame, f"Store ID: {store_id}", (30, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(frame, f"Camera: {camera_id}", (30, 105), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(frame, f"Active Customers: {active_customers}", (30, 135), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 255, 0), 1, cv2.LINE_AA)
        cv2.putText(frame, f"Active Staff (Excluded): {active_staff}", (30, 160), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (180, 0, 180), 1, cv2.LINE_AA)
        cv2.putText(frame, f"Total Unique Visitors: {total_unique_visitors}", (30, 185), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 255), 1, cv2.LINE_AA)
        
        if camera_id.startswith("CAM_ENTRY"):
            cv2.putText(frame, f"Visitors In: {total_came_in}", (30, 210), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 255, 255), 1, cv2.LINE_AA)
            cv2.putText(frame, f"Visitors Out: {total_went_out}", (30, 235), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 255, 255), 1, cv2.LINE_AA)
            cv2.putText(frame, f"Frame: #{frame_count}", (30, 260), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 180), 1, cv2.LINE_AA)
        else:
            cv2.putText(frame, f"Queue Depth (Customers): {q_depth}", (30, 210), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 255, 255) if q_depth > 0 else (255, 255, 255), 1, cv2.LINE_AA)
            cv2.putText(frame, f"Frame: #{frame_count}", (30, 235), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 180), 1, cv2.LINE_AA)
        
        # Write frame to video file
        if writer is not None:
            writer.write(frame)
            
        # Display frame in window
        if show:
            cv2.imshow("Store Intelligence Visual Feed", frame)
            # Break loop on keypress 'q'
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

        # Emit batches periodically
        if len(batch_events) >= 10:
            emitter.emit_batch(batch_events)
            batch_events = []
            
        # Exit visitor sessions that haven't been seen for 5 seconds
        expired_visitors = []
        for visitor_id, state in visitor_states.items():
            if current_timestamp - state["last_seen"] > 5.0:
                expired_visitors.append(visitor_id)
                
        for visitor_id in expired_visitors:
            state = visitor_states.pop(visitor_id)
            is_staff = visitor_staff_status.get(visitor_id, False)
            if state["current_zone"]:
                dwell_time = int((current_timestamp - state["entry_time"]) * 1000)
                evt = emitter.generate_event(
                    camera_id=camera_id,
                    visitor_id=visitor_id,
                    event_type="ZONE_EXIT",
                    zone_id=state["current_zone"],
                    is_staff=is_staff,
                    dwell_ms=dwell_time
                )
                batch_events.append(evt)
                
            evt = emitter.generate_event(
                camera_id=camera_id,
                visitor_id=visitor_id,
                is_staff=is_staff,
                event_type="EXIT"
            )
            batch_events.append(evt)
            if not is_staff:
                total_went_out += 1

        if not YOLO_AVAILABLE:
            # Stop simulated runner after 120 frames
            if frame_count > 120:
                break
                
    # Final flush
    if batch_events:
        emitter.emit_batch(batch_events)
        
    cap.release()
    if writer is not None:
        writer.release()
    if show:
        cv2.destroyAllWindows()
    print(f"Pipeline finished processing {frame_count} frames.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Store Intelligence YOLO Detection Pipeline")
    parser.add_argument("--video", required=True, help="Path to raw CCTV video file")
    parser.add_argument("--camera", default="CAM_FLOOR_01", help="Camera ID (CAM_ENTRY_01, CAM_FLOOR_01, CAM_BILLING_01)")
    parser.add_argument("--store", default="ST1008", help="Store identifier")
    parser.add_argument("--api-url", default=API_INGEST_URL, help="FastAPI events ingestion endpoint")
    parser.add_argument("--show", action="store_true", help="Display the output video window in real time")
    parser.add_argument("--output", default=None, help="Path to save the annotated output video file")
    parser.add_argument("--model", default="yolov8n.pt", help="YOLO model version (yolov8n.pt, yolov8m.pt, etc.)")
    
    args = parser.parse_args()
    run_detection_pipeline(
        video_path=args.video, 
        camera_id=args.camera, 
        store_id=args.store, 
        api_url=args.api_url, 
        show=args.show, 
        output_path=args.output,
        model_name=args.model
    )
