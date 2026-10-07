import sqlite3

import pytest

from app.detector import Detection
from app.incidents import IncidentStore, IncidentTracker

FLAG = Detection(True, 8.0, 50.0)
QUIET = Detection(False, 0.1, 50.0)


def feed(tracker, pattern, start_ts=0.0):
    """pattern like 'xxo..': x = flagged, anything else = normal. Returns list of events."""
    events = []
    for i, ch in enumerate(pattern):
        det = FLAG if ch == "x" else QUIET
        events.append(tracker.observe(start_ts + i, 90.0 if ch == "x" else 50.0, det))
    return events


@pytest.fixture
def store():
    return IncidentStore(":memory:")


def test_consecutive_flags_become_one_incident(store):
    tracker = IncidentTracker(store, close_after=3)
    events = feed(tracker, "..xxx...")
    assert events.count("opened") == 1 and events.count("closed") == 1
    incidents = store.recent()
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc["status"] == "closed"
    assert inc["samples"] == 3
    assert (inc["start_ts"], inc["end_ts"]) == (2.0, 4.0)     # first/last FLAGGED sample


def test_short_gap_is_merged_long_gap_splits(store):
    tracker = IncidentTracker(store, close_after=3)
    feed(tracker, "x..x" + "." * 5)          # gap of 2 normal samples -> same incident
    assert len(store.recent()) == 1

    feed(tracker, "x...x...", start_ts=100)  # gap of 3 normal samples -> two incidents
    assert len(store.recent()) == 3


def test_peak_tracks_the_largest_score(store):
    tracker = IncidentTracker(store)
    tracker.observe(0, 70.0, Detection(True, 4.0, 50.0))
    tracker.observe(1, 95.0, Detection(True, 11.0, 50.0))
    tracker.observe(2, 65.0, Detection(True, 3.6, 50.0))
    inc = store.recent()[0]
    assert inc["peak_value"] == 95.0 and inc["peak_score"] == 11.0 and inc["direction"] == "up"


def test_downward_anomaly_has_direction_down(store):
    tracker = IncidentTracker(store)
    tracker.observe(0, 5.0, Detection(True, -9.0, 50.0))
    assert store.recent()[0]["direction"] == "down"


def test_audit_log_records_lifecycle_in_order(store):
    tracker = IncidentTracker(store, close_after=2)
    feed(tracker, "xx..")
    incident_id = store.recent()[0]["id"]
    assert store.ack(incident_id, "known batch job", ts=10.0) == "acked"
    events = [e["event"] for e in store.audit(incident_id=incident_id)]
    assert events == ["opened", "closed", "acknowledged"]


def test_ack_rules(store):
    tracker = IncidentTracker(store, close_after=1)
    feed(tracker, "x.")
    incident_id = store.recent()[0]["id"]
    assert store.ack(incident_id, "first", ts=1.0) == "acked"
    assert store.ack(incident_id, "second", ts=2.0) == "already_acked"
    assert store.ack(9999, "nope", ts=3.0) == "not_found"
    assert store.get(incident_id)["ack_note"] == "first"      # first ack is not overwritten


def test_audit_log_cannot_be_modified_or_deleted(store):
    IncidentTracker(store).observe(0, 90.0, FLAG)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store._db.execute("UPDATE audit_log SET detail = 'tampered'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store._db.execute("DELETE FROM audit_log")


def test_incident_left_open_by_a_previous_run_is_closed_on_restart(tmp_path):
    path = str(tmp_path / "incidents.db")
    first = IncidentStore(path)
    incident_id = first.open_incident(1.0, 90.0, 8.0)
    assert first.get(incident_id)["status"] == "open"

    second = IncidentStore(path)               # "service restarted"
    assert second.get(incident_id)["status"] == "closed"
    last = second.audit(incident_id=incident_id)[-1]
    assert last["event"] == "closed" and "restart" in last["detail"]
