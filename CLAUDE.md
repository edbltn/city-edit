# Project Architecture

## Claude Instructions

- **gcloud commands**: Don't run gcloud commands directly. Ask me to run them and I'll provide the output.
- **docker commands**: Run docker commands directly (e.g. `docker compose up --build -d`, `docker compose logs`, etc.) without asking the user.
- **Browser testing**: I have a browser AI helper that can report on status. When you need me to test something, ask questions that this AI can answer (descriptions of screenshots, UI changes that need verification, functionality checks, error messages visible on screen).

## Overview

Desire Path Mapper is a crowdsourced map showing how people actually travel through a city. Users submit their commute routes, which are aggregated and visualized as a heatmap overlay.

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
Routing uses **OSRM** (Open Source Routing Machine) for fast pathfinding and a **Python graph provider** (osmnx) for topology visualization, nearest-node snapping, and reverse geocoding.

- **OSRM router**: `server/osrm_router.py` - HTTP client calling self-hosted OSRM
- **Graph provider**: `server/python_router.py` - Graph topology, snapping, reverse geocoding
- **OSRM config**: `osrm/entrypoint.sh` - Downloads NY state PBF and builds foot profile
- **Graph files**: `server/osm_data/walk_graph.pkl` - Pre-built graph from OSM data
- **Refresh script**: `server/refresh_osm.py` - Downloads OSM data and rebuilds graphs

### Regions
Available regions for graph building:
- `downtown` (default): Battery Park to 34th Street (~6km x 4km)
- `fidi`: Financial District only (~1.6km x 1.5km)
- `manhattan`: Full Manhattan
- `nyc-metro`: Full NYC metro area (high memory)

### Rebuilding Graphs
If graphs are missing or outdated:
```bash
cd server && source env/bin/activate
python refresh_osm.py --region downtown --force
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

```bash
# Terminal 1: Redis
redis-server

# Terminal 2: Flask
cd server && source env/bin/activate && python app.py

# Terminal 3: Client (auto-reloads on file changes)
cd client-react && npm run dev
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
