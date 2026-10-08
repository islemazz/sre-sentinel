# SRE Sentinel

An anomaly-detection and predictive-alerting platform for Kubernetes
infrastructure, built stage by stage on top of a full DevOps toolchain
(Docker, CI/CD, IaC, GitOps, Prometheus/Grafana).

![CI](https://github.com/islemazz/sre-sentinel/actions/workflows/ci.yml/badge.svg)

**Status: Stage 6 - predictive alert on top of a security-gated Jenkins pipeline with SonarQube.**

## Roadmap

| Stage | What it adds | Tools | Status |
| --- | --- | --- | --- |
| 1 | Metric simulator with ground-truth anomaly labels, `/metrics` endpoint | Python, FastAPI, Prometheus client | done |
| 2 | Anomaly detector + evaluation against ground truth | Python | done |
| 3 | Incident log: group consecutive anomalies into incidents, persist them, acknowledge them, append-only audit log | SQLite | done |
| 4 | Docker image (non-root, healthcheck), compose stack, CI that tests, builds, smoke-tests and publishes the image | Docker, GitHub Actions | done |
| 5 | Second pipeline with a code-quality gate and security scans (dependencies, secrets, image) | Jenkins, SonarQube, pip-audit, gitleaks, Trivy | done |
| 6 | Predictive alert: forecast a breach before it happens | Python | done |
| 7 | Run on a real cluster, GitOps deployment: CI updates the image tag in a separate repo and ArgoCD syncs it | Kubernetes (kind), ArgoCD, Kustomize | done |
| 8 | Real metrics, alert rules and dashboards, all deployed through Git | Prometheus, Alertmanager, Grafana (kube-prometheus-stack) | done |
| 9 | Reproducible infrastructure: cluster and ArgoCD built by code, secrets and smoke test automated | Terraform, Ansible | done |

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

## Predictive alert (will memory run out?)

The detector answers "is something wrong right now?". The forecaster answers a different question: **"if nothing changes, when will it go wrong?"** It watches a memory metric that slowly grows, like a leak, and warns before the limit is reached.

How it works (`app/forecast.py`):

1. Fit a straight line through the last 60 samples (least squares).
2. If the line goes up, extend it to the limit (90 %) to get the **ETA**: `eta = (limit - value_on_the_line_now) / slope`.
3. Raise the alert only if **all three** hold: the slope is meaningful (`>= 0.02` per sample), the line really explains the data (`r2 >= 0.5`, so noise does not trigger it), and the ETA is inside the horizon (120 samples).

Why a line and not machine learning? There is nothing to train on, it runs in microseconds, and an alert can be explained exactly. A model would only be worth it for signals a line cannot describe.

Measured on the leak simulator (ground truth: the memory crosses 90 % at a known moment):

| Check | Result |
| --- | --- |
| Leaks caught before the limit | 100 / 100 cycles |
| Warning time | 88 to 145 samples before the limit (median 111) |
| False alerts far from the limit | 0 in 60,000 samples |
| False alerts on a flat noisy signal | 0 in 100,000 samples |

**Honest limit:** on the CPU wave (a daily sine pattern) a straight line cannot tell a normal rise from a leak, and it raised false alerts in 17 % of samples. So the forecaster is only applied to the memory metric, not to CPU.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/forecast` | memory %, fitted slope, fit quality (`fit_r2`), seconds until the limit (`eta_seconds`, `null` if none) and `alert` |

It also exports `sre_memory_percent`, `sre_memory_breach_predicted` and `sre_memory_breach_eta_seconds` for Prometheus (Stage 8).

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
- http://localhost:8000/api/forecast - memory trend and predicted time until the limit
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

`scripts/smoke_test.sh` is a plain script on purpose: the Jenkins pipeline (below) reuses it.

## Second pipeline (Jenkins + SonarQube, DevSecOps)

`Jenkinsfile` is the pipeline as code. It runs on a Jenkins that I host myself with `ci/docker-compose.yml`:

```bash
cd ci
docker compose -p sre-sentinel-ci up -d --build
# Jenkins:   http://localhost:9080
# SonarQube: http://localhost:9001
```

Stages, in order. Any failing stage stops the pipeline:

1. **Test** - pytest with coverage (`coverage.xml`) and a JUnit report.
2. **Dependency audit** - `pip-audit` checks `requirements.txt` against known vulnerabilities.
3. **Secret scan** - `gitleaks` scans the whole git history. The binary is downloaded with a pinned version and its SHA-256 checksum is verified.
4. **SonarQube analysis + Quality Gate** - the build waits for SonarQube's verdict (no new issues, coverage and duplication on new code) and fails if the gate fails.
5. **Build image** - `docker build`.
6. **Image scan** - Trivy reports HIGH and CRITICAL findings and **fails the build on any fixable CRITICAL**.
7. **Smoke test** - the built image runs read-only with all capabilities dropped; `scripts/smoke_test.sh` checks it, and the build fails if the container runs as root.

Security choices (and why):

- **Private Docker engine** (`docker:dind`): Jenkins builds and runs containers in its own engine, so a pipeline can never see or touch other containers on the same machine. The engine's port is only reachable inside the compose network.
- **Ports bound to `127.0.0.1`**: Jenkins and SonarQube are reachable only from this computer.
- **No secrets in the repository**: the SonarQube token lives in Jenkins credentials and is injected only during the analysis.
- **A security check that cannot run fails the build** instead of passing silently (for example, when the vulnerability database cannot be downloaded).

What the pipeline caught while I built this: a SonarQube Quality Gate failure on my own new test code (a composite assertion), which blocked the build until I fixed it; and a Trivy run that failed because the vulnerability database download timed out, which is the intended behaviour for a scan that cannot run.

Known limits, on purpose: the Jenkins is local, so GitHub cannot trigger it with a webhook and builds are started by hand. SonarQube Community uses its embedded database (evaluation only) and does not scan for injection flaws such as SQL injection or XSS, which is why the other layers (dependencies, secrets, image) exist.

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
| `sre_memory_percent` | gauge | simulated memory utilisation |
| `sre_memory_breach_predicted` | gauge | 1 while memory is predicted to reach its limit soon, else 0 |
| `sre_memory_breach_eta_seconds` | gauge | predicted seconds until the limit (-1 = no breach predicted) |

## Layout

```
app/simulator.py   synthetic CPU signal + injected spikes, and a memory leak signal
app/detector.py    streaming detector (local baseline + robust residual score)
app/forecast.py    predictive alert: linear trend forecast of the time to a limit
app/evaluate.py    scores the detector against the ground truth
app/incidents.py   incident grouping (tracker) + SQLite store and audit log
app/main.py        FastAPI app, background loop, /metrics, incident API
tests/             unit, API and detector-quality regression tests
scripts/           smoke_test.sh - checks a running instance (used by CI)
Jenkinsfile        the Jenkins pipeline (test, audits, SonarQube, scans, smoke test)
ci/                Jenkins image + compose stack (Jenkins, SonarQube, private Docker engine)
Dockerfile         the image (non-root, healthcheck)
docker-compose.yml local stack with a data volume and hardening
.github/workflows/ CI pipeline (GitHub Actions)
```

## Rebuild the platform from scratch

Stage 9 turns the manual steps into code. Prerequisites: Docker, kind, kubectl, Terraform.

```powershell
# 1. Cluster + ArgoCD + app registration (a second cluster, sre-sentinel-tf, port 8089)
cd infra/terraform
terraform init
terraform apply

# 2. Secrets and smoke test, in a container (needs the cluster from step 1)
cd ../..
docker build -t sre-sentinel-ansible infra/ansible
kind get kubeconfig --name sre-sentinel-tf --internal | Set-Content infra/ansible/kubeconfig
docker run --rm --network kind -e ANSIBLE_CONFIG=/work/ansible.cfg -v "${PWD}/infra/ansible:/work" -w /work sre-sentinel-ansible ansible-playbook bootstrap.yml

# 3. Tear down
cd infra/terraform
terraform destroy
```

Terraform drives the `kind` CLI instead of a community provider, so it runs on machines where unsigned plugins are blocked. The GitOps repo (`sre-sentinel-gitops`) stays the source of truth for everything inside the cluster.