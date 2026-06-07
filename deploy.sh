#!/usr/bin/env bash
# deploy.sh — bump version, build, and push Gist in one shot
# Usage: ./deploy.sh <new-version>
# Example: ./deploy.sh 1.20.0
set -euo pipefail

GIST_ID="7a8201ed67cc3097e0430d1c9df038ab"
REPO="daisy-magus/homestead-launcher-releases"
CONFIG="launcher/config.py"

# ── Args ──────────────────────────────────────────────────────────────────────
if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <version>  (e.g. $0 1.20.0)"
  exit 1
fi
NEW_VERSION="$1"
TAG="v${NEW_VERSION}"

# ── Sanity checks ─────────────────────────────────────────────────────────────
if ! command -v gh &>/dev/null; then
  echo "ERROR: gh CLI not found. Install: https://cli.github.com/"
  exit 1
fi

if [[ $(git status --porcelain) ]]; then
  echo "ERROR: Uncommitted changes — commit or stash first."
  exit 1
fi

if git rev-parse "$TAG" &>/dev/null 2>&1; then
  echo "ERROR: Tag $TAG already exists."
  exit 1
fi

# ── Read current Gist ─────────────────────────────────────────────────────────
echo "→ Fetching current Gist..."
GIST_JSON=$(curl -sf "https://gist.githubusercontent.com/daisy-magus/${GIST_ID}/raw/homestead-server.json?_=$(date +%s)")

# Pull all fields we want to preserve unchanged
GAME_IP=$(echo "$GIST_JSON"    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['game_ip'])")
LAN_IP=$(echo "$GIST_JSON"     | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['lan_ip'])")
GAME_PORT=$(echo "$GIST_JSON"  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['game_port'])")
SYNC_URL=$(echo "$GIST_JSON"   | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('sync_url',''))")
TS_KEY=$(echo "$GIST_JSON"     | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('tailscale_key',''))")
PACK_VER=$(echo "$GIST_JSON"   | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('pack_version','1.0.0'))")
PACK_LOG=$(echo "$GIST_JSON"   | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('pack_changelog',''))")
OLD_VERSION=$(echo "$GIST_JSON"| python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('launcher_version','?'))")

echo "  Current launcher_version: $OLD_VERSION → $NEW_VERSION"

# ── Bump version in config.py ─────────────────────────────────────────────────
echo "→ Bumping version in $CONFIG..."
sed -i "s/^VERSION = \".*\"/VERSION = \"${NEW_VERSION}\"/" "$CONFIG"

# Verify the change landed
if ! grep -q "VERSION = \"${NEW_VERSION}\"" "$CONFIG"; then
  echo "ERROR: Version bump failed — check $CONFIG manually."
  exit 1
fi

# ── Commit + tag ──────────────────────────────────────────────────────────────
echo "→ Committing and tagging..."
git add "$CONFIG"
git commit -m "Release v${NEW_VERSION}"
git tag "$TAG"
git push origin main
git push origin "$TAG"

# ── Wait for CI ───────────────────────────────────────────────────────────────
echo "→ Waiting for GitHub Actions build (this takes ~2 minutes)..."
sleep 10  # give GH a moment to register the run

RUN_ID=""
for i in $(seq 1 30); do
  RUN_ID=$(gh run list --repo "$REPO" --limit 10 --json headBranch,databaseId,status,name \
    | python3 -c "
import sys, json
runs = json.load(sys.stdin)
for r in runs:
    if r.get('headBranch') == '${TAG}' or r.get('name','').endswith('${NEW_VERSION}'):
        print(r['databaseId'])
        break
" 2>/dev/null || true)
  if [[ -n "$RUN_ID" ]]; then break; fi
  echo "  Waiting for run to appear... ($i/30)"
  sleep 5
done

if [[ -z "$RUN_ID" ]]; then
  echo "WARNING: Could not find CI run for $TAG. Check GitHub Actions manually."
  echo "  Once done, run:"
  echo "    ./deploy.sh --update-gist-only $NEW_VERSION"
  exit 1
fi

echo "  Found run: $RUN_ID"
gh run watch "$RUN_ID" --repo "$REPO" --exit-status
echo "✓ Build complete."

# ── Update Gist ───────────────────────────────────────────────────────────────
echo "→ Updating Gist to $NEW_VERSION..."

NEW_CONTENT=$(python3 - <<PYEOF
import json
data = {
    "game_ip":          "$GAME_IP",
    "lan_ip":           "$LAN_IP",
    "game_port":        $GAME_PORT,
    "sync_url":         "$SYNC_URL",
    "tailscale_key":    "$TS_KEY",
    "launcher_version": "$NEW_VERSION",
    "linux_url":        "https://github.com/${REPO}/releases/download/${TAG}/Homestead-linux-x86_64",
    "windows_url":      "https://github.com/${REPO}/releases/download/${TAG}/HomesteadSetup.exe",
    "pack_version":     "$PACK_VER",
    "pack_changelog":   "$PACK_LOG",
}
print(json.dumps(data, indent=2))
PYEOF
)

gh api \
  --method PATCH \
  "/gists/${GIST_ID}" \
  --field "files[homestead-server.json][content]=${NEW_CONTENT}" \
  --jq '.updated_at' | xargs -I{} echo "  Gist updated at {}"

echo ""
echo "✓ Deploy complete: $OLD_VERSION → $NEW_VERSION"
echo "  Release:  https://github.com/${REPO}/releases/tag/${TAG}"
