---
name: verify-app
description: Verify app health. PROACTIVELY use after code changes to ensure Flask, Redis, and frontend work correctly.
tools: Bash, Read, Grep, Glob
model: haiku
---

Verify the City Edit application is working correctly.

Verification steps:
1. Check Redis is running: `redis-cli ping`
2. Check Flask starts without errors: `cd server && python -c "from app import app"`
3. Verify React app builds: `cd client-react && npm run build`
4. Check for Python syntax errors
5. Verify .env exists with required variables (ORS_API_KEY, REDIS_HOST)

Report format:
- Redis: OK/FAIL
- Flask: OK/FAIL (with error if any)
- React build: OK/FAIL
- Environment: OK/MISSING variables
- Overall: HEALTHY/UNHEALTHY

If unhealthy, provide specific fix recommendations.
