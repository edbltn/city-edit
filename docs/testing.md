# Testing

One taxonomy, two stacks: **(backend, frontend) × (unit, integration, E2E)**.

| | Unit | Integration | E2E |
|--|------|-------------|-----|
| **Backend** (pytest) | `server/tests/unit/` — pure codec + Redis count logic on fakeredis (no DB/Redis) | `server/tests/integration/` — opt-in, needs live Postgres+Redis (`@pytest.mark.integration`) | the stateful load test (below) drives the real `/api/vote` + WebSocket |
| **Frontend** (vitest) | `client-react/src/**/*.test.ts` — codec, vote store, cast planning, vote-apply math (colocated next to source) | covered by the load test's client-state verification | manual `/verify` + the load test |

## Run everything

```bash
make test            # frontend (vitest) + backend (pytest)
make test-frontend   # cd client-react && npm test
make test-backend    # cd server && env/bin/python -m pytest
```

Backend test deps (pytest, fakeredis) are in `server/requirements-dev.in`:

```bash
cd server && env/bin/python -m pip install -r requirements-dev.in
```

## What's covered

- **Codec parity** — `voteKey.test.ts` and `test_vote_codec.py` assert the SAME
  packed integers, so the client store key and the Redis hash field can't drift.
- **Vote math** — fresh / reverse / remove transitions, count accumulation,
  net + node derivation (`test_vote_counts.py`, `voteApply.test.ts`).
- **Multi-select rule** — `castVote.test.ts` (`planVoteChange`): already-cast
  edges are left alone, unvoted get the vote, opposite reverses, all-cast toggles
  off.
- **Store** — direction get/set/clear, mode/edge/type isolation, unknown-label
  fallback + migration, coverage, server reconcile (`voteStore.test.ts`).

## Stateful load test

`loadtest/verify_loadtest.py` assigns each of N agents a deterministic expected
final vote state and a convoluted path to reach it (alternating up/down/remove),
marches them concurrently through `/api/vote`, then verifies the server's
`/api/graph-votes` converged to the expected aggregate.

```bash
make loadtest-verify                 # 10 agents vs localhost:8080
make loadtest-verify USERS=25 HOST=https://…
```
