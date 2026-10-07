"""SRE Sentinel - stage 3: detects anomalies and keeps an incident log.

Run:   uvicorn app.main:app --reload
Open:  http://localhost:8000/health
       http://localhost:8000/metrics          (Prometheus text format)
       http://localhost:8000/api/latest       (last samples, with detection results)
       http://localhost:8000/api/incidents    (grouped incidents, newest first)
       http://localhost:8000/docs             (interactive API docs - try the ack endpoint there)
"""
import asyncio
import contextlib
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest
from pydantic import BaseModel, Field

from .detector import Detection, LocalResidualDetector
from .incidents import IncidentStore, IncidentTracker
from .simulator import MetricSimulator, Sample

INTERVAL = float(os.getenv("SENTINEL_INTERVAL", "1.0"))   # seconds between samples
HISTORY = int(os.getenv("SENTINEL_HISTORY", "300"))       # samples kept in memory
DB_PATH = os.getenv("SENTINEL_DB", "incidents.db")        # ":memory:" = no file (used by tests)

_seed = os.getenv("SENTINEL_SEED")
simulator = MetricSimulator(seed=int(_seed) if _seed else None)
detector = LocalResidualDetector()
store = IncidentStore(DB_PATH)
tracker = IncidentTracker(store)


@dataclass(frozen=True)
class Point:
    sample: Sample
    detection: Detection


history = deque(maxlen=HISTORY)

# Prometheus metrics. Names follow the convention <app>_<what>_<unit>.
cpu_gauge = Gauge("sre_cpu_percent", "Simulated CPU utilisation (%)")
score_gauge = Gauge("sre_anomaly_score", "Latest robust z-score of the CPU metric")
open_gauge = Gauge("sre_open_incidents", "Incidents currently open (0 or 1)")
samples_total = Counter("sre_samples_total", "Samples produced since start")
detected_total = Counter("sre_detected_anomalies_total", "Samples flagged as anomalous by the detector")
incidents_total = Counter("sre_incidents_opened_total", "Incidents opened since start")
injected_total = Counter(
    "sre_injected_anomalies_total",
    "Anomalous samples injected by the simulator (ground truth, for evaluating the detector)",
)


def process(sample: Sample) -> Point:
    """One step of the pipeline: detect, track incidents, record, update metrics."""
    detection = detector.update(sample.value)
    point = Point(sample, detection)
    history.append(point)

    if tracker.observe(sample.ts, sample.value, detection) == "opened":
        incidents_total.inc()

    cpu_gauge.set(sample.value)
    score_gauge.set(detection.score)
    open_gauge.set(0 if tracker.open_id is None else 1)
    samples_total.inc()
    if detection.is_anomaly:
        detected_total.inc()
    if sample.anomaly:
        injected_total.inc()
    return point


async def sampling_loop():
    while True:
        process(simulator.next())
        await asyncio.sleep(INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(sampling_loop())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="SRE Sentinel", version="0.3.0", lifespan=lifespan)


class AckRequest(BaseModel):
    note: str = Field(min_length=1, max_length=500, description="What was checked or done")


@app.get("/health")
def health():
    return {"status": "ok", "version": app.version}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/latest")
def latest(n: int = Query(60, ge=1, le=HISTORY)):
    return [
        {
            "ts": p.sample.ts,
            "value": round(p.sample.value, 2),
            "injected": p.sample.anomaly,          # ground truth from the simulator
            "detected": p.detection.is_anomaly,    # what the detector decided
            "score": round(p.detection.score, 2),
        }
        for p in list(history)[-n:]
    ]


@app.get("/api/incidents")
def list_incidents(limit: int = Query(20, ge=1, le=200)):
    return store.recent(limit)


@app.get("/api/incidents/{incident_id}")
def get_incident(incident_id: int):
    incident = store.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    return {**incident, "audit": store.audit(incident_id=incident_id)}


@app.post("/api/incidents/{incident_id}/ack")
def ack_incident(incident_id: int, body: AckRequest):
    result = store.ack(incident_id, body.note, time.time())
    if result == "not_found":
        raise HTTPException(status_code=404, detail="incident not found")
    if result == "already_acked":
        raise HTTPException(status_code=409, detail="incident already acknowledged")
    return store.get(incident_id)


@app.get("/api/audit")
def audit_log(limit: int = Query(50, ge=1, le=500)):
    return store.audit(limit=limit)
