// Injected via addInitScript BEFORE any app code runs.
// Records:
//  - rAF frame timestamps (window.__perf.frames)
//  - first heatmap paint: the frame after the graph-live GL source is primed
//    with voted edges (post-migration the heat renders as MapLibre layers;
//    the old 2D-canvas stroke hooks died with the canvas renderer)
//  - map zoom events from the MapLibre map (window.__mlMap, exposed by
//    src/map/maplibreInstance.ts) — recorded under the legacy names
//    'zoomanim-start'/'zoomanim-end' so measure.mjs windows keep working
(() => {
  const perf = {
    frames: [],
    canvasOps: [], // legacy (canvas renderer); stays empty on GL builds
    mapEvents: [], // {t, type}
    firstStrokeT: null, // first heatmap paint (name kept for measure.mjs)
    marks: [],     // {t, label} set by the harness via __perfMark
  };
  window.__perf = perf;
  window.__perfMark = (label) => perf.marks.push({ t: performance.now(), label });

  // --- MapLibre map hookup (the instance appears after app bootstrap, and is
  // recreated on style change — re-attach when the identity changes) ---
  let attachedMap = null;
  function attachMap() {
    const m = window.__mlMap;
    if (!m || m === attachedMap) return;
    attachedMap = m;
    m.on("zoomstart", () =>
      perf.mapEvents.push({ t: performance.now(), type: "zoomanim-start" }));
    m.on("zoomend", () =>
      perf.mapEvents.push({ t: performance.now(), type: "zoomanim-end" }));
  }

  // First heat paint: the graph-live GeoJSON source holds voted edges once
  // GraphLayer pushes them (setData); the next painted frame is the first
  // frame where heat is visible.
  let heatPrimed = false;
  function checkHeat(t) {
    if (perf.firstStrokeT !== null) return;
    const m = window.__mlMap;
    if (!m) return;
    try {
      if (heatPrimed) {
        // One frame after the data landed ≈ first painted heat frame.
        perf.firstStrokeT = t;
        return;
      }
      const data = m.getSource("graph-live")?.serialize()?.data;
      if (data?.features?.length) heatPrimed = true;
    } catch {
      // style not loaded yet
    }
  }

  // --- rAF frame recorder (bounded ring) ---
  const MAX_FRAMES = 50000;
  function frameLoop(t) {
    perf.frames.push(t);
    if (perf.frames.length > MAX_FRAMES) perf.frames.splice(0, 10000);
    attachMap();
    checkHeat(t);
    requestAnimationFrame(frameLoop);
  }
  requestAnimationFrame(frameLoop);
})();
