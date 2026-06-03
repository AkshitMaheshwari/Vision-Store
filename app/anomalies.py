import sqlite3
import uuid
import json
from datetime import datetime, timedelta
from typing import Dict, Any, List
from app.ingestion import get_db_connection
from app.metrics import get_store_metrics

def detect_store_anomalies(store_id: str) -> List[Dict[str, Any]]:
    """
    Scans the database and detects active operational anomalies:
    1. BILLING_QUEUE_SPIKE: Queue depth > 5 for over 3 minutes.
    2. CONVERSION_DROP: Current conversion rate is >30% lower than a 7-day baseline (or baseline of 25%).
    3. DEAD_ZONE: No visits in a product zone for 30 minutes.
    
    Returns a list of structured anomaly items.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    anomalies = []
    
    # Get current simulated time (timestamp of the latest event in the store)
    cursor.execute("""
        SELECT timestamp 
        FROM events 
        WHERE store_id = ? 
        ORDER BY timestamp DESC 
        LIMIT 1
    """, (store_id,))
    latest_event = cursor.fetchone()
    if not latest_event:
        conn.close()
        return anomalies
        
    current_time_str = latest_event["timestamp"]
    current_time = datetime.strptime(current_time_str, "%Y-%m-%dT%H:%M:%SZ")
    
    # --- 1. BILLING_QUEUE_SPIKE Detection ---
    # Find all BILLING_QUEUE_JOIN events in the last 15 minutes
    window_15m_ago = (current_time - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cursor.execute("""
        SELECT timestamp, queue_depth
        FROM events
        WHERE store_id = ? AND event_type = 'BILLING_QUEUE_JOIN' AND timestamp >= ?
        ORDER BY timestamp ASC
    """, (store_id, window_15m_ago))
    
    queue_events = cursor.fetchall()
    spike_start = None
    spike_detected = False
    
    for eq in queue_events:
        q_depth = eq["queue_depth"] or 0
            
        eq_time = datetime.strptime(eq["timestamp"], "%Y-%m-%dT%H:%M:%SZ")
        
        if q_depth > 5:
            if spike_start is None:
                spike_start = eq_time
            elif (eq_time - spike_start).total_seconds() >= 180: # 3 minutes
                spike_detected = True
        else:
            # Queue cleared
            spike_start = None
            
    if spike_detected:
        anomalies.append({
            "anomaly_id": str(uuid.uuid4()),
            "timestamp": current_time,
            "type": "BILLING_QUEUE_SPIKE",
            "severity": "CRITICAL",
            "description": "Billing queue depth has exceeded 5 customers for over 3 minutes, causing high checkout latency.",
            "suggested_action": "Open secondary checkout cash register immediately and deploy floater staff to billing."
        })
        
    # --- 2. CONVERSION_DROP Detection ---
    # Get current metrics (which triggers transaction correlation)
    metrics = get_store_metrics(store_id)
    current_conv = metrics["conversion_rate"]
    
    # 7-day average baseline calculation
    # For a real store, we would query the last 7 days.
    # If we have insufficient historical data, we assume a standard store baseline of 22% (0.22)
    cursor.execute("""
        SELECT COUNT(DISTINCT correlated_visitor_id) as tx_count, 
               (SELECT COUNT(DISTINCT visitor_id) FROM events WHERE store_id = ? AND is_staff = 0 AND timestamp < ?) as hist_visitors
        FROM pos_transactions
        WHERE store_id = ? AND correlated_visitor_id IS NOT NULL AND timestamp < ?
    """, (store_id, window_15m_ago, store_id, window_15m_ago))
    
    hist = cursor.fetchone()
    hist_tx = hist["tx_count"] or 0
    hist_vis = hist["hist_visitors"] or 0
    
    baseline_conv = float(hist_tx) / hist_vis if hist_vis >= 10 else 0.22
    
    # If current conversion is >30% below baseline (e.g. current < 0.7 * baseline)
    if baseline_conv > 0 and current_conv < (0.7 * baseline_conv):
        anomalies.append({
            "anomaly_id": str(uuid.uuid4()),
            "timestamp": current_time,
            "type": "CONVERSION_DROP",
            "severity": "WARN",
            "description": f"Conversion rate is currently {round(current_conv*100, 1)}%, which is more than 30% below the baseline of {round(baseline_conv*100, 1)}%.",
            "suggested_action": "Inspect the billing counter for queue abandonment, check for POS checkout failures, or verify uniform staff coverage."
        })
        
    # --- 3. DEAD_ZONE Detection ---
    # Get all zones in the layout sheet (we hardcode or load from database)
    # The active zones from the floor plan:
    active_zones = [
        "EB_KOREAN", "THE_FACE_SHOP", "GOOD_VIBES", "DERMDOC", "MINIMALIST", 
        "AQUALOGICA", "LAKME_SKIN", "ACCESSORIES", "MAYBELLINE", "FACES_CANADA", 
        "LAKME", "COLORBAR_SUGAR", "SWISS_BEAUTY", "RENEE_NY_BAE", "ALPS_GOODNESS", "STREAX"
    ]
    
    for zone in active_zones:
        # Find the last time this zone had a ZONE_ENTER event
        cursor.execute("""
            SELECT timestamp
            FROM events
            WHERE store_id = ? AND zone_id = ? AND event_type = 'ZONE_ENTER' AND is_staff = 0
            ORDER BY timestamp DESC
            LIMIT 1
        """, (store_id, zone))
        
        last_visit = cursor.fetchone()
        if last_visit:
            last_visit_time = datetime.strptime(last_visit["timestamp"], "%Y-%m-%dT%H:%M:%SZ")
            idle_seconds = (current_time - last_visit_time).total_seconds()
            if idle_seconds >= 1800: # 30 minutes
                anomalies.append({
                    "anomaly_id": str(uuid.uuid4()),
                    "timestamp": current_time,
                    "type": "DEAD_ZONE",
                    "severity": "WARN",
                    "description": f"The zone '{zone}' has not received any customer visits in the last {int(idle_seconds/60)} minutes.",
                    "suggested_action": "Check if products in this zone need restocking, or check if the camera feed covers the area correctly."
                })
        else:
            # Never visited in the database
            anomalies.append({
                "anomaly_id": str(uuid.uuid4()),
                "timestamp": current_time,
                "type": "DEAD_ZONE",
                "severity": "INFO",
                "description": f"The zone '{zone}' has received 0 customer visits since tracking started.",
                "suggested_action": "Inspect shelf visual merchandising or check if the zone configuration polygon is aligned with the camera angle."
            })
            
    conn.close()
    return anomalies
