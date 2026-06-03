import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Any, List
from app.ingestion import get_db_connection

def correlate_transactions_for_store(store_id: str):
    """
    Correlates pending POS transactions in the store with visitors
    who were in the billing/cash counter zone within 5 minutes before the transaction.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Fetch all uncorrelated transactions for this store
    cursor.execute("""
        SELECT transaction_id, timestamp 
        FROM pos_transactions 
        WHERE store_id = ? AND correlated_visitor_id IS NULL
        ORDER BY timestamp ASC
    """, (store_id,))
    transactions = cursor.fetchall()
    
    for tx in transactions:
        tx_id = tx["transaction_id"]
        tx_time_str = tx["timestamp"]
        
        # Parse transaction time
        tx_time = datetime.strptime(tx_time_str, "%Y-%m-%dT%H:%M:%SZ")
        window_start = (tx_time - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        
        # Find visitors who were in the billing zone or emitted BILLING_QUEUE_JOIN
        # in the [tx_time - 5min, tx_time] window, excluding staff.
        # We sort by timestamp descending to match the visitor closest to checkout time,
        # but also ensure that visitor isn't already assigned to another transaction at a similar time.
        cursor.execute("""
            SELECT distinct visitor_id, timestamp
            FROM events
            WHERE store_id = ? 
              AND is_staff = 0
              AND (event_type = 'BILLING_QUEUE_JOIN' OR zone_id = 'CASH_COUNTER')
              AND timestamp >= ? AND timestamp <= ?
              AND visitor_id NOT IN (
                  SELECT correlated_visitor_id 
                  FROM pos_transactions 
                  WHERE store_id = ? AND correlated_visitor_id IS NOT NULL
              )
            ORDER BY timestamp DESC
        """, (store_id, window_start, tx_time_str, store_id))
        
        candidates = cursor.fetchall()
        if candidates:
            # Match with the closest visitor (first candidate since we ordered by timestamp DESC)
            matched_visitor_id = candidates[0]["visitor_id"]
            cursor.execute("""
                UPDATE pos_transactions
                SET correlated_visitor_id = ?
                WHERE transaction_id = ?
            """, (matched_visitor_id, tx_id))
            
    conn.commit()
    conn.close()

def get_store_metrics(store_id: str) -> Dict[str, Any]:
    """
    Computes real-time store metrics:
    - Unique visitors (excluding staff)
    - Conversion rate
    - Average dwell time by zone (seconds)
    - Current queue depth
    - Abandonment rate
    """
    # Proactively run transaction correlation first
    correlate_transactions_for_store(store_id)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Total unique visitors (excluding staff) who had an ENTRY event
    # If no ENTRY events exist, fallback to any visitor_id who is not staff
    cursor.execute("""
        SELECT COUNT(DISTINCT visitor_id) as count
        FROM events
        WHERE store_id = ? AND is_staff = 0 AND event_type = 'ENTRY'
    """, (store_id,))
    unique_visitors = cursor.fetchone()["count"]
    
    if unique_visitors == 0:
        cursor.execute("""
            SELECT COUNT(DISTINCT visitor_id) as count
            FROM events
            WHERE store_id = ? AND is_staff = 0
        """, (store_id,))
        unique_visitors = cursor.fetchone()["count"]
        
    # 2. Converted visitors (unique visitor_ids matched with a transaction)
    cursor.execute("""
        SELECT COUNT(DISTINCT correlated_visitor_id) as count
        FROM pos_transactions
        WHERE store_id = ? AND correlated_visitor_id IS NOT NULL
    """, (store_id,))
    converted_visitors = cursor.fetchone()["count"]
    
    conversion_rate = float(converted_visitors) / unique_visitors if unique_visitors > 0 else 0.0
    
    # 3. Average dwell by zone (seconds)
    # We find the maximum dwell_ms for each visitor in each zone, and average it.
    cursor.execute("""
        SELECT zone_id, AVG(max_dwell) / 1000.0 as avg_dwell_sec
        FROM (
            SELECT visitor_id, zone_id, MAX(dwell_ms) as max_dwell
            FROM events
            WHERE store_id = ? AND zone_id IS NOT NULL AND is_staff = 0
            GROUP BY visitor_id, zone_id
        )
        GROUP BY zone_id
    """, (store_id,))
    avg_dwell = {}
    for row in cursor.fetchall():
        avg_dwell[row["zone_id"]] = round(row["avg_dwell_sec"], 2)
        
    # 4. Current queue depth
    # Count visitors who entered the billing zone (CASH_COUNTER) or queue and haven't exited
    # Since simulated real-time/batch, we can also look at the latest event's queue_depth metadata.
    cursor.execute("""
        SELECT queue_depth
        FROM events
        WHERE store_id = ? AND event_type = 'BILLING_QUEUE_JOIN'
        ORDER BY timestamp DESC, event_id DESC
        LIMIT 1
    """, (store_id,))
    latest_queue_event = cursor.fetchone()
    if latest_queue_event:
        current_queue_depth = latest_queue_event["queue_depth"] or 0
    else:
        # Fallback manual calculation: count how many people entered queue in the last 10 mins and haven't exited the store
        cursor.execute("""
            SELECT COUNT(DISTINCT q.visitor_id) as count
            FROM events q
            WHERE q.store_id = ? 
              AND q.is_staff = 0 
              AND q.event_type = 'BILLING_QUEUE_JOIN'
              AND q.visitor_id NOT IN (
                  SELECT visitor_id FROM events WHERE store_id = ? AND event_type = 'EXIT'
              )
        """, (store_id, store_id))
        current_queue_depth = cursor.fetchone()["count"]
        
    # 5. Abandonment rate
    # Visitors who joined the queue but left the store (or exited the billing zone)
    # without a transaction correlation.
    cursor.execute("""
        SELECT COUNT(DISTINCT visitor_id) as count
        FROM events
        WHERE store_id = ? AND is_staff = 0 AND event_type = 'BILLING_QUEUE_JOIN'
    """, (store_id,))
    total_queue_joins = cursor.fetchone()["count"]
    
    cursor.execute("""
        SELECT COUNT(DISTINCT visitor_id) as count
        FROM events
        WHERE store_id = ? 
          AND is_staff = 0 
          AND event_type = 'BILLING_QUEUE_JOIN'
          AND visitor_id NOT IN (
              SELECT DISTINCT correlated_visitor_id 
              FROM pos_transactions 
              WHERE store_id = ? AND correlated_visitor_id IS NOT NULL
          )
    """, (store_id, store_id))
    abandoned_queue_joins = cursor.fetchone()["count"]
    
    abandonment_rate = float(abandoned_queue_joins) / total_queue_joins if total_queue_joins > 0 else 0.0
    
    # Date extraction (get the date of the latest event in the store)
    cursor.execute("""
        SELECT timestamp 
        FROM events 
        WHERE store_id = ? 
        ORDER BY timestamp DESC 
        LIMIT 1
    """, (store_id,))
    latest_event = cursor.fetchone()
    date_str = latest_event["timestamp"][:10] if latest_event else datetime.utcnow().strftime("%Y-%m-%d")
    
    conn.close()
    
    return {
        "store_id": store_id,
        "date": date_str,
        "unique_visitors": unique_visitors,
        "conversion_rate": round(conversion_rate, 4),
        "avg_dwell_by_zone": avg_dwell,
        "current_queue_depth": current_queue_depth,
        "abandonment_rate": round(abandonment_rate, 4)
    }
