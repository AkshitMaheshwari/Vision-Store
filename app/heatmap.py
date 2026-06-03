import sqlite3
from typing import Dict, Any, List
from app.ingestion import get_db_connection

def get_store_heatmap(store_id: str) -> Dict[str, Any]:
    """
    Computes traffic heatmaps for each shelf/product zone in the store.
    Normalizes scores to 0-100 based on unique visit count.
    Sets data_confidence = True if total sessions >= 20.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Check data confidence: total unique visitor sessions in store
    cursor.execute("""
        SELECT COUNT(DISTINCT visitor_id) as session_count
        FROM events
        WHERE store_id = ? AND is_staff = 0
    """, (store_id,))
    session_count = cursor.fetchone()["session_count"]
    data_confidence = session_count >= 20
    
    # 2. Get unique visits and average dwell times for all zones
    # A unique visit is counted when a visitor has a ZONE_ENTER event.
    # Exclude null zones, CASH_COUNTER, PMU, and staff.
    cursor.execute("""
        SELECT zone_id, 
               COUNT(DISTINCT visitor_id) as visit_count,
               AVG(max_dwell) / 1000.0 as avg_dwell_sec
        FROM (
            SELECT visitor_id, zone_id, MAX(dwell_ms) as max_dwell
            FROM events
            WHERE store_id = ? 
              AND zone_id IS NOT NULL 
              AND zone_id != 'CASH_COUNTER' 
              AND zone_id != 'PMU'
              AND is_staff = 0
            GROUP BY visitor_id, zone_id
        )
        GROUP BY zone_id
    """, (store_id,))
    
    zones_data = cursor.fetchall()
    conn.close()
    
    # List of all expected active brand zones from store layout to ensure we cover empty zones
    active_zones = [
        "EB_KOREAN", "THE_FACE_SHOP", "GOOD_VIBES", "DERMDOC", "MINIMALIST", 
        "AQUALOGICA", "LAKME_SKIN", "ACCESSORIES", "MAYBELLINE", "FACES_CANADA", 
        "LAKME", "COLORBAR_SUGAR", "SWISS_BEAUTY", "RENEE_NY_BAE", "ALPS_GOODNESS", "STREAX"
    ]
    
    heatmap_dict = {
        zone: {"visit_count": 0, "avg_dwell_seconds": 0.0} for zone in active_zones
    }
    
    for row in zones_data:
        z_id = row["zone_id"]
        # Ensure we keep track of valid zones in our layout list
        if z_id in heatmap_dict:
            heatmap_dict[z_id] = {
                "visit_count": row["visit_count"],
                "avg_dwell_seconds": round(row["avg_dwell_sec"], 2)
            }
            
    # Calculate normalization scores (0-100) based on visit count
    counts = [item["visit_count"] for item in heatmap_dict.values()]
    max_count = max(counts) if counts else 0
    min_count = min(counts) if counts else 0
    count_range = max_count - min_count
    
    heatmap_list = []
    for zone, data in heatmap_dict.items():
        visit_count = data["visit_count"]
        # Normalize score
        if max_count == min_count:
            norm_score = 100.0 if max_count > 0 else 0.0
        else:
            norm_score = round(((visit_count - min_count) / count_range * 100.0), 2)
            
        heatmap_list.append({
            "zone_id": zone,
            "visit_count": visit_count,
            "avg_dwell_seconds": data["avg_dwell_seconds"],
            "normalized_score": norm_score
        })
        
    return {
        "store_id": store_id,
        "data_confidence": data_confidence,
        "heatmap": heatmap_list
    }
