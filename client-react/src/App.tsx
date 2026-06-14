import { useCallback, useEffect, useState } from "react";
import {
  RouteProvider, WebSocketProvider, GhostPinProvider, GraphSnapProvider,
  ThemeProvider, MapProvider, HeatmapProvider,
} from "./context";
import { TopBar, MapView, ErrorToast, Landing, ErrorBoundary } from "./components";
import { PasscodeGate } from "./components/PasscodeGate/PasscodeGate";
import { useRoute, useHeatmap } from "./context";
import { isLandingHost, subdomainRedirectUrl } from "./themes";
import {
  resolveMapConfig, fetchMapConfig, applyMap, detectMapSlugFromUrl,
  takePasscodeParam, authWithPasscode,
  type MapConfig,
} from "./map/runtime";

/** Full-screen "Loading..." splash with the ASCII (| / - \) spinner. */
function FullScreenLoader() {
  const year = new Date().getFullYear();
  return (
    <div className="map-bootstrap">
      <span className="spinner map-bootstrap-spinner" aria-hidden />
      <div className="map-bootstrap-label">Loading...</div>
      <footer className="map-bootstrap-footer">
        <span>© {year} City Edit. All rights reserved.</span>
        <a href="https://sphericalharmonics.org/" className="map-bootstrap-footer-link">
          sphericalharmonics.org
        </a>
      </footer>
    </div>
  );
}

function AppContent() {
  const { error, clearError } = useRoute();
  // Keep the splash up until BOTH the base map and the vote heatmap have first
  // painted, so the map is never revealed half-loaded (slow on mobile).
  const { isInitialLoading } = useHeatmap();

  return (
    <div id="app">
      <TopBar />
      <main id="map">
        {/* A render crash here is almost always a poisoned graph cache; the
            boundary clears it and reloads once instead of looping forever. */}
        <ErrorBoundary>
          <MapView />
        </ErrorBoundary>
      </main>
      <ErrorToast message={error} onDismiss={clearError} />
      <PasscodeGate />
      {isInitialLoading && <FullScreenLoader />}
    </div>
  );
}

/**
 * Resolve + load the active map before mounting the map subtree. Fetching the
 * map config rebinds CONFIG to the right city, so all downstream consumers
 * (bounds, tiles, camera) read the correct values on first render.
 */
function MapApp() {
  // undefined = still resolving; a config with `locked` means the passcode gate
  // must clear before the map subtree mounts.
  const [cfg, setCfg] = useState<MapConfig | null | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      let resolved = await resolveMapConfig();
      if (cancelled) return;
      // A map with a canonical subdomain (presets + admin-assigned vanity hosts)
      // settles on that host: send apex /m/<slug> and shared/typed visitors to
      // e.g. bikepaths.cityedit.org, preserving any ?slat/?vt deep-link params.
      if (resolved?.subdomain) {
        const target = subdomainRedirectUrl(resolved.subdomain);
        if (target) {
          window.location.replace(target);
          return;
        }
      }
      // Locked map: a shareable ?passcode=… link auto-unlocks it (consumed AFTER
      // the subdomain redirect above, so the redirect carries the param to the
      // canonical host). Otherwise we may already hold a token (returning visitor
      // / subdomain load that couldn't send it). Either way, retry by slug, which
      // carries the header.
      if (resolved?.locked && resolved.slug) {
        const urlPasscode = takePasscodeParam();
        if (urlPasscode) {
          await authWithPasscode(resolved.slug, urlPasscode);
          if (cancelled) return;
        }
        const unlocked = await fetchMapConfig(resolved.slug);
        if (cancelled) return;
        if (unlocked && !unlocked.locked) resolved = unlocked;
      }
      if (resolved && !resolved.locked) applyMap(resolved);
      setCfg(resolved);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // After a successful unlock the token is stored; re-fetch the full config
  // (sent with the header) and proceed to mount the map.
  const handleUnlock = useCallback(async (slug: string) => {
    const full = await fetchMapConfig(slug);
    if (full && !full.locked) {
      applyMap(full);
      setCfg(full);
    }
  }, []);

  if (cfg === undefined) {
    return <FullScreenLoader />;
  }

  if (cfg?.locked) {
    return (
      <div className="map-bootstrap">
        <PasscodeGate slug={cfg.slug} onUnlock={handleUnlock} />
      </div>
    );
  }

  return (
    <ThemeProvider>
      <MapProvider>
        {/* GraphSnapProvider must sit above RouteProvider: RouteContext reads the
            point→edge snap resolver from it for point/route casts. */}
        <GraphSnapProvider>
          <RouteProvider>
            <WebSocketProvider>
              <GhostPinProvider>
                <HeatmapProvider>
                  <AppContent />
                </HeatmapProvider>
              </GhostPinProvider>
            </WebSocketProvider>
          </RouteProvider>
        </GraphSnapProvider>
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
