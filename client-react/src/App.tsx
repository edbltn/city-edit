import { useEffect, useState } from "react";
import {
  RouteProvider, WebSocketProvider, GhostPinProvider, GraphSnapProvider,
  ThemeProvider, MapProvider, HeatmapProvider,
} from "./context";
import { TopBar, MapView, ErrorToast, Landing } from "./components";
import { PasscodeGate } from "./components/PasscodeGate/PasscodeGate";
import { useRoute } from "./context";
import { isLandingHost } from "./themes";
import { resolveMapSlug, fetchMapConfig, applyMap } from "./map/runtime";

function AppContent() {
  const { error, clearError } = useRoute();

  return (
    <div id="app">
      <TopBar />
      <main id="map">
        <MapView />
      </main>
      <ErrorToast message={error} onDismiss={clearError} />
      <PasscodeGate />
    </div>
  );
}

/**
 * Resolve + load the active map before mounting the map subtree. Fetching the
 * map config rebinds CONFIG to the right city, so all downstream consumers
 * (bounds, tiles, camera) read the correct values on first render.
 */
function MapApp() {
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const cfg = await fetchMapConfig(resolveMapSlug());
      if (!cancelled && cfg) applyMap(cfg);
      if (!cancelled) setReady(true);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (!ready) return <div className="map-bootstrap" />;

  return (
    <ThemeProvider>
      <MapProvider>
        <RouteProvider>
          <WebSocketProvider>
            <GhostPinProvider>
              <GraphSnapProvider>
                <HeatmapProvider>
                  <AppContent />
                </HeatmapProvider>
              </GraphSnapProvider>
            </GhostPinProvider>
          </WebSocketProvider>
        </RouteProvider>
      </MapProvider>
    </ThemeProvider>
  );
}

function App() {
  if (isLandingHost()) {
    return <Landing />;
  }
  return <MapApp />;
}

export default App;
