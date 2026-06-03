# DESIGN.md - Store Intelligence System Architecture

This document provides a comprehensive technical overview of the Store Intelligence analytics system, illustrating the design patterns, processing flows, and decisions that shape the architecture.

---

## 1. System Architecture Diagram

The system employs a decoupled, event-driven design divided into two primary subsystems: the **Edge Computer Vision Pipeline** and the **Centralized Analytics API**.

```mermaid
graph TD
    subgraph Edge CCTV Pipeline (CV Layer)
        C1[Entry Camera Feed] --> Y1[YOLOv8 Detector]
        C2[Floor Camera Feed] --> Y2[YOLOv8 Detector]
        C3[Billing Camera Feed] --> Y3[YOLOv8 Detector]
        
        Y1 --> T1[ByteTrack Tracker]
        Y2 --> T2[ByteTrack Tracker]
        Y3 --> T3[ByteTrack Tracker]
        
        T1 & T2 & T3 --> R1[OSNet Re-ID Manager]
        R1 --> H1[Homography Coordinate Mapper]
        H1 --> E1[Event Emitter]
    end

    subgraph Centralized Cloud/Server (API Layer)
        E1 -->|HTTP POST JSON Batch| A1[FastAPI REST Server]
        A1 -->|Query / Ingest| D1[(SQLite Store Database)]
        P1[POS transaction CSV] -->|Startup Seed| D1
    end

    subgraph Analytics & Consumption (Dashboard Layer)
        A1 -->|GET /metrics| Dash[Real-time Live Dashboard]
        A1 -->|GET /heatmap| Dash
        A1 -->|GET /funnel| Dash
        A1 -->|GET /anomalies| Dash
    end
```

---

## 2. Architectural Subsystems

### 2.1 Edge CV Ingestion Subsystem
- **Object Detection & Tracking**: Processes raw video frames on the edge using a customized YOLOv8 and ByteTrack pipeline.
- **Visitor Re-ID**: Extracts feature embeddings using a pre-trained OSNet neural network to build a persistent, cross-camera visitor profile (`visitor_id`).
- **Homography Coordinate Mapping**: Performs projective geometry to translate camera pixel space coordinates $(x,y)$ to standard 2D Cartesian coordinates $(X,Y)$ on the store floor layout drawing.

### 2.2 Central REST API Subsystem
- **FastAPI Core**: A lightweight, high-performance async-capable web API. Handles event streams and metrics queries.
- **Relational Storage**: Stores event history and transaction structures in SQLite. Database indexes are deployed on `visitor_id`, `store_id`, `timestamp`, and `event_type` to guarantee sub-millisecond execution for analytical aggregation queries.
- **POS Correlation Engine**: Performs time-windowed joins on transactions to trace customer checkouts without collecting identity data.

---

## 3. Data Processing Flows

### 3.1 Visitor Entry & Shelf Dwell Flow
1. A visitor walks through the entry door: `CAM_ENTRY_01` detects crossing and emits an `ENTRY` event.
2. The visitor moves to the cosmetic brand stands: `CAM_FLOOR_01` detects their coordinates, maps them to the `MINIMALIST` polygon coordinates, and emits `ZONE_ENTER`.
3. If they stand there for over 30 seconds, `ZONE_DWELL` is emitted repeatedly, updating cumulative time.
4. When they move away, a `ZONE_EXIT` event logs their total duration in that zone.

### 3.2 Transaction Correlation Flow
To evaluate purchase conversions without collecting personal shopper data:
1. When a transaction at time $T$ is registered, the API queries all visitors who joined the billing queue (`CASH_COUNTER` zone) in the 5 minutes prior: $[T-5\text{ mins}, T]$.
2. The matching algorithm assigns the transaction to the visitor who has been in the queue zone the longest.
3. The database updates the transaction record with `correlated_visitor_id`, linking the checkout path to the store journey.

---

## 4. AI-Assisted Decisions

During design and construction, AI code tools (Gemini / Copilot) suggested several approaches. Below are 3 key decisions where AI played a role, and why we agreed or overrode the advice:

### 4.1 SQLite for Storage
- **AI Suggestion**: Use PostgreSQL inside Docker Compose with SQLAlchemy ORM.
- **Our Decision**: **Overrode.** We chose a lightweight, native SQLite connection manager. For a localized store edge intelligence system processing 20-minute video segments, setting up a heavy PostgreSQL container introduces database startup lag and complex migrations. SQLite stores data inside a single flat file, handles concurrent reads efficiently, and keeps container footprint extremely small (<100MB).

### 4.2 Re-ID Embedding Gallery Management
- **AI Suggestion**: Save visitor embeddings indefinitely in database tables.
- **Our Decision**: **Overrode.** We chose a rolling in-memory Python dictionary cache capped at 5 recent embeddings per visitor. Saving high-dimensional embeddings indefinitely in SQLite slows queries and risks "feature drift" (where clothing changes or lighting shifts cause a visitor's profile to drift and match other people). Keeping a rolling memory cache limits matching latency to $O(N)$ and stabilizes matching accuracy.

### 4.3 Behavioral Staff Detection
- **AI Suggestion**: Use a separate VLM model (like Gemini Flash) to detect staff uniforms.
- **Our Decision**: **Agreed.** While we implemented standard behavioral classification (tracking visitors whose session exceeds 60 minutes or unique zone count > 12), using visual clothing color classification (uniform detection) is an excellent enhancement. We scaffolded the `is_staff` schema flag so that both behavioral and visual class classifiers can set this attribute.
