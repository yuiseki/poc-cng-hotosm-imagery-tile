#!/usr/bin/env bash
# Smoke-test the hotosm-imagery-tile service.
# Run while `docker compose up` (or Knative) is serving.

set -euo pipefail

HOST="${HOTOSM_TILE_HOST:-localhost}"
PORT="${HOTOSM_TILE_PORT:-8080}"
BASE="http://$HOST:$PORT"

# A bbox known to have HOTOSM imagery (Saarland region, Germany).
BBOX="${HOTOSM_VERIFY_BBOX:-7.68,49.56,7.70,49.57}"

echo "=== hotosm-imagery-tile verification ==="
echo "Target: $BASE"
echo "Probe bbox: $BBOX"
echo ""

echo "--- GET / ---"
curl -sf "$BASE/" | python3 -m json.tool
echo ""

echo "--- GET /healthz ---"
curl -sf "$BASE/healthz" | python3 -m json.tool
echo ""

echo "--- GET /search?bbox=$BBOX&limit=3 ---"
SEARCH_JSON=$(curl -sf "$BASE/search?bbox=$BBOX&limit=3")
echo "$SEARCH_JSON" | python3 -m json.tool
echo ""

ITEM_ID=$(python3 -c '
import json, sys
items = json.loads(sys.argv[1]).get("items", [])
print(items[0]["id"] if items else "")
' "$SEARCH_JSON")

if [ -z "$ITEM_ID" ]; then
  echo "No items returned for probe bbox; skipping item-level checks."
  exit 0
fi

echo "Picked item: $ITEM_ID"
echo ""

echo "--- GET /items/$ITEM_ID/metadata ---"
curl -sf "$BASE/items/$ITEM_ID/metadata" | python3 -m json.tool
echo ""

echo "--- GET /items/$ITEM_ID/tilejson.json ---"
TILEJSON=$(curl -sf "$BASE/items/$ITEM_ID/tilejson.json")
echo "$TILEJSON" | python3 -m json.tool
echo ""

# Pull centroid + maxzoom out of the tilejson and ask for that one tile.
read Z X Y < <(python3 -c '
import json, math, sys
tj = json.loads(sys.argv[1])
lon, lat, _ = tj["center"]
z = int(tj.get("maxzoom", 18))
n = 2 ** z
x = int((lon + 180.0) / 360.0 * n)
y = int((1.0 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2.0 * n)
print(z, x, y)
' "$TILEJSON")

TILE_FILE="/tmp/hotosm-imagery-test.png"
echo "--- GET /items/$ITEM_ID/tiles/$Z/$X/$Y.png ---"
HTTP_CODE=$(curl -sf -o "$TILE_FILE" -w "%{http_code}" \
  "$BASE/items/$ITEM_ID/tiles/$Z/$X/$Y.png" || true)
echo "HTTP status: $HTTP_CODE"
if [ "$HTTP_CODE" = "200" ]; then
  echo "Tile size: $(wc -c < "$TILE_FILE") bytes -> $TILE_FILE"
else
  echo "tile fetch failed; try a different (z,x,y) from /items/$ITEM_ID/metadata"
fi
echo ""

echo "--- GET /tiles/$Z/$X/$Y.png  (auto-pick item from bbox) ---"
HTTP_CODE=$(curl -sf -o "$TILE_FILE.auto" -w "%{http_code}" \
  "$BASE/tiles/$Z/$X/$Y.png" || true)
echo "HTTP status: $HTTP_CODE"
if [ "$HTTP_CODE" = "200" ]; then
  echo "Tile size: $(wc -c < "$TILE_FILE.auto") bytes -> $TILE_FILE.auto"
fi
echo ""

echo "=== verification complete ==="
