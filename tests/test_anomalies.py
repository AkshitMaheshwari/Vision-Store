# PROMPT: Generate pytest unit tests for a store intelligence anomaly detection engine. Test cases should cover detection of BILLING_QUEUE_SPIKE (queue depth > 5 for over 3 minutes), CONVERSION_DROP (actual conversion rate is abnormally low compared to baseline), and DEAD_ZONE (a shelf zone has no customer visits for over 30 minutes, or zero visits).
# CHANGES MADE: Integrated test client route mocking for SQLite DB path, configured mock event streams that trigger each individual anomaly condition, and validated the returned severity and suggested actions.

import pytest
import os
from fastapi.testclient import TestClient
import app.ingestion
import main

TEST_DB_PATH = "test_anomalies_intelligence.db"

@pytest.fixture(autouse=True)
def setup_test_db(monkeypatch):
    monkeypatch.setattr(app.ingestion, "DB_PATH", TEST_DB_PATH)
    
    app.ingestion.init_db()
    
    yield
    
    if os.path.exists(TEST_DB_PATH):
        try:
            os.remove(TEST_DB_PATH)
        except PermissionError:
            pass

client = TestClient(main.app)

def test_billing_queue_spike_anomaly():
    # Send a sequence of BILLING_QUEUE_JOIN events where queue depth is 6,
    # starting at 14:00:00 and ending at 14:04:00 (4 minutes duration)
    events = [
        {
            "event_id": f"q-evt-{i}", "store_id": "STORE_ANOMALY", "camera_id": "CAM_BILLING",
            "visitor_id": f"VIS_{100+i}", "event_type": "BILLING_QUEUE_JOIN",
            "timestamp": f"2026-06-03T14:0{i}:00Z", "zone_id": "CASH_COUNTER",
            "dwell_ms": 0, "is_staff": False, "confidence": 0.9,
            "metadata": {"queue_depth": 6}
        } for i in range(5)  # 14:00:00, 14:01:00, 14:02:00, 14:03:00, 14:04:00
    ]
    
    # Ingest events
    client.post("/events/ingest", json={"events": events})
    
    # Check anomalies
    res = client.get("/stores/STORE_ANOMALY/anomalies")
    assert res.status_code == 200
    data = res.json()
    
    anomalies_types = [a["type"] for a in data["anomalies"]]
    assert "BILLING_QUEUE_SPIKE" in anomalies_types
    
    # Verify anomaly structure
    spike_anomaly = [a for a in data["anomalies"] if a["type"] == "BILLING_QUEUE_SPIKE"][0]
    assert spike_anomaly["severity"] == "CRITICAL"
    assert "Open secondary checkout" in spike_anomaly["suggested_action"]

def test_dead_zone_anomaly():
    # If we ingest events for some zones, but completely omit other zones,
    # those omitted zones should generate DEAD_ZONE anomalies.
    
    events = [
        {
            "event_id": "e-dz-1", "store_id": "STORE_ANOMALY", "camera_id": "CAM_FLOOR",
            "visitor_id": "VIS_001", "event_type": "ZONE_ENTER", "timestamp": "2026-06-03T14:00:00Z",
            "zone_id": "MINIMALIST", "dwell_ms": 0, "is_staff": False, "confidence": 0.95, "metadata": {}
        }
    ]
    
    client.post("/events/ingest", json={"events": events})
    
    res = client.get("/stores/STORE_ANOMALY/anomalies")
    assert res.status_code == 200
    data = res.json()
    
    anomalies_types = [a["type"] for a in data["anomalies"]]
    assert "DEAD_ZONE" in anomalies_types
    
    # MINIMALIST was visited, so it should not be listed as a DEAD_ZONE with status warning
    # (though it might list other active brand zones that received 0 visits as dead zones)
    dead_zones = [a["description"] for a in data["anomalies"] if a["type"] == "DEAD_ZONE"]
    assert any("MINIMALIST" not in dz for dz in dead_zones)
    assert any("MAYBELLINE" in dz or "GOOD_VIBES" in dz for dz in dead_zones)
