# SRE Sentinel

An anomaly-detection and predictive-alerting platform for Kubernetes
infrastructure, built stage by stage on top of a full DevOps toolchain
(Docker, CI/CD, IaC, GitOps, Prometheus/Grafana).

![CI](https://github.com/islemazz/sre-sentinel/actions/workflows/ci.yml/badge.svg)

**Status: Stage 4 - containerised, with CI that tests, builds, smoke-tests and publishes the image.**

## Roadmap

| Stage | What it adds | Tools | Status |
| --- | --- | --- | --- |
| 1 | Metric simulator with ground-truth anomaly labels, `/metrics` endpoint | Python, FastAPI, Prometheus client | done |
| 2 | Anomaly detector + evaluation against ground truth | Python | done |
| 3 | Incident log: group consecutive anomalies into incidents, persist them, acknowledge them, append-only audit log | SQLite | done |
| 4 | Docker image (non-root, healthcheck), compose stack, CI that tests, builds, smoke-tests and publishes the image | Docker, GitHub Actions | done |
| 5 | Second pipeline and code-quality gate | Jenkins, SonarQube | next |
| 6 | Predictive alert: forecast a breach before it happens | Python (+ scikit-learn if time allows) | |
| 7 | Run on a real cluster, GitOps deployment | Kubernetes, ArgoCD | |
| 8 | Real metrics + dashboards | Prometheus, Grafana | |
| 9 | Reproducible infrastructure | Terraform, Ansible | |

## How the detector works

For every new value `x`:

1. **Local baseline** - the median of the last 9 samples. A short window follows slow trends, so a normal rise or fall leaves only a small residual `x - baseline`.
2. **Robust score** - compare that residual with the residuals of the last 120 samples using median/MAD (not mean/std, which a spike would distort): `score = 0.6745 * (residual - median) / MAD`.
3. **Decision** - flag the sample if `|score| >= 3.5`. Nothing is flagged during the warm-up.

## Incidents and the audit trail

A spike lasts 1-3 samples; paging someone three times for one spike is noise. So consecutive flagged samples are grouped into **one incident**:

- **open** on the first flagged sample, **extend** on each further one (peak value and score are tracked), **close** after 3 consecutive normal samples. Two spikes separated by fewer than 3 normal samples are one incident.
- Everything is stored in SQLite (`incidents.db`, path set by `SENTINEL_DB`).
- A human can **acknowledge** an incident with a note (what was checked or done). Acknowledging twice is refused (409) so the first note can't be overwritten.
- Every change (`opened`, `closed`, `acknowledged`) is appended to `audit_log`. The database itself rejects `UPDATE` and `DELETE` on that table (triggers), so the history can't be rewritten afterwards.
- An incident still open when the service starts belongs to a previous run: it is closed, with a "closed on service restart" audit entry.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/incidents?limit=20` | incidents, newest first |
| `GET /api/incidents/{id}` | one incident with its audit trail |
| `POST /api/incidents/{id}/ack` | body `{"note": "..."}` - acknowledge (404 unknown, 409 already acknowledged) |
| `GET /api/audit?limit=50` | latest audit entries across all incidents |

The easiest way to try the POST is the interactive docs at http://localhost:8000/docs.

## How well does it work?

The simulator injects spikes of 1-3 samples and labels them, so the detector can be scored instead of eyeballed.
`python -m app.evaluate` runs 20 seeds x 5000 samples on **held-out seeds** (not used when choosing the defaults):

| Spike size (CPU points) | Precision | Recall | F1 | Spikes caught | False alarms |
| --- | --- | --- | --- | --- | --- |
| 10 | 98.0 % | 37.8 % | 0.55 | 505 / 990 (51 %) | 15 |
| 15 | 98.1 % | 85.8 % | 0.92 | 937 / 990 (95 %) | 33 |
| 20 | 97.6 % | 98.4 % | 0.98 | 983 / 990 (99 %) | 48 |
| 35 | 97.4 % | 99.2 % | 0.98 | 984 / 990 (99 %) | 52 |

(over 100,000 samples per row). Spikes much smaller than the normal noise are genuinely hard to separate from it: that is the limit of this method, not a bug.

**First attempt, for the record:** comparing each value with the median of a long 60-sample window gave about 1,360 false alarms per 100,000 samples and F1 0.65 at best. The slow wave in the signal dragged the baseline and widened the scale. Switching to a short local baseline plus a separate robust scale fixed it.

**Honest limits:** the signal is synthetic (a sine wave, noise and spikes that I designed), and the parameters were tuned on the same kind of signal. Real cluster metrics will behave differently; Stage 8 (real Prometheus metrics) is where that gets tested.

## Run it

```bash
python -m venv .venv
# Windows:   .venv\Scripts\activate
# Linux/Mac: source .venv/bin/activate
python -m pip install -r requirements-dev.txt     # runtime + test dependencies
uvicorn app.main:app --reload
```

Then open:

- http://localhost:8000/health
- http://localhost:8000/metrics - Prometheus text format
- http://localhost:8000/api/latest - last samples as JSON: `injected` is the ground truth, `detected` is the detector's decision, `score` is its robust z-score
- http://localhost:8000/docs - auto-generated API docs

To watch detection happen quickly (about one spike every few seconds):

```bash
# PowerShell
$env:SENTINEL_INTERVAL="0.05"; uvicorn app.main:app
# Linux/Mac
SENTINEL_INTERVAL=0.05 uvicorn app.main:app
```

## Run it in Docker

```bash
docker compose up --build
# open http://localhost:8000/docs
```

What the image does (and why):

- **Runtime dependencies only** (`requirements.txt`); pytest and friends live in `requirements-dev.txt`, so the image stays small.
- **Runs as an unprivileged user** (uid 10001), not root. A compromised app then has far less power inside the container.
- **`HEALTHCHECK`** calls `/health` with Python (the slim image has no curl); `docker ps` shows `healthy` / `unhealthy`.
- **Incident log in a volume** (`/data`): `docker compose down` keeps your incidents, `docker compose down -v` deletes them.
- **Compose hardening**: read-only root filesystem, all Linux capabilities dropped, `no-new-privileges`.

## Continuous integration (GitHub Actions)

`.github/workflows/ci.yml` runs on every push and pull request:

1. **test** - installs dependencies, runs `pytest`, prints the detector quality table.
2. **docker** (only if tests pass) - builds the image, starts the container, runs `scripts/smoke_test.sh` against it (health, live metric samples, incident API), and fails if the container runs as root.
3. **publish** (only on `main`) - pushes the image to GitHub Container Registry as `ghcr.io/islemazz/sre-sentinel` (tags `latest` and the commit SHA).

`scripts/smoke_test.sh` is a plain script on purpose: Stage 5's Jenkins pipeline reuses it.

## Test

```bash
pytest -q
python -m app.evaluate
```

## Configuration (environment variables)

| Variable | Default | Meaning |
| --- | --- | --- |
| `SENTINEL_INTERVAL` | `1.0` | seconds between samples |
| `SENTINEL_HISTORY` | `300` | samples kept in memory |
| `SENTINEL_SEED` | unset | set an integer for a reproducible stream |
| `SENTINEL_DB` | `incidents.db` | SQLite file for incidents and audit log (`:memory:` for none) |

## Prometheus metrics

| Metric | Type | Meaning |
| --- | --- | --- |
| `sre_cpu_percent` | gauge | simulated CPU utilisation |
| `sre_anomaly_score` | gauge | latest robust z-score |
| `sre_open_incidents` | gauge | 1 while an incident is open, else 0 |
| `sre_incidents_opened_total` | counter | incidents opened since start |
| `sre_samples_total` | counter | samples produced |
| `sre_detected_anomalies_total` | counter | samples the detector flagged |
| `sre_injected_anomalies_total` | counter | samples the simulator made anomalous (ground truth) |

## Layout

```
app/simulator.py   synthetic CPU signal + injected spikes (ground truth)
app/detector.py    streaming detector (local baseline + robust residual score)
app/evaluate.py    scores the detector against the ground truth
app/incidents.py   incident grouping (tracker) + SQLite store and audit log
app/main.py        FastAPI app, background loop, /metrics, incident API
tests/             unit, API and detector-quality regression tests
scripts/           smoke_test.sh - checks a running instance (used by CI)
Dockerfile         the image (non-root, healthcheck)
docker-compose.yml local stack with a data volume and hardening
.github/workflows/ CI pipeline
```
