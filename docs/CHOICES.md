# CHOICES.md - Technical Choices and Trade-offs

This document details the primary engineering decisions made while building the Store Intelligence system, documenting options considered, AI recommendations, and the selected solutions.

---

## 1. Detection Model Selection

### Options Considered
1. **YOLOv8 (Ultralytics)**: Modern single-stage detector with built-in ByteTrack/BoT-SORT integration.
2. **YOLOv9 / YOLOv10**: Slightly higher theoretical accuracy, but fewer stable production wrappers and higher model overhead.
3. **RT-DETR (Real-Time DEtection Transformer)**: Transformer-based detector. Excellent accuracy but demands significant GPU memory and features high inference latency on non-CUDA edge hardware.

### AI Recommendation
AI suggested using **YOLOv8m (medium)** as a sweet spot for edge computing, with a tracking framework built using standard OpenCV centroids if Ultralytics is too heavy.

### Our Choice and Rationale
We chose **YOLOv8m**.
- **Edge Deployment**: YOLOv8 provides optimized export paths to ONNX and TensorRT. In a real-world store, running inference on 3 cameras simultaneously requires a model that can run at 15fps on mid-range edge accelerators (e.g. NVIDIA Jetson).
- **Tracking Integration**: Ultralytics has direct, native support for **ByteTrack**. ByteTrack utilizes low-confidence bounding boxes to maintain tracklets during occlusion (e.g., when a client walks behind a cosmetics pillar), reducing track fragmentation without requiring custom Kalman-filter glue code.

---

## 2. Event Schema Design

### Options Considered
1. **Granular Coordinates Logging**: Emit raw $(x,y)$ bounding boxes coordinates to the server every frame.
2. **State-Change Event Streams**: Emit structured events only when state changes occur (e.g., `ZONE_ENTER`, `ZONE_EXIT`, `ZONE_DWELL` every 30s).

### AI Recommendation
AI recommended emitting coordinate streams every 5 frames and calculating zone bounding box overlaps inside the REST API to keep the edge pipeline "dumb."

### Our Choice and Rationale
We chose the **State-Change Event Stream** approach.
- **Bandwidth Limitations**: Sending raw coordinates every frame across 40 stores would require persistent high-speed network uploads. By processing coordinates on the edge (mapping them using Homography Polygons directly on the camera device) and emitting only state-change events (e.g., entering or exiting a shelf area), we reduce network payload sizes by over **98%**.
- **Database Scaling**: Storing every coordinate frame would flood the API database. The selected schema contains only actionable business events, allowing SQLite to perform sub-millisecond aggregations.

---

## 3. API Architecture Choice (FastAPI vs. Node.js)

### Options Considered
1. **Node.js (Express/Fastify)**: High concurrency, but lacks native scientific packages for data manipulation and pandas-like processing.
2. **Python (FastAPI)**: Direct integration with Pydantic, built-in async loops, and native access to Python CV/data packages.

### AI Recommendation
AI suggested FastAPI as the industry standard for Python microservices due to automatic Swagger documentation and fast serialization.

### Our Choice and Rationale
We chose **FastAPI**.
- **Python Ecosystem Alignment**: The CV pipeline (YOLO, OpenCV, PyTorch) is entirely written in Python. Using FastAPI allows sharing data validation schemas (Pydantic models) between the edge pipeline and the analytics server.
- **Automatic Serialization & Validation**: FastAPI validates client JSON payloads against Pydantic models automatically. If the CV pipeline emits a malformed event, FastAPI rejects it at the gate with a 422 error, protecting the database from corrupt metrics.
