#!/usr/bin/env bash
# Smoke test: is a running SRE Sentinel actually alive and doing its job?
#
#   bash scripts/smoke_test.sh [base_url]        (default: http://localhost:8000)
#
# Used by CI right after the container starts (and reusable from Jenkins later).
# Exit code 0 = healthy, non-zero = something is wrong.
set -euo pipefail

BASE="${1:-http://localhost:8000}"

fail() { echo "SMOKE TEST FAILED: $1" >&2; exit 1; }

echo "Waiting for $BASE/health ..."
up=0
for _ in $(seq 1 30); do
  if curl -fsS "$BASE/health" >/dev/null 2>&1; then up=1; break; fi
  sleep 1
done
[ "$up" -eq 1 ] || fail "/health never answered within 30 s"

curl -fsS "$BASE/health" | grep -q '"status":"ok"' || fail "/health did not report status ok"

echo "Waiting for the first metric sample ..."
sampled=0
for _ in $(seq 1 15); do
  if curl -fsS "$BASE/metrics" | grep -Eq '^sre_samples_total [1-9]'; then sampled=1; break; fi
  sleep 1
done
[ "$sampled" -eq 1 ] || fail "/metrics shows no samples: the sampling loop is not running"

curl -fsS "$BASE/api/incidents" | grep -q '^\[' || fail "/api/incidents did not return a JSON list"

echo "SMOKE TEST PASSED"
