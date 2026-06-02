import { useEffect, useState } from "react";
import {
  RouteProvider, WebSocketProvider, GhostPinProvider, GraphSnapProvider,
  ThemeProvider, MapProvider, HeatmapProvider,
} from "./context";
import { TopBar, MapView, ErrorToast, Landing } from "./components";
import { PasscodeGate } from "./components/PasscodeGate/PasscodeGate";
import { useRoute } from "./context";
import { isLandingHost, isApexHost, subdomainHref } from "./themes";
import { resolveMapSlug, fetchMapConfig, applyMap, detectMapSlugFromUrl } from "./map/runtime";

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
      if (cancelled) return;
      // Preset maps live on their own subdomain — send apex /m/<slug> (or
      // shared/typed) visitors to e.g. bikepaths.cityedit.org.
      if (cfg?.subdomain && isApexHost()) {
        window.location.replace(subdomainHref(cfg.subdomain));
        return;
      }
      if (cfg) applyMap(cfg);
      setReady(true);
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
  // An explicit map in the URL (/m/<slug> or ?map=) means map mode even on the
  // apex/landing host — otherwise clicking a map card just re-rendered Landing.
  if (!detectMapSlugFromUrl() && isLandingHost()) {
    return <Landing />;
  }
  return <MapApp />;
}

export default App;
