#!/bin/bash
# Health check script for supervisor
# Checks gunicorn health every 60 seconds and restarts if unhealthy

HEALTH_URL="http://127.0.0.1:5001/health"
CHECK_INTERVAL=60
MAX_FAILURES=3

failures=0

echo "[healthcheck] Starting health checker (interval=${CHECK_INTERVAL}s, max_failures=${MAX_FAILURES})"

while true; do
    sleep $CHECK_INTERVAL

    # Check health endpoint
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$HEALTH_URL" --max-time 5 2>/dev/null)

    if [ "$HTTP_CODE" = "200" ]; then
        if [ $failures -gt 0 ]; then
            echo "[healthcheck] Recovered after $failures failures"
        fi
        failures=0
    else
        failures=$((failures + 1))
        echo "[healthcheck] Health check failed (HTTP $HTTP_CODE), failure $failures/$MAX_FAILURES"

        if [ $failures -ge $MAX_FAILURES ]; then
            echo "[healthcheck] Max failures reached, restarting gunicorn..."
            supervisorctl restart gunicorn
            failures=0
            # Wait for gunicorn to start up
            sleep 10
        fi
    fi
done
