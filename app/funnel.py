import sqlite3
from typing import Dict, Any, List
from app.ingestion import get_db_connection
from app.metrics import correlate_transactions_for_store

def get_store_funnel(store_id: str) -> Dict[str, Any]:
    """
    Computes conversion funnel stages for a store:
    1. Entry (total unique visitors)
    2. Zone Visit (visitors entering at least one product/brand zone)
    3. Billing Queue (visitors joining the billing queue)
    4. Purchase (visitors with correlated transactions)
    
    Excludes staff. Handles division by zero gracefully.
    """
    # Correlate transactions first
    correlate_transactions_for_store(store_id)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Stage 1: Entry
    cursor.execute("""
        SELECT COUNT(DISTINCT visitor_id) as count
        FROM events
        WHERE store_id = ? AND is_staff = 0
    """, (store_id,))
    entry_count = cursor.fetchone()["count"]
    
    # Stage 2: Zone Visit
    # Visitors who entered any zone that is not CASH_COUNTER, PMU, or null
    cursor.execute("""
        SELECT COUNT(DISTINCT visitor_id) as count
        FROM events
        WHERE store_id = ? AND is_staff = 0 
          AND zone_id IS NOT NULL 
          AND zone_id != 'CASH_COUNTER' 
          AND zone_id != 'PMU'
    """, (store_id,))
    zone_visit_count = cursor.fetchone()["count"]
    
    # Stage 3: Billing Queue
    cursor.execute("""
        SELECT COUNT(DISTINCT visitor_id) as count
        FROM events
        WHERE store_id = ? AND is_staff = 0
          AND (event_type = 'BILLING_QUEUE_JOIN' OR zone_id = 'CASH_COUNTER')
    """, (store_id,))
    billing_queue_count = cursor.fetchone()["count"]
    
    # Stage 4: Purchase
    cursor.execute("""
        SELECT COUNT(DISTINCT correlated_visitor_id) as count
        FROM pos_transactions
        WHERE store_id = ? AND correlated_visitor_id IS NOT NULL
    """, (store_id,))
    purchase_count = cursor.fetchone()["count"]
    
    conn.close()
    
    # Ensure counts are cascading (i.e. Stage N <= Stage N-1) for logical consistency
    # (Though in reality, a visitor can go straight to purchase, we treat Entry as the universe)
    zone_visit_count = min(zone_visit_count, entry_count)
    billing_queue_count = min(billing_queue_count, zone_visit_count)
    purchase_count = min(purchase_count, billing_queue_count)
    
    # Calculate drop-off percentages compared to the previous stage
    stages = []
    
    # Entry
    stages.append({
        "stage_name": "Entry",
        "count": entry_count,
        "drop_off_pct": 0.0
    })
    
    # Zone Visit
    drop_off_zone = round(((entry_count - zone_visit_count) / entry_count * 100.0), 2) if entry_count > 0 else 0.0
    stages.append({
        "stage_name": "Zone Visit",
        "count": zone_visit_count,
        "drop_off_pct": drop_off_zone
    })
    
    # Billing Queue
    drop_off_billing = round(((zone_visit_count - billing_queue_count) / zone_visit_count * 100.0), 2) if zone_visit_count > 0 else 0.0
    stages.append({
        "stage_name": "Billing Queue",
        "count": billing_queue_count,
        "drop_off_pct": drop_off_billing
    })
    
    # Purchase
    drop_off_purchase = round(((billing_queue_count - purchase_count) / billing_queue_count * 100.0), 2) if billing_queue_count > 0 else 0.0
    stages.append({
        "stage_name": "Purchase",
        "count": purchase_count,
        "drop_off_pct": drop_off_purchase
    })
    
    return {
        "store_id": store_id,
        "stages": stages
    }
