import json
import uuid
import requests
from datetime import datetime
from typing import Dict, Any, Optional

API_INGEST_URL = "http://localhost:8000/events/ingest"

class EventEmitter:
    """
    Constructs and emits structured JSON events from the YOLOv8 tracking pipeline
    and transmits them to the FastAPI ingestion service.
    """
    def __init__(self, store_id: str, api_url: str = API_INGEST_URL):
        self.store_id = store_id
        self.api_url = api_url
        self.session_sequences: Dict[str, int] = {}
        
    def generate_event(
        self,
        camera_id: str,
        visitor_id: str,
        event_type: str,
        zone_id: Optional[str] = None,
        dwell_ms: int = 0,
        is_staff: bool = False,
        confidence: float = 1.0,
        queue_depth: Optional[int] = None,
        sku_zone: Optional[str] = None
    ) -> Dict[str, Any]:
        """Formats details into the required JSON event schema."""
        # Increment sequence count for visitor session
        seq = self.session_sequences.get(visitor_id, 0) + 1
        self.session_sequences[visitor_id] = seq
        
        event = {
            "event_id": str(uuid.uuid4()),
            "store_id": self.store_id,
            "camera_id": camera_id,
            "visitor_id": visitor_id,
            "event_type": event_type,
            "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "zone_id": zone_id,
            "dwell_ms": dwell_ms,
            "is_staff": is_staff,
            "confidence": float(confidence),
            "metadata": {
                "queue_depth": queue_depth,
                "sku_zone": sku_zone,
                "session_seq": seq
            }
        }
        return event

    def emit_batch(self, events: list) -> bool:
        """Sends a batch of events to the API ingestion service."""
        if not events:
            return True
            
        try:
            payload = {"events": events}
            headers = {"Content-Type": "application/json"}
            response = requests.post(self.api_url, json=payload, headers=headers, timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                print(f"Successfully emitted {data['processed_count']} events (skipped {data['duplicates_skipped']} duplicates).")
                return True
            else:
                print(f"Failed to emit events. HTTP Status: {response.status_code}, Response: {response.text}")
                return False
        except Exception as e:
            print(f"Error connecting to ingestion service: {e}")
            return False
