#!/usr/bin/env bash
# Repeat hunt cycles until HUNT_LOOP_MINUTES. Never enables copy.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
MINUTES="${HUNT_LOOP_MINUTES:-330}"
PAUSE="${HUNT_PAUSE_SEC:-600}"
END=$((SECONDS + MINUTES * 60))

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
  PUBLISH_MODE=data bash "$ROOT/scripts/publish_ops_pages.sh"
}

mark_pulse running
publish_data || echo "publish (running) failed"

while [ "$SECONDS" -lt "$END" ]; do
  echo "hunt cycle start SECONDS=$SECONDS"
  python3 "$ROOT/scripts/hunt_next.py" || echo "hunt cycle failed, will retry"
  python3 "$ROOT/scripts/claude_solutions.py" || echo "solutions skipped"
  python3 "$ROOT/scripts/grok_x_filter.py" || echo "x filter skipped"
  publish_data || echo "publish failed"
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

mark_pulse idle
publish_data || echo "publish (idle) failed"
echo "hunt loop done"
