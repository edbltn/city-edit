// Injected via addInitScript BEFORE any app code runs.
// Records:
//  - rAF frame timestamps (window.__perf.frames)
//  - first heatmap paint: the client stamps `first-heat-apply` via
//    window.__perf.mark() the first time MapLibreBackground diff-applies
//    block vote heat as feature-state (feature-state isn't introspectable
//    from outside, so polling a GL source — the perf-interim approach —
//    can't see it; the explicit mark is the only reliable signal)
//  - map zoom events from the Leaflet camera owner (window.__lmap, exposed
//    by MapView in dev builds — MapLibre is a synced background, Leaflet
//    owns the camera) — recorded under the legacy names
//    'zoomanim-start'/'zoomanim-end' so measure.mjs windows keep working
(() => {
  const perf = {
    frames: [],
    canvasOps: [], // legacy (canvas renderer); stays empty on feature-state builds
    mapEvents: [], // {t, type}
    firstStrokeT: null, // first heatmap paint (name kept for measure.mjs)
    marks: [],     // {t, label} set by the harness via __perfMark / app via mark()
  };
  // App-side hook: MapLibreBackground calls window.__perf?.mark('first-heat-apply')
  // (guarded, zero-cost when this script isn't injected).
  perf.mark = (label) => {
    const t = performance.now();
    perf.marks.push({ t, label });
    if (label === "first-heat-apply" && perf.firstStrokeT === null) {
      perf.firstStrokeT = t;
    }
  };
  window.__perf = perf;
  window.__perfMark = (label) => perf.mark(label);

  // --- Leaflet map hookup (window.__lmap appears after app bootstrap; only
  // exposed in dev builds. Re-attach if the identity ever changes.) ---
  let attachedMap = null;
  function attachMap() {
    const m = window.__lmap;
    if (!m || m === attachedMap) return;
    attachedMap = m;
    m.on("zoomstart", () =>
      perf.mapEvents.push({ t: performance.now(), type: "zoomanim-start" }));
    m.on("zoomend", () =>
      perf.mapEvents.push({ t: performance.now(), type: "zoomanim-end" }));
  }

  // --- rAF frame recorder (bounded ring) ---
  const MAX_FRAMES = 50000;
  function frameLoop(t) {
    perf.frames.push(t);
    if (perf.frames.length > MAX_FRAMES) perf.frames.splice(0, 10000);
    attachMap();
    requestAnimationFrame(frameLoop);
  }
  requestAnimationFrame(frameLoop);
})();
