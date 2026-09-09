#!/bin/bash
# Copy ops/ onto the public GitHub Pages repo (mrbinio/copy-lab-ops).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TOKEN="${PAGES_DEPLOY_TOKEN:-${GH_TOKEN:-${GITHUB_TOKEN:-}}}"
if [ -z "$TOKEN" ]; then
  echo "Need PAGES_DEPLOY_TOKEN or GH_TOKEN" >&2
  exit 1
fi
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
git clone --depth 1 "https://x-access-token:${TOKEN}@github.com/mrbinio/copy-lab-ops.git" "$TMP/site"
rsync -a --delete \
  --exclude '.git' \
  --exclude 'remote.json' \
  "$ROOT/ops/" "$TMP/site/"
touch "$TMP/site/.nojekyll"
cd "$TMP/site"
git add -A
if git diff --cached --quiet; then
  echo "pages already up to date"
  exit 0
fi
git -c user.name="github-actions[bot]" \
    -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
    commit -m "ops: hunt + console $(date -u +%Y-%m-%dT%H:%MZ)"
git push origin HEAD
echo "published https://mrbinio.github.io/copy-lab-ops/"
