# Project Architecture

## 🚧 In progress (2026-06-14): Caching + concurrency + zoom overhaul

Active workstream (full report with diffs: `changelog/2026-06-14-caching-concurrency-zoom.html`, index at `changelog/index.html`):
1. **Stale-cache heatmap crash** — the app crashed on heatmap load (mobile Safari "a problem repeatedly occurred", esp. NYC ebikes). Fixing via defense-in-depth: server stamps topology dimensions onto `/api/graph-votes` so the client can detect a topology/vote mismatch; client validates lengths + the binary topology header; a new React error boundary clears the poisoned IndexedDB cache and reloads once to break the crash loop.
2. **Concurrency / >1 Flask instance** — the single gevent worker head-of-line-blocked under concurrent tenants and the in-memory vote cache grew unbounded. Fixing via a bounded LRU vote cache, correct mode-scoped invalidation, single-flight on the expensive vote-array build, and a cross-instance Redis vote lock so horizontal scaling is safe.
3. **Zoom overhaul** — the heatmap canvas cleared on `zoomstart` and only repainted on `zoomend`, so it vanished mid-zoom. Now it rides Leaflet's zoom animation (CSS-transition transform like `L.Canvas`) and scales smoothly instead of disappearing.

## Claude Instructions

- **Python dependencies**: ALWAYS install and manage Python packages with `uv pip` — never bare `pip`, `pip3`, `python -m pip`, `poetry`, `conda`, or `easy_install`. Add a package by editing `requirements.in`, recompile with `uv pip compile requirements.in -o requirements.txt`, then `uv pip install -r requirements.txt`. For one-off installs use `uv pip install <pkg>`. This applies everywhere: local venvs, scripts, and Dockerfiles.
- **gcloud commands**: Run gcloud commands directly (e.g. `gcloud builds submit`, `gcloud run services logs read`, etc.) without asking the user.
- **ALWAYS back up the prod DB locally before deploying** — every deploy, no exceptions. Take a fresh `pg_dump` snapshot of `desire-path-votes-prod` into `~/city-edit-prod-backups/<UTC-timestamp>/` *before* running any deploy command (Cloud Build, `terraform apply`, or `gcloud run services update`). Procedure: [docs/gcp-deployment.md#database-access--backups](docs/gcp-deployment.md#database-access--backups).
- **docker commands**: Run docker commands directly (e.g. `docker compose up --build -d`, `docker compose logs`, etc.) without asking the user.
- **Commit every completed request**: whenever one of my messages results in code changes, land those changes as a git commit once they're done and verified — no need to ask. Git is my precise log of everything you changed and why. Rules:
  - One commit per completed request; if the work naturally splits into independent pieces (e.g. a server fix + an unrelated doc cleanup), several logical commits are better than one blob.
  - Stage **only the files you touched for that request** (explicit paths, never `git add -A`/`git add .`) so my own in-flight edits are never swept into your commit.
  - Message: conventional prefix (`feat`/`fix`/`refactor`/`docs`), a subject that states the change, and a body that says *why* — enough that the commit alone explains itself without the chat transcript. Keep the existing `Co-Authored-By: Claude` trailer.
  - Commit locally only — never push unless I ask.
  - If tests fail or the work is half-done at the end of a turn, say so instead of committing a broken state.
- **Browser testing**: I have a browser AI helper that can report on status. When you need me to test something, ask questions that this AI can answer (descriptions of screenshots, UI changes that need verification, functionality checks, error messages visible on screen).
- **Debugging workflow** (full doc: [docs/debugging.md](docs/debugging.md)): when we debug together, ask me to open a **named debug tab** — `http://localhost:3000/m/<slug>?tab=<name>` — which tags the tab title `[dbg:<name>]` and turns on all client debug channels. Find that tab by title via the Chrome tools, read its console with pattern `\[(topo|votes|cast|store|blocks|proposals|maplibre|ws)\]`, and run `cityedit.dumpState()` for the one-call health check. Client logging goes through `dlog(channel, …)` in `client-react/src/utils/debugLog.ts` (never bare `console.log`); server lines are `[TAG]`-prefixed (`LOG_LEVEL=DEBUG` for chatty). NOTE: a hidden/occluded Chrome window freezes rAF, so MapLibre never fires `load` there — bring the window forward (or expect `maplibreLoaded: false`) before concluding rendering is broken.

### Change logs (always produce one for substantial work)

- Write an **HTML change-log report** under `changelog/` (one file per workstream, e.g. `changelog/<date>-<topic>.html`, linked from `changelog/index.html`). Capture the diff and generate it with a small Python builder (see `changelog/build_report.py`): write the diff to `changelog/changes.diff` (`git diff … > changelog/changes.diff`), then run the builder.
- **For EVERY diff in the report, include a hierarchical "where does this block sit" context diagram** — a recursively-summarized map at four zoom levels: **System** (which of City Edit's components — nginx · Flask API · OSRM · Redis · React/Leaflet client — the file belongs to, shown as highlighted pills) → **Module** (the subsystem + a one-line summary) → **File** (a one-line summary + LOC) → a **file map** (the file's top-level sections as a focus+context minimap, with the changed sections highlighted and the rest dimmed) → the ordered **changed blocks**. This data lives in `FILE_CONTEXT` in `build_report.py`; add an entry per changed file.
- After finishing, **link the report** in the response so I can open it.

### Local preview (redeploy on each change)

- On each change, **redeploy to local dev and give me the link `http://localhost:3000/`** (Vite). The stack is: **Redis** on `:6379`, host **Flask** on `:5001` (`cd server && ./env/bin/python app.py`; for a fast restart use `SKIP_PREWARM=1` — graphs load lazily on first request), and **Vite** on `:3000` (`cd client-react && npm run dev`) which proxies `/api` + `/ws` to `:5001`.
- Vite hot-reloads client edits; **restart Flask** after any `server/*.py` change (it doesn't auto-reload). If a long-running dev server wedges (e.g. `EPERM` reading `index.html` from a stale sandbox), kill and relaunch it via `nohup … &`.
- Map URLs are `http://localhost:3000/m/<slug>` (e.g. `/m/nyc-walkways` for the streets heatmap, `/m/e-bikes-3` for a public e-bikes station map). Verify the redeploy took: `curl -s localhost:5001/api/graph-votes?map=<slug>&mode=<mode>` and confirm the expected fields.

## Overview

City Edit is a crowdsourced map showing how people actually travel through a city. Users submit their commute routes, which are aggregated and visualized as a heatmap overlay.

## Components

- **Nginx**: Reverse proxy serving static files and load-balancing Flask instances
- **Flask**: Python backend handling vote processing and graph topology (runs as multiple replicas)
- **OSRM**: Self-hosted Open Source Routing Machine for fast pathfinding (foot profile, MLD algorithm)
- **Redis**: In-memory store for vote data and real-time state; used for pub/sub to sync state across Flask instances
- **Client**: React + Leaflet map UI (TypeScript, Vite)

## Frontend

The React frontend is in `client-react/`.

- **Local dev**: `cd client-react && npm run dev` (port 3000)
- **Docker dev**: `docker compose -f docker-compose.yml -f docker-compose.dev.yml up`
- **Docker prod**: `docker compose up --build` (port 8080)

## Environment Variables

All secrets and configuration are stored in `server/.env`. Copy from `.env.example` if needed.

| Variable | Description | Required |
|----------|-------------|----------|
| `REDIS_HOST` | Redis host (default: `localhost`) | No |
| `DATABASE_URL` | PostgreSQL connection URL for persistent vote storage | No |
| `OSRM_HOST` | OSRM service host (default: `localhost`) | No |
| `OSRM_PORT` | OSRM service port (default: `5000`) | No |

**Never commit `.env` to git.** It's in `.gitignore`.

## Data Flow

1. Client connects via WebSocket to receive real-time map state
2. User submits a commute route (start/end points + mode)
3. Flask calls self-hosted OSRM for fast pathfinding (sub-ms routing)
4. Route segments are converted to vote points stored in Redis
5. WebSocket broadcasts updated heatmap to all clients

## Routing

### Architecture
Routing uses **OSRM** (Open Source Routing Machine) for fast pathfinding and a **Python graph provider** for topology visualization, nearest-node snapping, and reverse geocoding.

The votable/topology graph is built from the **same OSM PBF + foot filter as OSRM**, so it's a superset of OSRM's foot network and route votes map cleanly by OSM node id (OSRM returns node ids via `annotations=nodes` → `vote_store.osm_nodes_to_edge_ids`). `server/foot_profile.py` mirrors `osrm/foot.lua` (v5.25.0) — keep them in sync.

- **OSRM router**: `server/osrm_router.py` - HTTP client calling self-hosted OSRM
- **OSRM profile**: `osrm/foot.lua` - pinned foot profile (the routable-way rules)
- **Graph provider**: `server/python_router.py` - Graph topology, snapping, reverse geocoding
- **Graph builder**: `server/osm_graph_builder.py` - reads the PBF (pyosmium) into the walk graph; `server/foot_profile.py` decides which ways are foot-routable
- **Graph files**: `server/osm_data/<city>/walk_graph.pkl` - per-city graph built from `<city>/source.osm.pbf`
- **Refresh script**: `server/refresh_osm.py` - downloads each city's PBF and rebuilds graphs
- **Validation**: `server/tests/validate_osrm_topology.py` - confirms OSRM node ids resolve to topology edges

### Regions
Available regions for graph building:
- `downtown`: Battery Park to 34th Street (~6km x 4km)
- `fidi`: Financial District only (~1.6km x 1.5km)
- `manhattan`: Full Manhattan
- `nyc` (default): All 5 boroughs (~673K nodes, ~1.97M edges)
- `nyc-metro`: Full NYC metro area (high memory)

### Rebuilding Graphs
If graphs are missing or outdated:
```bash
cd server && source env/bin/activate
python refresh_osm.py --region nyc --force
```

## Docker

```bash
# Production
docker compose up --build

# Development (with hot reload)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

Services: `nginx` (port 8080), `flask` (internal, 3 replicas), `osrm` (internal, port 5000), `redis` (port 6379).

## Local Development

Hybrid loop (canonical — see the README Quickstart): backing services in
Docker, Flask + Vite on the host for fast edit cycles.

```bash
# Backing services (Redis :6379, Postgres :5432, OSRM → host :5005)
make deps   # = docker compose -f docker-compose.yml -f docker-compose.osrmport.yml up -d redis postgres osrm

# Flask on the host (restart after server/*.py edits; SKIP_PREWARM=1 for fast boots)
cd server && source env/bin/activate && python app.py

# Client on the host (hot-reloads on file changes)
cd client-react && npm run dev
```

Or all at once: `make dev` (builds missing graphs/tiles first).

## Connecting to the Prod Database

Prod Postgres is Cloud SQL `desire-path-votes-prod` (private IP `10.39.0.3:5432`), reached via IAP through the `bastion-prod` VM. Creds are in Secret Manager (`database-url-prod`). Backup/restore of prod data — and where snapshots live (`~/city-edit-prod-backups/`) — is documented in [docs/gcp-deployment.md](docs/gcp-deployment.md#database-access--backups).

**⚠️ Bind the tunnel to local port 5433, never 5432.** Local dev's own DB lives on `localhost:5432` (`server/.env` `DATABASE_URL` and the `docker-compose.yml` postgres port). A tunnel on `5432` shadows it, so host-run Flask silently connects to **prod**. Keep prod on 5433; never repoint `server/.env` at prod — pass the prod URL inline for the one command that needs it, and kill the tunnel when done.

```bash
# Terminal A: open the tunnel (prod → local 5433)
gcloud compute ssh bastion-prod --zone=us-central1-a \
  --project=google-mpf-ywspom2sxeey --tunnel-through-iap \
  --ssh-flag="-N" --ssh-flag="-L 5433:10.39.0.3:5432"

# Terminal B: connect with PROD creds via localhost:5433 (rewrites host on the fly)
PROD_DB_URL="$(gcloud secrets versions access latest --secret=database-url-prod \
  --project=google-mpf-ywspom2sxeey | sed -E 's#@[^/]+/#@localhost:5433/#')"
psql "$PROD_DB_URL"
```

---

# CSS Best Practices

## Organization

### File Structure
- Start with CSS custom properties (`:root` variables) at the top
- Follow with reset/base styles (`html`, `body`)
- Then layout components (`.topbar`, `.sidebar`, etc.)
- End with utility classes if needed

### Grouping
- Group related properties together
- Use blank lines to separate distinct rule sets
- Keep one blank line between selectors

## Naming & Selectors

### Specificity
- Prefer class selectors over IDs
- Avoid deep nesting (max 2-3 levels)
- Use descendant selectors sparingly (`.topbar .title` is fine, `.header .nav .list .item .link` is not)

### Naming Conventions
- Use semantic, descriptive names (`.topbar` not `.header-bar-1`)
- Use kebab-case for multi-word classes (`.user-profile`, not `.userProfile`)
- Consider BEM methodology for complex components

## Formatting

### Spacing & Indentation
- Use 2 spaces for indentation
- Add space after colons: `color: red;` not `color:red;`
- Add space in rgba/functions: `rgba(0, 0, 0, 0.5)` not `rgba(0,0,0,.5)`
- Separate selectors on new lines when multiple: `html,\nbody {` not `html, body {`

### Property Order
Loosely follow this order:
1. Positioning (`position`, `top`, `left`, `z-index`)
2. Box model (`display`, `width`, `height`, `margin`, `padding`, `border`)
3. Typography (`font-*`, `line-height`, `letter-spacing`, `color`)
4. Visual (`background`, `opacity`, `box-shadow`)
5. Other (`cursor`, `transition`, `animation`)

### Values
- Use shorthand when appropriate: `margin: 0;` not `margin-top: 0; margin-bottom: 0;...`
- Be explicit when needed: `padding: 0 16px;` is clearer than `padding: 0 1rem;` sometimes
- Use unitless line-height: `line-height: 1.5;` not `line-height: 1.5em;`
- Omit units for zero: `margin: 0;` not `margin: 0px;`

## Design Tokens

### CSS Custom Properties
- Define all colors, fonts, spacing as CSS variables in `:root`
- Use semantic names: `--ink`, `--paper` not `--black`, `--white`
- Group by category with blank lines between groups

Example:
```css
:root {
  /* Typography */
  --font-ui: "Source Sans 3", system-ui, sans-serif;
  --font-editorial: "Source Serif 4", Georgia, serif;

  /* Colors */
  --paper: #fbfbf8;
  --ink: #111;
  --hairline: rgba(0, 0, 0, 0.12);

  /* Layout */
  --topbar-height: 44px;
}
```

## Modern CSS Features

### Use When Appropriate
- CSS Grid for two-dimensional layouts
- Flexbox for one-dimensional layouts
- CSS custom properties for theming
- `calc()` for computed values
- Modern color functions (`oklch`, `color-mix`) when browser support allows

### Avoid
- Overly clever hacks
- Browser-specific prefixes without fallbacks
- `!important` (except for utilities)
- Inline styles in HTML

## Comments

### When to Comment
- Complex calculations: `/* 100vh - topbar - footer */`
- Non-obvious decisions: `/* backdrop-filter needs semi-transparent bg */`
- Browser-specific workarounds

### When NOT to Comment
- Obvious things: `/* Make text red */` before `color: red;`
- Restating the property name: `/* Font family */` before `font-family: ...;`

## Performance

- Avoid expensive properties like `box-shadow` on large/animated elements
- Use `will-change` sparingly and temporarily for animations
- Prefer `transform` and `opacity` for animations (GPU-accelerated)
- Consider `contain` property for performance boundaries

---

# JavaScript Best Practices

## File Organization

### File Structure
1. **Imports** at the top
2. **Constants and configuration** (UPPER_SNAKE_CASE for true constants)
3. **Helper functions** (pure utilities)
4. **Main functions** (exported API)
5. **Exports** at the bottom (or inline with function declarations)

Example:
```js
import { CONFIG } from "./config.js";
import { helper } from "./utils.js";

const DEFAULT_TIMEOUT = 5000;

const defaultLineStyle = {
  weight: 3,
  opacity: 0.85,
  lineCap: "round",
  lineJoin: "round"
};

function processData(data) {
  // helper function
}

export function createManager(options) {
  // main exported function
}
```

### Grouping & Spacing
- One blank line between functions
- Two blank lines between major sections (imports → constants → functions)
- Group related constants together
- Keep related functions near each other

## Naming Conventions

### Variables & Functions
- `camelCase` for variables and functions: `getUserData`, `isActive`
- `PascalCase` for classes and constructors: `UserManager`, `EventEmitter`
- `UPPER_SNAKE_CASE` for true constants: `API_KEY`, `MAX_RETRY_COUNT`
- Prefix booleans with `is`, `has`, `should`: `isLoading`, `hasError`, `shouldUpdate`

### Descriptive Names
- Use full words: `getUserById` not `getUsrById`
- Be specific: `fetchUserProfile` not `getData`
- Avoid abbreviations unless very common: `btn` → `button`, `idx` → `index`

### Scope-Appropriate Length
- Short names for small scopes: `for (const item of items)`
- Longer names for larger scopes: `activeUserSessionManager`

## Code Style

### Spacing & Formatting
- Use 2 spaces for indentation
- Space after keywords: `if (condition)` not `if(condition)`
- Space around operators: `a + b` not `a+b`
- No space before function params: `function foo()` not `function foo ()`
- Trailing commas in multiline objects/arrays for cleaner diffs

### Object & Array Literals
```js
// Short objects: single line
const point = { x: 10, y: 20 };

// Long objects: multiple lines with trailing comma
const config = {
  timeout: 5000,
  retries: 3,
  endpoint: "/api/v1"
};

// Inline comments for clarity
const style = {
  weight: 3,       // stroke width in pixels
  opacity: 0.85,   // overall stroke opacity
  lineCap: "round",
  lineJoin: "round"
};
```

### Functions
- Prefer `function` declarations for top-level named functions (hoisted, debugger-friendly)
- Use arrow functions for callbacks and short utilities
- Use `const` for arrow functions assigned to variables

```js
// Top-level: function declaration
function createOverlayManager(map) {
  // ...
}

// Callback: arrow function
items.map(item => item.id);

// Assigned: const with arrow
const formatDate = (date) => date.toISOString();
```

### Destructuring
Use destructuring for clarity:
```js
// Good
const { x, y } = point;
const [first, ...rest] = array;

// In function params
function process({ id, name, options = {} }) {
  // ...
}
```

## Modern JavaScript

### Prefer Modern Syntax
- `const`/`let` over `var`
- Template literals over string concatenation: `` `Hello ${name}` ``
- Spread operator: `{ ...defaults, ...options }`
- Optional chaining: `overlay.options?.style`
- Nullish coalescing: `value ?? defaultValue`
- Array methods: `.map()`, `.filter()`, `.reduce()` over manual loops

### Avoid
- `var` declarations
- `==` comparison (use `===`)
- Modifying function parameters
- Deep mutations of objects (prefer immutability)
- Overly clever one-liners that sacrifice readability

## Error Handling

### Be Explicit
```js
// Check parameters
function createLayer(id, overlay) {
  if (!id || !overlay) {
    throw new Error("id and overlay are required");
  }
  // ...
}

// Handle edge cases early (guard clauses)
function removeLayer(id) {
  const layer = layersById.get(id);
  if (!layer) return;

  map.removeLayer(layer);
  layersById.delete(id);
}
```

### When to Return Early
- Use guard clauses for error conditions
- Return early for edge cases
- Keep the happy path at the lowest indentation level

## Comments

### When to Comment
- Complex algorithms or business logic
- Non-obvious optimizations
- API quirks or workarounds: `// Leaflet requires clearLayers before addData`
- TODOs with context: `// TODO: add support for polygon overlays`

### When NOT to Comment
- Obvious code: `// increment i` before `i++`
- Restating variable names: `// user name` before `const userName`
- Outdated comments (remove or update them!)

### Prefer Self-Documenting Code
```js
// Bad: needs comment
const d = 86400000; // milliseconds in a day

// Good: self-documenting
const MILLISECONDS_PER_DAY = 86400000;
```

## Modules & Exports

### Named Exports
Prefer named exports for clarity:
```js
export function createManager() { }
export const DEFAULT_CONFIG = { };
```

### Default Exports
Use sparingly, mainly for single-purpose modules:
```js
export default class UserService { }
```

### Import Organization
```js
// 1. External dependencies
import L from "leaflet";

// 2. Internal modules (absolute paths if available)
import { CONFIG } from "./config.js";
import { createOverlayManager } from "./overlays.js";

// 3. Blank line before code starts
```

## Performance

- Avoid unnecessary re-renders or recalculations
- Use `Map` and `Set` for lookups instead of arrays
- Debounce/throttle expensive operations
- Cache computed values when appropriate
- Prefer iteration methods that don't create intermediate arrays when chaining many operations

## Patterns

### Factory Functions
```js
export function createOverlayManager(map) {
  const state = {};

  function publicMethod() {
    // has access to state
  }

  return { publicMethod };
}
```

### Object Configuration
Accept configuration objects for functions with many parameters:
```js
// Good
function createLayer({ id, style, opacity = 1.0 }) {
  // ...
}

// Avoid
function createLayer(id, style, opacity, visible, zIndex) {
  // too many params
}
```

---

# Python Best Practices

## Package Management with uv

**ALWAYS use `uv pip` for every dependency operation — never bare `pip`/`pip3`, `python -m pip`, `poetry`, or `conda`.** This is non-negotiable across local venvs, scripts, and Docker builds. If `uv` isn't installed, install it (`pip install uv` once, or `brew install uv`) rather than falling back to bare pip.

### Dependency Files
Use a two-file approach with `uv`:
- `requirements.in` - High-level dependencies (hand-written)
- `requirements.txt` - Locked, pinned dependencies (auto-generated)

**requirements.in** (minimal, human-maintained):
```
flask
flask-sock
flask-cors
requests
```

**Workflow**:
```bash
# Compile dependencies with uv (generates requirements.txt)
uv pip compile requirements.in -o requirements.txt

# Install dependencies
source env/bin/activate
uv pip install -r requirements.txt
```

### Virtual Environments

**Setup** (one-time):
```bash
python3 -m venv env
source env/bin/activate
uv pip install -r requirements.txt
```

**Daily usage**:
```bash
# Activate
source env/bin/activate

# Deactivate
deactivate
```

**Best practices**:
- Always use virtual environments (never install globally)
- Add `env/` to `.gitignore`
- Commit both `requirements.in` and `requirements.txt`
- Use `uv pip compile` when adding/updating dependencies

## Code Style

### Formatting
Follow PEP 8 with these highlights:
- 4 spaces for indentation (not tabs)
- Max line length: 88 characters (Black default)
- Two blank lines between top-level functions/classes
- One blank line between methods

### Naming Conventions
```python
# snake_case for variables and functions
user_name = "Alice"
def get_user_data():
    pass

# PascalCase for classes
class UserManager:
    pass

# UPPER_SNAKE_CASE for constants
MAX_RETRY_COUNT = 3
API_BASE_URL = "https://api.example.com"

# _leading_underscore for internal/private
def _internal_helper():
    pass
```

### Imports
Organize imports in three groups with blank lines between:
```python
# 1. Standard library
import json
import time
from typing import Dict, List

# 2. Third-party packages
import requests
from flask import Flask, jsonify, request

# 3. Local modules
from .utils import parse_data
from .config import CONFIG
```

### Type Hints
Use type hints for function signatures (especially in public APIs):
```python
def make_state(rev: int) -> dict:
    return {"revision": rev}

def geocode(query: str, limit: int = 5) -> list[dict]:
    results = fetch_results(query, limit)
    return results
```

## Functions & Classes

### Function Design
```python
# Good: single responsibility, clear name
def format_address(address: dict) -> str:
    parts = [
        address.get("house_number"),
        address.get("road"),
        address.get("city")
    ]
    return ", ".join(filter(None, parts))

# Avoid: too many parameters
def create_user(name, email, age, city, country, phone, address):
    pass

# Better: use a dataclass or dict
from dataclasses import dataclass

@dataclass
class UserData:
    name: str
    email: str
    age: int
    city: str

def create_user(data: UserData):
    pass
```

### Early Returns
Use guard clauses for validation:
```python
def geocode(query: str):
    if not query:
        return {"error": "Missing query"}, 400

    if len(query) < 3:
        return {"error": "Query too short"}, 400

    # Main logic at lowest indentation
    results = perform_search(query)
    return results
```

## Flask Best Practices

### Route Organization
```python
# Group related routes
@app.route("/api/geocode")
def geocode():
    """Geocode search query using Nominatim"""
    query = request.args.get("q", "")
    # ...

@app.route("/api/reverse-geocode")
def reverse_geocode():
    """Reverse geocode lat/lon to address"""
    lat = request.args.get("lat")
    lon = request.args.get("lon")
    # ...
```

### Error Handling
```python
from flask import jsonify

@app.route("/api/geocode")
def geocode():
    query = request.args.get("q", "")
    if not query:
        return jsonify({"error": "Missing query parameter 'q'"}), 400

    try:
        response = requests.get(url, params=params, timeout=5)
        response.raise_for_status()
        return jsonify(response.json())
    except requests.RequestException as e:
        return jsonify({"error": str(e)}), 500
```

### Configuration
```python
# config.py
class Config:
    DEBUG = False
    TESTING = False
    NYC_BBOX = "-74.26,40.49,-73.70,40.92"

class DevelopmentConfig(Config):
    DEBUG = True

# app.py
app.config.from_object('config.DevelopmentConfig')
```

## Error Handling

### Be Specific
```python
# Good: catch specific exceptions
try:
    data = json.loads(text)
except json.JSONDecodeError as e:
    logger.error(f"Invalid JSON: {e}")
    return None

# Avoid: bare except
try:
    data = json.loads(text)
except:  # Too broad!
    pass
```

### Context Managers
```python
# Use context managers for resources
with open("data.json") as f:
    data = json.load(f)

# Custom context manager
from contextlib import contextmanager

@contextmanager
def db_transaction(conn):
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
```

## Comments & Docstrings

### Docstrings
Use docstrings for all public functions/classes:
```python
def geocode(query: str, limit: int = 5) -> list[dict]:
    """
    Geocode a search query using Nominatim API.

    Args:
        query: Address or place name to search for
        limit: Maximum number of results to return

    Returns:
        List of geocoding results with lat, lon, and display_name

    Raises:
        requests.RequestException: If API request fails
    """
    # ...
```

### When to Comment
- Complex algorithms or business logic
- Non-obvious optimizations
- Workarounds for external API quirks
- TODOs with context

### When NOT to Comment
```python
# Bad: obvious
# Increment counter
counter += 1

# Bad: outdated
# TODO: Add caching (already implemented)

# Good: explains why
# Use bounded=1 to restrict results to viewbox, not just bias them
params["bounded"] = 1
```

## Modern Python Features

### Use
- f-strings for formatting: `f"Hello {name}"`
- Pathlib for file paths: `Path("data") / "file.json"`
- Dataclasses for data structures
- List/dict comprehensions (when readable)
- `enumerate()` instead of manual indexing
- `get()` with defaults for dict access

### Avoid
- String concatenation with `+`
- Manual file closing (use context managers)
- Mutable default arguments: `def foo(items=[]):`
- `from module import *`

## Performance

- Use generators for large datasets
- Cache expensive operations with `functools.lru_cache`
- Use `requests.Session()` for multiple HTTP requests
- Profile before optimizing (`cProfile`, `timeit`)

## Testing

```python
# test_geocode.py
import pytest

def test_geocode_valid_query():
    result = geocode("Times Square, NYC")
    assert len(result) > 0
    assert "lat" in result[0]
    assert "lon" in result[0]

def test_geocode_empty_query():
    result, status = geocode("")
    assert status == 400
    assert "error" in result
```

---

# Response Format

## Always Provide a Summary

After completing any task that modifies code or files, always provide a summary that includes:

1. **What was done**: Brief description of the changes made
2. **Key line changes**: Specific files and line numbers that were modified (e.g., `app.js:42-45`)
3. **Key concepts**: Important architectural decisions, patterns used, or concepts the user should understand
4. **Checklist to verify**: A short, concrete checklist (3–6 items) of things the user should manually check to confirm the work — phrased as actions in the running app or commands to run, not restatements of the code change. Always include this when work is finished.

Example format:
```
## Summary

**What was done**: Added auto-reload support to the development server

**Key changes**:
- `README.md:20-22`: Changed `npx serve` to `npx live-server --port=3000`
- `CLAUDE.md:50-51`: Updated local development instructions

**Key concepts**:
- live-server watches for file changes and automatically refreshes the browser
- Port 3000 is used to match the existing documentation
```
