# PROMPT: Generate pytest unit tests for a FastAPI application that handles store visitor events. Test endpoints: POST /events/ingest, GET /stores/{id}/metrics, and GET /stores/{id}/funnel. Include tests for duplicate event handling, staff event exclusion from metrics, correct conversion rate calculation using correlated POS transactions, and correct funnel drop-off percentages.
# CHANGES MADE: Added SQLite database isolation for tests, seeded test POS transactions, mock events, and validated JSON payloads against models.

import pytest
import sqlite3
import os
from fastapi.testclient import TestClient
from datetime import datetime, timedelta
import app.ingestion
import main

# Mock database path for testing isolation
TEST_DB_PATH = "test_store_intelligence.db"

@pytest.fixture(autouse=True)
def setup_test_db(monkeypatch):
    # Route db connection to test database
    monkeypatch.setattr(app.ingestion, "DB_PATH", TEST_DB_PATH)
    
    # Initialize clean test db
    app.ingestion.init_db()
    
    yield
    
    # Clean up test database
    if os.path.exists(TEST_DB_PATH):
        try:
            os.remove(TEST_DB_PATH)
        except PermissionError:
            pass

client = TestClient(main.app)

def test_event_ingestion_and_idempotency():
    # Test valid event ingestion
    event1 = {
        "event_id": "test-uuid-1",
        "store_id": "STORE_TEST",
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": "VIS_001",
        "event_type": "ENTRY",
        "timestamp": "2026-06-03T14:00:00Z",
        "zone_id": None,
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.95,
        "metadata": {}
    }
    
    response = client.post("/events/ingest", json={"events": [event1]})
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["processed_count"] == 1
    assert res_data["duplicates_skipped"] == 0
    
    # Ingest the exact same event again to test duplicate detection (idempotency)
    response = client.post("/events/ingest", json={"events": [event1]})
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["processed_count"] == 0
    assert res_data["duplicates_skipped"] == 1

def test_metrics_and_staff_exclusion():
    # Seed a transaction
    conn = app.ingestion.get_db_connection()
    conn.execute("""
        INSERT INTO pos_transactions (transaction_id, store_id, timestamp, basket_value_inr)
        VALUES ('TX_001', 'STORE_TEST', '2026-06-03T14:04:30Z', 500.00)
    """)
    conn.commit()
    conn.close()
    
    # Seed events: 1 customer (VIS_001) who entries, visits skincare, billing queue, and exits
    # Also seed 1 staff member (VIS_STAFF) who should be excluded
    events = [
        # Customer VIS_001
        {
            "event_id": "evt-1", "store_id": "STORE_TEST", "camera_id": "CAM_1",
            "visitor_id": "VIS_001", "event_type": "ENTRY", "timestamp": "2026-06-03T14:00:00Z",
            "zone_id": None, "dwell_ms": 0, "is_staff": False, "confidence": 0.9, "metadata": {}
        },
        {
            "event_id": "evt-2", "store_id": "STORE_TEST", "camera_id": "CAM_2",
            "visitor_id": "VIS_001", "event_type": "ZONE_ENTER", "timestamp": "2026-06-03T14:01:00Z",
            "zone_id": "SKINCARE", "dwell_ms": 0, "is_staff": False, "confidence": 0.9, "metadata": {}
        },
        {
            "event_id": "evt-3", "store_id": "STORE_TEST", "camera_id": "CAM_2",
            "visitor_id": "VIS_001", "event_type": "ZONE_DWELL", "timestamp": "2026-06-03T14:02:00Z",
            "zone_id": "SKINCARE", "dwell_ms": 60000, "is_staff": False, "confidence": 0.9, "metadata": {}
        },
        {
            "event_id": "evt-4", "store_id": "STORE_TEST", "camera_id": "CAM_3",
            "visitor_id": "VIS_001", "event_type": "BILLING_QUEUE_JOIN", "timestamp": "2026-06-03T14:03:00Z",
            "zone_id": "CASH_COUNTER", "dwell_ms": 0, "is_staff": False, "confidence": 0.9,
            "metadata": {"queue_depth": 2}
        },
        {
            "event_id": "evt-5", "store_id": "STORE_TEST", "camera_id": "CAM_1",
            "visitor_id": "VIS_001", "event_type": "EXIT", "timestamp": "2026-06-03T14:05:00Z",
            "zone_id": None, "dwell_ms": 300000, "is_staff": False, "confidence": 0.9, "metadata": {}
        },
        # Staff VIS_STAFF (explicitly flagged as staff)
        {
            "event_id": "evt-staff-1", "store_id": "STORE_TEST", "camera_id": "CAM_2",
            "visitor_id": "VIS_STAFF", "event_type": "ZONE_ENTER", "timestamp": "2026-06-03T14:02:00Z",
            "zone_id": "SKINCARE", "dwell_ms": 0, "is_staff": True, "confidence": 0.9, "metadata": {}
        }
    ]
    
    response = client.post("/events/ingest", json={"events": events})
    assert response.status_code == 200
    
    # Query metrics
    res = client.get("/stores/STORE_TEST/metrics")
    assert res.status_code == 200
    data = res.json()
    
    # Assert unique_visitors is 1 (excluding staff)
    assert data["unique_visitors"] == 1
    # Check that transaction was correlated: VIS_001 was in queue in window before 14:04:30
    assert data["conversion_rate"] == 1.0
    # Average dwell in skincare should be 60.0s
    assert data["avg_dwell_by_zone"]["SKINCARE"] == 60.0
    # Current queue depth should be from latest queue join event (2)
    assert data["current_queue_depth"] == 2
    # Abandonment rate: 0 because visitor converted
    assert data["abandonment_rate"] == 0.0

