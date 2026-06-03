import os
import argparse
import cv2
import uuid
import time
from typing import Dict, Tuple, List
from tracker import HomographyMapper, ReIDManager
from emit import EventEmitter, API_INGEST_URL

# Try importing ultralytics for YOLOv8
try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False

def run_detection_pipeline(video_path: str, camera_id: str, store_id: str, api_url: str):
    """
    Main loop that reads a video clip, performs object detection and tracking,
    determines spatial zone containment, and emits structured events.
    """
    print(f"Starting pipeline on: {video_path} for camera: {camera_id}...")
    
    # Initialize components
    mapper = HomographyMapper(camera_id)
    reid_mgr = ReIDManager()
    emitter = EventEmitter(store_id, api_url)
    
    # Open video capture
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"Error: Could not open video file: {video_path}")
        return
        
    fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
    frame_delay = 1.0 / fps
    
    # Track states for visitors (e.g., active zone, dwell times)
    # visitor_id -> {current_zone, entry_time, last_seen}
    visitor_states: Dict[str, Dict] = {}
    
    # Load YOLO model
    if YOLO_AVAILABLE:
        # Load YOLOv8 model (person class only)
        model = YOLO("yolov8m.pt")
    else:
        print("Ultralytics library not found. Running in simulated playback mode...")
        model = None

    frame_count = 0
    batch_events = []
    
    # Polygons defining shelf zones (bounding box coordinates on the floor map)
    # We load polygon outlines corresponding to the floor plan zones
    # E.g., skincare_zone_poly = [(X1, Y1), (X2, Y2), ...]
    
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
            
        frame_count += 1
        current_timestamp = time.time()
        
        # Bounding box format: list of [x1, y1, x2, y2, track_id, conf]
        tracks = []
        
        if YOLO_AVAILABLE and model is not None:
            # Run YOLOv8 detection + ByteTrack tracking
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
            # Mock track generation for demonstration/testing
            # Simulates 1 visitor walking through the store
            if frame_count % 30 == 0:
                # Mock a visitor bounding box
                tracks = [[200, 400, 260, 550, 101, 0.92]]
            
        # Process each track
        for t in tracks:
            x1, y1, x2, y2, local_track_id, conf = t
            bbox = (x1, y1, x2, y2)
            
            # Extract Re-ID Embedding for global tracking
            emb = reid_mgr.extract_embedding(frame, bbox)
            
            # Match visitor using ReID embeddings
            visitor_id = reid_mgr.match_visitor(emb)
            if not visitor_id:
                # Generate new visitor token
                visitor_id = f"VIS_{uuid.uuid4().hex[:6]}"
                reid_mgr.register_new_visitor(visitor_id, emb)
                
                # Emit ENTRY event
                evt = emitter.generate_event(
                    camera_id=camera_id,
                    visitor_id=visitor_id,
                    event_type="ENTRY",
                    confidence=conf
                )
                batch_events.append(evt)
                
            # Map visitor feet coordinates to 2D floor plan
            X, Y = mapper.map_to_floor(bbox)
            
            # Determine containing zone (Skincare, Makeup, Billing, etc.)
            # Point-in-polygon logic
            detected_zone = None
            if Y < 300:
                detected_zone = "MINIMALIST"  # Top wall zone example
            elif Y > 700:
                detected_zone = "FACES_CANADA"  # Bottom wall zone example
            elif X > 800:
                detected_zone = "CASH_COUNTER"
                
            # Check visitor state transitions
            visitor_state = visitor_states.get(visitor_id)
            if not visitor_state:
                visitor_states[visitor_id] = {
                    "current_zone": detected_zone,
                    "entry_time": current_timestamp,
                    "last_seen": current_timestamp
                }
                if detected_zone:
                    # Emit ZONE_ENTER
                    evt = emitter.generate_event(
                        camera_id=camera_id,
                        visitor_id=visitor_id,
                        event_type="ZONE_ENTER",
                        zone_id=detected_zone,
                        confidence=conf
                    )
                    batch_events.append(evt)
            else:
                old_zone = visitor_state["current_zone"]
                visitor_state["last_seen"] = current_timestamp
                
                if old_zone != detected_zone:
                    # Zone change: emit exit for old and enter for new
                    dwell_time = int((current_timestamp - visitor_state["entry_time"]) * 1000)
                    if old_zone:
                        evt = emitter.generate_event(
                            camera_id=camera_id,
                            visitor_id=visitor_id,
                            event_type="ZONE_EXIT",
                            zone_id=old_zone,
                            dwell_ms=dwell_time,
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
                            confidence=conf
                        )
                        batch_events.append(evt)
                        
                        # Special queue joining logic
                        if detected_zone == "CASH_COUNTER":
                            # Calculate queue depth: count other visitors in CASH_COUNTER
                            q_depth = sum(1 for v_id, v in visitor_states.items() if v["current_zone"] == "CASH_COUNTER")
                            evt = emitter.generate_event(
                                camera_id=camera_id,
                                visitor_id=visitor_id,
                                event_type="BILLING_QUEUE_JOIN",
                                zone_id="CASH_COUNTER",
                                confidence=conf,
                                queue_depth=q_depth
                            )
                            batch_events.append(evt)
                else:
                    # Continuous dwell check: check if 30 seconds have elapsed in current zone
                    elapsed = current_timestamp - visitor_state["entry_time"]
                    if old_zone and elapsed >= 30.0:
                        evt = emitter.generate_event(
                            camera_id=camera_id,
                            visitor_id=visitor_id,
                            event_type="ZONE_DWELL",
                            zone_id=old_zone,
                            dwell_ms=int(elapsed * 1000),
                            confidence=conf
                        )
                        batch_events.append(evt)
                        # Reset entry time to cycle every 30s
                        visitor_state["entry_time"] = current_timestamp
                        
        # Emit batches periodically
        if len(batch_events) >= 10:
            emitter.emit_batch(batch_events)
            batch_events = []
            
        # Exit visitor sessions that haven't been seen for 5 seconds (exit threshold)
        expired_visitors = []
        for visitor_id, state in visitor_states.items():
            if current_timestamp - state["last_seen"] > 5.0:
                expired_visitors.append(visitor_id)
                
        for visitor_id in expired_visitors:
            state = visitor_states.pop(visitor_id)
            if state["current_zone"]:
                # Exit last zone
                dwell_time = int((current_timestamp - state["entry_time"]) * 1000)
                evt = emitter.generate_event(
                    camera_id=camera_id,
                    visitor_id=visitor_id,
                    event_type="ZONE_EXIT",
                    zone_id=state["current_zone"],
                    dwell_ms=dwell_time
                )
                batch_events.append(evt)
                
            # Emit EXIT event
            evt = emitter.generate_event(
                camera_id=camera_id,
                visitor_id=visitor_id,
                event_type="EXIT"
            )
            batch_events.append(evt)

        # Control playback rate in simulated mode
        if not YOLO_AVAILABLE:
            time.sleep(frame_delay)
            # Stop simulated runner after 100 frames
            if frame_count > 100:
                break
                
    # Final flush
    if batch_events:
        emitter.emit_batch(batch_events)
        
    cap.release()
    print(f"Pipeline finished processing {frame_count} frames.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Store Intelligence YOLO Detection Pipeline")
    parser.add_argument("--video", required=True, help="Path to raw CCTV video file")
    parser.add_argument("--camera", default="CAM_FLOOR_01", help="Camera ID (CAM_ENTRY_01, CAM_FLOOR_01, CAM_BILLING_01)")
    parser.add_argument("--store", default="ST1008", help="Store identifier")
    parser.add_argument("--api-url", default=API_INGEST_URL, help="FastAPI events ingestion endpoint")
    
    args = parser.parse_args()
    run_detection_pipeline(args.video, args.camera, args.store, args.api_url)
