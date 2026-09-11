#!/bin/bash
# Publish to mrbinio/copy-lab-ops (GitHub Pages).
# PUBLISH_MODE=data  → only hunt/pulse/snapshot JSON (do not overwrite the UI).
# PUBLISH_MODE=all   → full ops/ site (default).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TOKEN="${PAGES_DEPLOY_TOKEN:-${GH_TOKEN:-${GITHUB_TOKEN:-}}}"
MODE="${PUBLISH_MODE:-all}"
if [ -z "$TOKEN" ]; then
  echo "Need PAGES_DEPLOY_TOKEN or GH_TOKEN" >&2
  exit 1
fi
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
git clone --depth 1 "https://x-access-token:${TOKEN}@github.com/mrbinio/copy-lab-ops.git" "$TMP/site"
if [ "$MODE" = "data" ]; then
  mkdir -p "$TMP/site/data"
  cp "$ROOT/ops/data/hunt.json" "$TMP/site/data/hunt.json"
  [ -f "$ROOT/ops/data/pulse.json" ] && cp "$ROOT/ops/data/pulse.json" "$TMP/site/data/pulse.json"
  [ -f "$ROOT/ops/data/snapshot.json" ] && cp "$ROOT/ops/data/snapshot.json" "$TMP/site/data/snapshot.json"
  [ -f "$ROOT/ops/data/solutions.json" ] && cp "$ROOT/ops/data/solutions.json" "$TMP/site/data/solutions.json"
else
  rsync -a --delete \
    --exclude '.git' \
    --exclude 'remote.json' \
    "$ROOT/ops/" "$TMP/site/"
  touch "$TMP/site/.nojekyll"
fi
cd "$TMP/site"
git add -A
if git diff --cached --quiet; then
  echo "pages already up to date"
  exit 0
fi
git -c user.name="github-actions[bot]" \
    -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
    commit -m "ops ${MODE} $(date -u +%Y-%m-%dT%H:%MZ)"
git push origin HEAD
echo "published https://mrbinio.github.io/copy-lab-ops/"
