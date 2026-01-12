#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

RUN_SECONDS="${RUN_SECONDS:-20}"
LOG_DIR="${LOG_DIR:-logs/sim}"
METRICS_SAMPLE_DELAY="${METRICS_SAMPLE_DELAY:-3}"
PREBUILD="${PREBUILD:-1}"

mkdir -p "$LOG_DIR"

cleanup() {
  if [[ -f "$LOG_DIR/neuroplc.pid" ]]; then
    kill "$(cat "$LOG_DIR/neuroplc.pid")" >/dev/null 2>&1 || true
    rm -f "$LOG_DIR/neuroplc.pid"
  fi
  if [[ -f "$LOG_DIR/cortex.pid" ]]; then
    kill "$(cat "$LOG_DIR/cortex.pid")" >/dev/null 2>&1 || true
    rm -f "$LOG_DIR/cortex.pid"
  fi
}
trap cleanup EXIT

wait_for_url() {
  local url="$1"
  local timeout="${2:-60}"
  local start
  start="$(date +%s)"
  while true; do
    if curl -sf "$url" >/dev/null 2>&1; then
      return 0
    fi
    if [[ $(( $(date +%s) - start )) -ge "$timeout" ]]; then
      return 1
    fi
    sleep 1
  done
}

# Ensure ports are free from previous runs.
docker compose -f docker/compose.simulation.yml down -v >/dev/null 2>&1 || true

docker compose -f docker/compose.simulation.yml up -d --build

if [[ "$PREBUILD" == "1" ]]; then
  cargo build --release --features opcua --bin neuro-plc
fi

RUST_LOG="${RUST_LOG:-info,neuro_io=trace}" \
  cargo run --release --features opcua --bin neuro-plc -- \
  --metrics-addr 0.0.0.0:9100 \
  --audit-log "$LOG_DIR/audit.jsonl" \
  --modbus 127.0.0.1:5020 \
  --run-seconds "$RUN_SECONDS" \
  > "$LOG_DIR/neuroplc.log" 2>&1 &

echo $! > "$LOG_DIR/neuroplc.pid"

BASYX_URL="${BASYX_URL:-http://localhost:8081}" \
NEUROPLC_SEND_HELLO=1 \
PYTHONUNBUFFERED=1 \
  python3 python-cortex/run_supervisor.py \
  > "$LOG_DIR/cortex.log" 2>&1 &

echo $! > "$LOG_DIR/cortex.pid"

sleep 3

if wait_for_url "http://localhost:9100/metrics" 60; then
  sleep "$METRICS_SAMPLE_DELAY"
  curl -s http://localhost:9100/metrics > "$LOG_DIR/metrics.prom" || true
fi

if [[ "$RUN_SECONDS" -gt 0 ]]; then
  sleep "$RUN_SECONDS"
fi

if wait_for_url "http://localhost:9100/metrics" 5; then
  curl -s http://localhost:9100/metrics > "$LOG_DIR/metrics.prom.final" || true
fi

python3 - <<'PY'
import base64, json, urllib.request
base_url = 'http://localhost:8081'

aas_id = 'urn:neuroplc:aas:motor:001'
operational_id = 'urn:neuroplc:sm:operational-data:001'
ai_id = 'urn:neuroplc:sm:ai-recommendation:001'

def b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode()

def fetch(path: str) -> str:
    url = base_url + path
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.read().decode('utf-8')

def safe_fetch(path: str) -> str:
    try:
        return fetch(path)
    except Exception as exc:
        return json.dumps({'error': str(exc), 'path': path})

status = {
    'aas': json.loads(safe_fetch(f'/shells/{b64(aas_id)}')),
    'operational_submodel': json.loads(safe_fetch(f'/submodels/{b64(operational_id)}')),
    'ai_submodel': json.loads(safe_fetch(f'/submodels/{b64(ai_id)}')),
}

with open('logs/sim/basyx_status.json', 'w') as f:
    json.dump(status, f, indent=2)
PY

cleanup

docker compose -f docker/compose.simulation.yml down -v