def test_conversion_funnel():
    # Ingest 3 customer entry events (VIS_001, VIS_002, VIS_003)
    # VIS_001 enters and goes to zone
    # VIS_002 enters, goes to zone, joins queue, and buys (seeded tx)
    # VIS_003 enters, goes to zone, joins queue, but abandons (does not buy)
    
    conn = app.ingestion.get_db_connection()
    conn.execute("""
        INSERT INTO pos_transactions (transaction_id, store_id, timestamp, basket_value_inr)
        VALUES ('TX_002', 'STORE_TEST', '2026-06-03T14:04:00Z', 100.00)
    """)
    conn.commit()
    conn.close()
    
    events = [
        # Visitor 1 (Entry + Zone Visit only)
        {"event_id": "e1-1", "store_id": "STORE_TEST", "camera_id": "CAM_1", "visitor_id": "VIS_001", "event_type": "ENTRY", "timestamp": "2026-06-03T14:00:00Z", "zone_id": None, "dwell_ms": 0, "is_staff": False, "confidence": 0.9, "metadata": {}},
        {"event_id": "e1-2", "store_id": "STORE_TEST", "camera_id": "CAM_2", "visitor_id": "VIS_001", "event_type": "ZONE_ENTER", "timestamp": "2026-06-03T14:00:30Z", "zone_id": "SKINCARE", "dwell_ms": 0, "is_staff": False, "confidence": 0.9, "metadata": {}},
        
        # Visitor 2 (Entry + Zone Visit + Queue Join + Purchase)
        {"event_id": "e2-1", "store_id": "STORE_TEST", "camera_id": "CAM_1", "visitor_id": "VIS_002", "event_type": "ENTRY", "timestamp": "2026-06-03T14:01:00Z", "zone_id": None, "dwell_ms": 0, "is_staff": False, "confidence": 0.9, "metadata": {}},
        {"event_id": "e2-2", "store_id": "STORE_TEST", "camera_id": "CAM_2", "visitor_id": "VIS_002", "event_type": "ZONE_ENTER", "timestamp": "2026-06-03T14:01:30Z", "zone_id": "SKINCARE", "dwell_ms": 0, "is_staff": False, "confidence": 0.9, "metadata": {}},
        {"event_id": "e2-3", "store_id": "STORE_TEST", "camera_id": "CAM_3", "visitor_id": "VIS_002", "event_type": "BILLING_QUEUE_JOIN", "timestamp": "2026-06-03T14:02:00Z", "zone_id": "CASH_COUNTER", "dwell_ms": 0, "is_staff": False, "confidence": 0.9, "metadata": {"queue_depth": 1}},
        
        # Visitor 3 (Entry + Zone Visit + Queue Join but no Purchase)
        {"event_id": "e3-1", "store_id": "STORE_TEST", "camera_id": "CAM_1", "visitor_id": "VIS_003", "event_type": "ENTRY", "timestamp": "2026-06-03T14:02:00Z", "zone_id": None, "dwell_ms": 0, "is_staff": False, "confidence": 0.9, "metadata": {}},
        {"event_id": "e3-2", "store_id": "STORE_TEST", "camera_id": "CAM_2", "visitor_id": "VIS_003", "event_type": "ZONE_ENTER", "timestamp": "2026-06-03T14:02:30Z", "zone_id": "SKINCARE", "dwell_ms": 0, "is_staff": False, "confidence": 0.9, "metadata": {}},
        {"event_id": "e3-3", "store_id": "STORE_TEST", "camera_id": "CAM_3", "visitor_id": "VIS_003", "event_type": "BILLING_QUEUE_JOIN", "timestamp": "2026-06-03T14:03:00Z", "zone_id": "CASH_COUNTER", "dwell_ms": 0, "is_staff": False, "confidence": 0.9, "metadata": {"queue_depth": 2}}
    ]
    
    client.post("/events/ingest", json={"events": events})
    
    # Query funnel
    res = client.get("/stores/STORE_TEST/funnel")
    assert res.status_code == 200
    data = res.json()
    
    stages = {s["stage_name"]: s for s in data["stages"]}
    
    # Verify stages counts
    assert stages["Entry"]["count"] == 3
    assert stages["Zone Visit"]["count"] == 3
    assert stages["Billing Queue"]["count"] == 2
    assert stages["Purchase"]["count"] == 1
    
    # Verify drop-offs
    assert stages["Entry"]["drop_off_pct"] == 0.0
    assert stages["Zone Visit"]["drop_off_pct"] == 0.0
    # Drop-off from Zone Visit (3) to Billing Queue (2) is (3 - 2)/3 = 33.33%
    assert stages["Billing Queue"]["drop_off_pct"] == 33.33
    # Drop-off from Billing Queue (2) to Purchase (1) is (2 - 1)/2 = 50.00%
    assert stages["Purchase"]["drop_off_pct"] == 50.0

