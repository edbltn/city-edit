#!/bin/bash
# Test the cloud instance for health and basic functionality

CLOUD_URL="${1:-https://desire-path-mapper-prod-906562157830.us-central1.run.app}"

echo "Testing cloud instance: $CLOUD_URL"
echo "========================================"

# Helper to extract HTTP code and body (macOS compatible)
extract_response() {
    local response="$1"
    RESP_CODE=$(echo "$response" | tail -1)
    RESP_BODY=$(echo "$response" | sed '$d')
}

# Test 1: Health check
echo -n "Health check... "
RESPONSE=$(curl -s -w "\n%{http_code}" "$CLOUD_URL/health" 2>/dev/null)
extract_response "$RESPONSE"

if [ "$RESP_CODE" = "200" ]; then
    echo "PASS (200)"
    echo "  Response: $RESP_BODY"
else
    echo "FAIL (HTTP $RESP_CODE)"
    echo "  Response: $RESP_BODY"
    echo ""
    echo "NOTE: Health endpoint may not be deployed yet."
    echo "Run deployment to add /health endpoint."
fi

# Test 2: WebSocket endpoint availability (just check it responds)
echo -n "WebSocket endpoint... "
WS_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$CLOUD_URL/ws" 2>/dev/null)
# WebSocket upgrade returns 400 without proper headers, which is expected
if [ "$WS_CODE" = "400" ] || [ "$WS_CODE" = "426" ]; then
    echo "PASS (endpoint exists, requires upgrade)"
else
    echo "INFO (HTTP $WS_CODE)"
fi

# Test 3: API routes endpoint (POST with sample data)
echo -n "Routes API... "
RESPONSE=$(curl -s -w "\n%{http_code}" \
    -X POST "$CLOUD_URL/api/routes" \
    -H "Content-Type: application/json" \
    -d '{"start":[40.7128,-74.0060],"end":[40.7580,-73.9855],"mode":"bike"}' \
    2>/dev/null)
extract_response "$RESPONSE"

if [ "$RESP_CODE" = "200" ]; then
    echo "PASS (200)"
    # Extract just the distance if possible
    DISTANCE=$(echo "$RESP_BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'distance={d.get(\"route\",{}).get(\"distance\",\"N/A\")}m')" 2>/dev/null)
    if [ -n "$DISTANCE" ]; then
        echo "  $DISTANCE"
    fi
else
    echo "INFO (HTTP $RESP_CODE)"
    echo "  ${RESP_BODY:0:100}"
fi

echo "========================================"
echo "Cloud instance tests completed"
