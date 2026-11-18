#!/bin/bash
# Deploys pub/ to the store server.
#
#   ./deploy.sh dev|prod [-n] [-y]
#     -n  dry run: check, then show what would be uploaded
#     -y  don't ask for confirmation (prod)
#
# Safety:
# - check.py must pass.
# - prod: the working tree must be clean, so what's live is always a commit.
# - An existing zip on the server is never replaced with different content. Cloudflare caches
#   zips for 7 days, so users would keep getting the old one. Use a new file name instead.
# - Nothing is deleted on the server. Apps that fetched the previous index.json may still
#   download files from it.
# - index.json is uploaded last (rsync writes a temp file and renames it), so the listing never
#   points at a file that isn't there yet.

set -euo pipefail
cd "$(dirname "$0")"

HOST=www@main
TARGET=""
DRY=0
YES=0
for arg in "$@"; do
  case "$arg" in
    dev|prod) TARGET=$arg ;;
    -n) DRY=1 ;;
    -y) YES=1 ;;
    *) echo "Unknown argument: $arg"; exit 1 ;;
  esac
done

case "$TARGET" in
  prod) DEST=/srv/www/store.ppsspp.org/pub; HOSTNAME=store.ppsspp.org ;;
  dev)  DEST=/srv/www/store.ppsspp.org/dev; HOSTNAME=dev-store.ppsspp.org ;;
  *) echo "Usage: $0 dev|prod [-n] [-y]"; exit 1 ;;
esac

echo "== Checking"
python3 check.py

if [ "$TARGET" = prod ] && [ -n "$(git status --porcelain)" ]; then
  echo "Uncommitted changes. Commit before deploying to prod:"
  git status --short
  exit 1
fi

# Compare by content (-c), since local mtimes come from git checkouts.
RSYNC=(rsync -rlt -c --exclude=index.json)

echo "== Changes on $HOST:$DEST"
CHANGES=$("${RSYNC[@]}" --dry-run --itemize-changes pub/ "$HOST:$DEST/" | grep '^[<>c]f' || true)
IDX=$(rsync -rlt -c --dry-run --itemize-changes pub/index.json "$HOST:$DEST/index.json" | grep '^[<>]f' || true)
if [ -z "$CHANGES" ] && [ -z "$IDX" ]; then
  echo "Nothing to do, $TARGET is up to date."
  exit 0
fi
[ -n "$CHANGES" ] && echo "$CHANGES"
[ -n "$IDX" ] && echo "$IDX"

# '>f+++++++++' is a new file. Anything else under files/ is an existing zip being replaced.
REPLACED=$(echo "$CHANGES" | grep '^>f' | grep -v '^>f+++++++++' | grep ' files/' || true)
if [ -n "$REPLACED" ]; then
  echo
  echo "ERROR: these zips already exist on the server with different content:"
  echo "$REPLACED"
  echo "Cloudflare caches zips for a week, so give the new version a new file name"
  echo "(and point download-url at it)."
  exit 1
fi

if [ $DRY = 1 ]; then
  echo "(dry run)"
  exit 0
fi

if [ "$TARGET" = prod ] && [ $YES = 0 ]; then
  read -r -p "Deploy to PROD ($HOSTNAME)? [y/N] " answer
  [ "$answer" = y ] || [ "$answer" = Y ] || exit 1
fi

echo "== Uploading"
ssh "$HOST" "mkdir -p $DEST"
"${RSYNC[@]}" --info=progress2 pub/ "$HOST:$DEST/"
rsync -rlt -c pub/index.json "$HOST:$DEST/index.json"

echo "== Verifying"
# Fetch through nginx on the server itself, bypassing Cloudflare.
REMOTE=$(ssh "$HOST" "curl -sf -H 'Host: $HOSTNAME' http://127.0.0.1/index.json | sha256sum" | cut -d' ' -f1 || true)
LOCAL=$(sha256sum pub/index.json | cut -d' ' -f1)
if [ "$REMOTE" = "$LOCAL" ]; then
  echo "OK, $HOSTNAME serves the new index.json."
else
  echo "WARNING: $HOSTNAME doesn't serve the new index.json (no nginx site for it yet?)."
fi
