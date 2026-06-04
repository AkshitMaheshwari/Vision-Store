import sqlite3
import os
import json
import csv
from datetime import datetime
from typing import List, Tuple, Dict, Any
from app.models import EventSchema

DB_PATH = "store_intelligence.db"

def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Enable foreign keys
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db(pos_csv_path: str = None):
    """Initializes the SQLite database tables and indexes."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Create events table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS events (
        event_id TEXT PRIMARY KEY,
        store_id TEXT NOT NULL,
        camera_id TEXT NOT NULL,
        visitor_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        zone_id TEXT,
        dwell_ms INTEGER DEFAULT 0,
        is_staff INTEGER DEFAULT 0,
        confidence REAL NOT NULL,
        queue_depth INTEGER,
        sku_zone TEXT,
        session_seq INTEGER
    );
    """)
    
    # Create POS transactions table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS pos_transactions (
        transaction_id TEXT PRIMARY KEY,
        store_id TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        basket_value_inr REAL NOT NULL,
        correlated_visitor_id TEXT
    );
    """)
    
    # Create indexes for fast lookup and metrics calculations
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_visitor ON events (visitor_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_store_time ON events (store_id, timestamp);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events (event_type);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pos_store_time ON pos_transactions (store_id, timestamp);")
    
    conn.commit()
    conn.close()
    
    # Optionally load POS transactions from CSV
    if pos_csv_path and os.path.exists(pos_csv_path):
        import_pos_transactions_csv(pos_csv_path)

def import_pos_transactions_csv(csv_path: str):
    """Parses POS transactions from the provided CSV file and loads them into SQLite."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    print(f"Importing POS transactions from {csv_path}...")
    imported_count = 0
    skipped_count = 0
    
    with open(csv_path, mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                # Map fields from the CSV columns
                # e.g., order_id, order_date, order_time, store_id, total_amount
                order_id = row.get("order_id") or row.get("invoice_number")
                if not order_id:
                    continue
                
                store_id = row.get("store_id") or "ST1008" # Fallback if missing
                if store_id == "ST1008":
                    store_id = "STORE_BLR_002"
                
                # Combine order_date (DD-MM-YYYY) and order_time (HH:MM:SS)
                date_str = row.get("order_date")
                time_str = row.get("order_time")
                
                if date_str and time_str:
                    # Input format e.g. "10-04-2026" and "16:55:36"
                    # Let's parse and output standard ISO-8601 UTC string
                    dt = datetime.strptime(f"{date_str} {time_str}", "%d-%m-%Y %H:%M:%S")
                    timestamp_iso = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                else:
                    timestamp_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                    
                basket_value = float(row.get("total_amount") or row.get("GMV") or 0.0)
                
                cursor.execute("""
                INSERT OR IGNORE INTO pos_transactions (transaction_id, store_id, timestamp, basket_value_inr)
                VALUES (?, ?, ?, ?)
                """, (order_id, store_id, timestamp_iso, basket_value))
                if cursor.rowcount > 0:
                    imported_count += 1
                else:
                    skipped_count += 1
            except Exception as e:
                print(f"Skipped row in CSV due to error: {e}")
                skipped_count += 1
                
    conn.commit()
    conn.close()
    print(f"POS CSV import completed. Imported: {imported_count}, Skipped/Duplicates: {skipped_count}")

def ingest_event_batch(events: List[EventSchema]) -> Tuple[int, int, List[Dict[str, Any]]]:
    """Ingests a batch of events, handles deduplication, and processes retroactive staff flags."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    processed = 0
    duplicates = 0
    errors = []
    
    for event in events:
        try:
            # Check for duplicate event_id
            cursor.execute("SELECT 1 FROM events WHERE event_id = ?", (event.event_id,))
            if cursor.fetchone():
                duplicates += 1
                continue
                
            cursor.execute("""
            INSERT INTO events (
                event_id, store_id, camera_id, visitor_id, event_type, timestamp, 
                zone_id, dwell_ms, is_staff, confidence, queue_depth, sku_zone, session_seq
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event.event_id,
                event.store_id,
                event.camera_id,
                event.visitor_id,
                event.event_type,
                event.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
                event.zone_id,
                event.dwell_ms,
                1 if event.is_staff else 0,
                event.confidence,
                event.metadata.queue_depth,
                event.metadata.sku_zone,
                event.metadata.session_seq
            ))
            processed += 1
        except Exception as e:
            errors.append({"event_id": event.event_id, "error": str(e)})
            
    conn.commit()
    conn.close()
    
    # Run retroactive staff classification
    if processed > 0:
        classify_staff_behavior()
        
    return processed, duplicates, errors

def classify_staff_behavior():
    """
    Scans the database for behavior indicative of store staff:
    1. Visitors with total active session span > 60 minutes.
    2. Visitors who have visited more than 12 unique zones.
    If flagged, updates all records for this visitor_id to is_staff = 1.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Fetch visitors whose span or zone variety suggests they are staff
    # We group by visitor_id, calculate duration from min/max timestamps, and count unique zones.
    cursor.execute("""
    SELECT visitor_id, 
           (strftime('%s', max(timestamp)) - strftime('%s', min(timestamp))) as duration_seconds,
           count(distinct zone_id) as unique_zones
    FROM events
    WHERE is_staff = 0
    GROUP BY visitor_id
    HAVING duration_seconds > 3600 OR unique_zones > 12
    """)
    
    staff_visitors = [row["visitor_id"] for row in cursor.fetchall()]
    
    if staff_visitors:
        print(f"Retroactively flagging staff members: {staff_visitors}")
        # Update is_staff = 1 for all events of these visitors
        placeholders = ",".join("?" for _ in staff_visitors)
        cursor.execute(f"""
        UPDATE events
        SET is_staff = 1
        WHERE visitor_id IN ({placeholders})
        """, staff_visitors)
        conn.commit()
        
    conn.close()
