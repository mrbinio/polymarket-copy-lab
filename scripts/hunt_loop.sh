#!/usr/bin/env bash
# Repeat hunt cycles until HUNT_LOOP_MINUTES. Never enables copy.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
MINUTES="${HUNT_LOOP_MINUTES:-330}"
PAUSE="${HUNT_PAUSE_SEC:-600}"
PUBLISH_EVERY="${HUNT_PUBLISH_SEC:-90}"
CYCLE_TIMEOUT="${HUNT_CYCLE_TIMEOUT_SEC:-900}"
END=$((SECONDS + MINUTES * 60))
FLAG="/tmp/copy-lab-hunt-loop-live"
LOCK="/tmp/copy-lab-hunt-publish.lock"
PUB_PID=""

export PYTHONUNBUFFERED=1

mark_pulse() {
  python3 - "$1" <<'PY'
import json, sys
from datetime import datetime, timezone
from pathlib import Path
Path("ops/data").mkdir(parents=True, exist_ok=True)
Path("ops/data/pulse.json").write_text(json.dumps({
    "hunt": sys.argv[1],
    "updated_at": datetime.now(timezone.utc).isoformat(),
}, indent=2) + "\n")
PY
}

publish_data() {
  if [ -z "${PAGES_DEPLOY_TOKEN:-${GH_TOKEN:-${GITHUB_TOKEN:-}}}" ]; then
    echo "skip publish (no token)"
    return 0
  fi
  local mode="${1:-data}"
  if command -v flock >/dev/null 2>&1; then
    (
      flock -w 180 9 || { echo "publish lock timeout"; exit 0; }
      PUBLISH_MODE="$mode" bash "$ROOT/scripts/publish_ops_pages.sh"
    ) 9>"$LOCK"
  else
    PUBLISH_MODE="$mode" bash "$ROOT/scripts/publish_ops_pages.sh"
  fi
}

keep_publishing() {
  while [ -f "$FLAG" ]; do
    sleep "$PUBLISH_EVERY"
    [ -f "$FLAG" ] || break
    if [ -f "$ROOT/ops/data/hunt.json" ]; then
      python3 - <<'PY' || true
import json
from pathlib import Path
d = json.loads(Path("ops/data/hunt.json").read_text())
print("heartbeat", d.get("status"), "checked", d.get("checked"), d.get("updated_at"))
PY
    fi
    publish_data data || echo "heartbeat publish failed"
  done
}

run_hunt() {
  if command -v timeout >/dev/null 2>&1; then
    timeout -k 30 "$CYCLE_TIMEOUT" python3 "$ROOT/scripts/hunt_next.py"
  else
    python3 "$ROOT/scripts/hunt_next.py"
  fi
}

mark_pulse running
: > "$FLAG"
keep_publishing &
PUB_PID=$!
trap 'rm -f "$FLAG"; kill "$PUB_PID" 2>/dev/null || true' EXIT

# Pulse only at t=0. Pushing hunt.json here would republish the checkout snapshot.
publish_data pulse || echo "publish (running pulse) failed"

while [ "$SECONDS" -lt "$END" ]; do
  echo "hunt cycle start SECONDS=$SECONDS"
  run_hunt || echo "hunt cycle failed or timed out, will retry"
  publish_data data || echo "publish failed"
  left=$((END - SECONDS))
  if [ "$left" -le 45 ]; then
    break
  fi
  nap=$PAUSE
  if [ "$left" -lt "$nap" ]; then
    nap=$left
  fi
  echo "pause ${nap}s"
  sleep "$nap"
done

rm -f "$FLAG"
wait "$PUB_PID" 2>/dev/null || true
PUB_PID=""
mark_pulse idle
publish_data data || echo "publish (idle) failed"
echo "hunt loop done"
