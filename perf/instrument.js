// Injected via addInitScript BEFORE any app code runs.
// Records:
//  - rAF frame timestamps (window.__perf.frames)
//  - draw ops on the GraphLayer heatmap canvas (clearRect / stroke batches)
//  - Leaflet map events (movestart/moveend/zoomstart/zoomend) once the map exists
(() => {
  const perf = {
    frames: [],
    canvasOps: [], // {t, op} op: 'clear' | 'strokeBatch'
    mapEvents: [], // {t, type}
    firstStrokeT: null,
    marks: [],     // {t, label} set by the harness via __perfMark
  };
  window.__perf = perf;
  window.__perfMark = (label) => perf.marks.push({ t: performance.now(), label });

  // --- rAF frame recorder (bounded ring) ---
  const MAX_FRAMES = 50000;
  function frameLoop(t) {
    perf.frames.push(t);
    if (perf.frames.length > MAX_FRAMES) perf.frames.splice(0, 10000);
    requestAnimationFrame(frameLoop);
  }
  requestAnimationFrame(frameLoop);

  // --- GraphLayer canvas instrumentation ---
  // The heatmap canvas is created with className "graph-layer". We wrap its 2D
  // context methods to log clears and stroke batches (coalesce strokes within
  // the same task via microtask flag to avoid recording thousands of ops).
  const origGetContext = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function (...args) {
    const ctx = origGetContext.apply(this, args);
    if (!ctx || args[0] !== "2d") return ctx;
    const canvas = this;
    if (canvas.__perfWrapped) return ctx;
    // Wrap lazily: className may be set after getContext, so check on each op.
    canvas.__perfWrapped = true;

    const isGraph = () => canvas.className === "graph-layer";

    const origClear = ctx.clearRect.bind(ctx);
    ctx.clearRect = function (...a) {
      if (isGraph()) perf.canvasOps.push({ t: performance.now(), op: "clear" });
      return origClear(...a);
    };

    let strokePending = false;
    const origStroke = ctx.stroke.bind(ctx);
    ctx.stroke = function (...a) {
      if (isGraph() && !strokePending) {
        strokePending = true;
        const t = performance.now();
        perf.canvasOps.push({ t, op: "strokeBatch" });
        if (perf.firstStrokeT === null) perf.firstStrokeT = t;
        queueMicrotask(() => { strokePending = false; });
      }
      return origStroke(...a);
    };
    return ctx;
  };

  // --- Leaflet map events (poll until the map container carries the instance) ---
  // MapView stores the map via setMapInstance; we can't reach module scope, so
  // we hook Leaflet's Map prototype instead once L is importable. Vite bundles
  // ESM, so intercept via the events fired on the container element is not
  // possible; instead poll for a leaflet container and use its _leaflet_map ref
  // if present (react-leaflet attaches none), so fall back to DOM classlist:
  // .leaflet-container gets 'leaflet-zoom-anim' class during zoom animation.
  const seen = { zoomAnim: false };
  function pollZoomClass() {
    const el = document.querySelector(".leaflet-container");
    if (el) {
      const z = el.classList.contains("leaflet-zoom-anim");
      if (z !== seen.zoomAnim) {
        seen.zoomAnim = z;
        perf.mapEvents.push({ t: performance.now(), type: z ? "zoomanim-start" : "zoomanim-end" });
      }
    }
    requestAnimationFrame(pollZoomClass);
  }
  requestAnimationFrame(pollZoomClass);
})();
