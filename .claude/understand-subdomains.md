# Understand: Vanity subdomains (how to add them + how the code wires it up)

Scope: the subdomain work from this session + the Namecheap/DNS side.

## Layer 1 — The problem (the "why")
- [ ] Why subdomain→map resolution had to become **data-driven** (what the old hardcoded `THEME_TO_SLUG` + `detectTheme()` prefixes prevented)
- [x] The mental model: three independent layers (DNS, TLS, DB row). Baton A = Cloud Run mapping (cert+routing) gets request to box; Baton B = our code/DB picks the map. ✅
- [x] Resolution precedence + graceful fallback: explicit slug > subdomain > default; a miss never errors (avoids broken pages). ✅
- [x] Why `detectSubdomain` must report apex as "no subdomain" (redirect-loop safety, not just wasted lookup). ✅
- [x] Where this sits among the 3 standard patterns; cost = bootstrap flash + no per-subdomain meta. ✅

## Layer 2 — The solution / code logic
- [ ] The resolution chain in `resolveMapConfig()`: explicit slug → subdomain (DB) → default
- [ ] `detectSubdomain()` — what counts as a subdomain (and what's excluded: apex, localhost, www/demo)
- [ ] Server: `/api/maps/by-subdomain/<sub>` + `get_map_by_subdomain` reading the `maps.subdomain` column
- [ ] The `subdomain` column + **partial unique index** (why partial, why unique)
- [ ] Writing it: `set_map_subdomain` + the two admin paths (CLI `manage_maps.py`, token-gated HTTP)
- [ ] The canonical-subdomain **redirect** (`subdomainRedirectUrl`) + why it preserves the query string (deep-link sharing)

## Layer 3 — Broader context (Namecheap + prod)
- [ ] Why nginx is **host-agnostic** and what that buys us
- [ ] Cloud Run **domain mappings** (per-subdomain cert) vs **wildcard via load balancer** — the trade-off
- [ ] The **Namecheap** steps: which record type, host, and value; per-subdomain vs wildcard
- [ ] End-to-end runbook: add a brand-new vanity subdomain start→finish

## Status: not started
