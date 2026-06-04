import os
import sqlite3
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from app.models import (
    IngestRequest, IngestResponse, StoreMetricsResponse, 
    StoreFunnelResponse, StoreHeatmapResponse, StoreAnomaliesResponse, 
    HealthResponse
)
from app.ingestion import init_db, ingest_event_batch, get_db_connection
from app.metrics import get_store_metrics
from app.funnel import get_store_funnel
from app.heatmap import get_store_heatmap
from app.anomalies import detect_store_anomalies
from app.health import check_service_health

app = FastAPI(
    title="Store Intelligence API",
    description="REST API for real-time offline retail metrics and visitor analytics.",
    version="1.0.0"
)

# Startup event to initialize the SQLite database and seed POS transactions
@app.on_event("startup")
def startup_event():
    # Path to the POS CSV file in the workspace
    pos_csv = r"c:\AI_ML\Computer_Vision\Purple\Brigade_Bangalore_10_April_26 (1)bc6219c.csv"
    if not os.path.exists(pos_csv):
        # Fallback relative search
        pos_csv = "Brigade_Bangalore_10_April_26 (1)bc6219c.csv"
        
    init_db(pos_csv_path=pos_csv if os.path.exists(pos_csv) else None)

# Global Exception Handler for Database Failures (Graceful Degradation to HTTP 503)
@app.exception_handler(sqlite3.Error)
async def database_exception_handler(request: Request, exc: sqlite3.Error):
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "error": "Service Unavailable",
            "message": "Database is temporarily unavailable or connection failed.",
            "details": str(exc)
        }
    )

# Endpoint: Ingest events
@app.post("/events/ingest", response_model=IngestResponse, status_code=status.HTTP_200_OK)
def ingest_events(payload: IngestRequest):
    processed, duplicates, errors = ingest_event_batch(payload.events)
    return IngestResponse(
        status="success" if not errors else "partial_success",
        processed_count=processed,
        duplicates_skipped=duplicates,
        errors=errors if errors else None
    )

# Endpoint: Reset Database values
@app.post("/db/reset", status_code=status.HTTP_200_OK)
def reset_db_endpoint():
    try:
        # Delete events and pos_transactions tables
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DROP TABLE IF EXISTS events;")
        cursor.execute("DROP TABLE IF EXISTS pos_transactions;")
        conn.commit()
        conn.close()
        
        # Re-initialize DB and re-load transactions CSV
        pos_csv = r"c:\AI_ML\Computer_Vision\Purple\Brigade_Bangalore_10_April_26 (1)bc6219c.csv"
        if not os.path.exists(pos_csv):
            pos_csv = "Brigade_Bangalore_10_April_26 (1)bc6219c.csv"
            
        init_db(pos_csv_path=pos_csv if os.path.exists(pos_csv) else None)
        return {"status": "success", "message": "Database reset successfully."}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error resetting database: {e}"
        )

# Endpoint: Store metrics
@app.get("/stores/{id}/metrics", response_model=StoreMetricsResponse)
def get_metrics(id: str):
    try:
        metrics = get_store_metrics(id)
        return StoreMetricsResponse(**metrics)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error calculating metrics: {e}"
        )

# Endpoint: Conversion funnel
@app.get("/stores/{id}/funnel", response_model=StoreFunnelResponse)
def get_funnel(id: str):
    try:
        funnel = get_store_funnel(id)
        return StoreFunnelResponse(**funnel)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error calculating conversion funnel: {e}"
        )

# Endpoint: Store heatmap
@app.get("/stores/{id}/heatmap", response_model=StoreHeatmapResponse)
def get_heatmap(id: str):
    try:
        heatmap = get_store_heatmap(id)
        return StoreHeatmapResponse(**heatmap)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error calculating store heatmap: {e}"
        )

# Endpoint: Store anomalies
@app.get("/stores/{id}/anomalies", response_model=StoreAnomaliesResponse)
def get_anomalies(id: str):
    try:
        anomalies = detect_store_anomalies(id)
        return StoreAnomaliesResponse(store_id=id, anomalies=anomalies)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error checking anomalies: {e}"
        )

# Endpoint: Health status check
@app.get("/health", response_model=HealthResponse)
def get_health():
    try:
        health = check_service_health()
        return HealthResponse(**health)
    except Exception as e:
        # If database fails, check_service_health catches it or triggers 503 handler,
        # but just in case, return unhealthy
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "unhealthy",
                "last_event_timestamp_by_store": {},
                "stale_feeds": {},
                "error": str(e)
            }
        )

# Endpoint: Web UI Dashboard
from fastapi.responses import HTMLResponse

@app.get("/", response_class=HTMLResponse)
def get_dashboard_ui():
    paths_to_check = [
        os.path.join(os.path.dirname(__file__), "app", "dashboard.html"),
        os.path.join(os.path.dirname(__file__), "dashboard.html"),
        "app/dashboard.html",
        "dashboard.html"
    ]
    for p in paths_to_check:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return f.read()
    return "<h3>Dashboard HTML template file not found</h3>"
