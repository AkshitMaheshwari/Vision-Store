from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime

class EventMetadata(BaseModel):
    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: Optional[int] = None

class EventSchema(BaseModel):
    event_id: str = Field(..., description="Globally unique UUIDv4")
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: str = Field(..., description="ENTRY, EXIT, ZONE_ENTER, ZONE_EXIT, ZONE_DWELL, BILLING_QUEUE_JOIN, BILLING_QUEUE_ABANDON, REENTRY")
    timestamp: datetime = Field(..., description="ISO-8601 UTC timestamp")
    zone_id: Optional[str] = None
    dwell_ms: int = Field(default=0, description="Dwell duration in milliseconds")
    is_staff: bool = False
    confidence: float = Field(..., ge=0.0, le=1.0)
    metadata: EventMetadata = Field(default_factory=EventMetadata)

class IngestRequest(BaseModel):
    events: List[EventSchema]

class IngestResponse(BaseModel):
    status: str
    processed_count: int
    duplicates_skipped: int
    errors: Optional[List[Dict[str, Any]]] = None

class StoreMetricsResponse(BaseModel):
    store_id: str
    date: str
    unique_visitors: int
    conversion_rate: float
    avg_dwell_by_zone: Dict[str, float]
    current_queue_depth: int
    abandonment_rate: float

class FunnelStage(BaseModel):
    stage_name: str
    count: int
    drop_off_pct: float

class StoreFunnelResponse(BaseModel):
    store_id: str
    stages: List[FunnelStage]

class HeatmapItem(BaseModel):
    zone_id: str
    visit_count: int
    avg_dwell_seconds: float
    normalized_score: float

class StoreHeatmapResponse(BaseModel):
    store_id: str
    data_confidence: bool
    heatmap: List[HeatmapItem]

class AnomalyItem(BaseModel):
    anomaly_id: str
    timestamp: datetime
    type: str  # BILLING_QUEUE_SPIKE, CONVERSION_DROP, DEAD_ZONE
    severity: str  # INFO, WARN, CRITICAL
    description: str
    suggested_action: str

class StoreAnomaliesResponse(BaseModel):
    store_id: str
    anomalies: List[AnomalyItem]

class HealthResponse(BaseModel):
    status: str
    last_event_timestamp_by_store: Dict[str, Optional[datetime]]
    stale_feeds: Dict[str, bool]
