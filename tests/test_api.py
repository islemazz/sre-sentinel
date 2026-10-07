import time

from fastapi.testclient import TestClient

from app.main import app, history, process, tracker
from app.simulator import Sample


def drive(values, start_ts):
    """Push values through the real pipeline without the background loop."""
    for i, v in enumerate(values):
        process(Sample(ts=start_ts + i, value=v, anomaly=False))


def make_incident(start_ts=1000.0):
    """160 calm samples, one big spike, then calm again so the incident closes."""
    drive([50.0 + (i % 3) * 0.5 for i in range(160)], start_ts)
    drive([95.0], start_ts + 160)
    drive([50.0] * 4, start_ts + 161)


def test_health():
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


def test_metrics_exposes_our_gauges():
    with TestClient(app) as client:
        time.sleep(1.2)  # let the background loop produce a sample
        body = client.get("/metrics").text
        for name in ("sre_cpu_percent", "sre_anomaly_score", "sre_samples_total",
                     "sre_detected_anomalies_total", "sre_injected_anomalies_total",
                     "sre_incidents_opened_total", "sre_open_incidents"):
            assert name in body


def test_latest_returns_samples_with_detection_fields():
    with TestClient(app) as client:
        time.sleep(1.2)
        data = client.get("/api/latest?n=5").json()
        assert 1 <= len(data) <= 5
        assert {"ts", "value", "injected", "detected", "score"} <= set(data[0])


def test_pipeline_flags_a_big_spike_end_to_end():
    history.clear()
    drive([50.0 + (i % 3) * 0.5 for i in range(160)], 0.0)
    point = process(Sample(ts=999.0, value=95.0, anomaly=True))
    assert point.detection.is_anomaly
    assert history[-1] is point
    drive([50.0] * 4, 1000.0)      # let the incident close so other tests start clean
    assert tracker.open_id is None


# The tests below use TestClient WITHOUT `with`, so no background loop runs
# and the only samples are the ones we feed in.

def test_incident_appears_in_api_with_audit_trail():
    make_incident()
    client = TestClient(app)
    newest = client.get("/api/incidents?limit=1").json()[0]
    assert newest["status"] == "closed" and newest["direction"] == "up"
    assert newest["peak_value"] == 95.0

    detail = client.get(f"/api/incidents/{newest['id']}").json()
    assert [e["event"] for e in detail["audit"]] == ["opened", "closed"]


def test_ack_flow_and_error_codes():
    make_incident(start_ts=5000.0)
    client = TestClient(app)
    incident_id = client.get("/api/incidents?limit=1").json()[0]["id"]

    r = client.post(f"/api/incidents/{incident_id}/ack", json={"note": "planned load test"})
    assert r.status_code == 200 and r.json()["ack_note"] == "planned load test"

    assert client.post(f"/api/incidents/{incident_id}/ack", json={"note": "again"}).status_code == 409
    assert client.post("/api/incidents/999999/ack", json={"note": "x"}).status_code == 404
    assert client.post(f"/api/incidents/{incident_id}/ack", json={"note": ""}).status_code == 422
    assert client.get("/api/incidents/999999").status_code == 404

    events = [e["event"] for e in client.get(f"/api/incidents/{incident_id}").json()["audit"]]
    assert events == ["opened", "closed", "acknowledged"]
    assert client.get("/api/audit?limit=3").json()[0]["event"] == "acknowledged"
