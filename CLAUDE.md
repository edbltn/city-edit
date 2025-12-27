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