def test_store_id_mapping_and_conversion():
    import tempfile
    conn = app.ingestion.get_db_connection()
    
    # Write a temporary mock POS transaction CSV
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, newline='', encoding='utf-8') as f:
        f.write("order_id,store_id,order_date,order_time,total_amount\n")
        f.write("TX_MAP_CSV_001,ST1008,03-06-2026,14:04:30,500.00\n")
        temp_csv_path = f.name
        
    try:
        app.ingestion.import_pos_transactions_csv(temp_csv_path)
        
        # Verify that the store_id was correctly mapped to STORE_BLR_002
        cursor = conn.cursor()
        cursor.execute("SELECT store_id FROM pos_transactions WHERE transaction_id = 'TX_MAP_CSV_001'")
        row = cursor.fetchone()
        assert row is not None
        assert row["store_id"] == "STORE_BLR_002"
    finally:
        conn.close()
        if os.path.exists(temp_csv_path):
            try:
                os.remove(temp_csv_path)
            except OSError:
                pass

def test_database_reset():
    # Ingest a mock event
    event = {
        "event_id": "test-reset-evt-1",
        "store_id": "STORE_TEST",
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": "VIS_RESET_001",
        "event_type": "ENTRY",
        "timestamp": "2026-06-03T14:00:00Z",
        "zone_id": None,
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.95,
        "metadata": {}
    }
    response = client.post("/events/ingest", json={"events": [event]})
    assert response.status_code == 200
    
    # Verify event is in DB
    conn = app.ingestion.get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM events;")
    assert cursor.fetchone()[0] > 0
    conn.close()
    
    # Call reset database endpoint
    response = client.post("/db/reset")
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    
    # Verify event is gone from DB
    conn = app.ingestion.get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM events;")
    assert cursor.fetchone()[0] == 0
    conn.close()
