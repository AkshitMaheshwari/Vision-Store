# Store Intelligence System - Quickstart Guide

This project is a containerized real-time Store Intelligence API and Computer Vision pipeline designed to analyze customer behavior and purchase conversions in physical retail stores.

---

## 1. Quickstart Setup (5 Commands)

Run these five commands in your terminal to build, start, and verify the entire system:

```bash
# 1. Navigate to the project root directory
cd store-intelligence

# 2. Build and start the containerized REST API in the background
docker-compose up --build -d

# 3. Run the complete pytest suite inside the container to verify endpoints
docker-compose exec api pytest tests/ -v

# 4. Run the simulated pipeline to generate and ingest store visitor events
python pipeline/detect.py --video ../cct.pdf --camera CAM_FLOOR_01

# 5. Query the store metrics endpoint to check the conversion rate
curl http://localhost:8000/stores/ST1008/metrics
```

---

## 2. API Services & Endpoints

Once the docker container is running, the following endpoints are available on `http://localhost:8000`:

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/events/ingest` | Ingests a batch of visitor events (deduplicated by `event_id`). |
| `GET` | `/stores/{id}/metrics` | Returns visitor count, conversion rate, and average zone dwell times. |
| `GET` | `/stores/{id}/funnel` | Returns customer conversion funnel (`Entry -> Zone Visit -> Queue -> Buy`). |
| `GET` | `/stores/{id}/heatmap` | Returns traffic density and average dwell score normalized 0-100 per zone. |
| `GET` | `/stores/{id}/anomalies` | Returns operational anomalies (spikes, drop-offs, dead zones). |
| `GET` | `/health` | Returns API status, last event timestamps, and feed lag warnings. |

### Swagger Documentation
Interactive API docs are available at [http://localhost:8000/docs](http://localhost:8000/docs) once the service is running.

---

## 3. Running Unit Tests Locally

To run the unit test suite locally on your host machine (outside Docker):

```bash
pip install -r requirements.txt
pytest tests/ -v
```
*(Make sure python has fastapi, uvicorn, pydantic, and pytest installed)*

---

## 4. Computer Vision Pipeline Execution

The edge pipeline is executed using the following parameters:

```bash
python pipeline/detect.py --video <path_to_video_clip.mp4> --camera <camera_id> --store <store_id>
```

- **Entry Camera**: Use `--camera CAM_ENTRY_01` to track store entries/exits.
- **Main Floor Camera**: Use `--camera CAM_FLOOR_01` to track product shelf visits.
- **Billing Camera**: Use `--camera CAM_BILLING_01` to monitor cash counter queues.
