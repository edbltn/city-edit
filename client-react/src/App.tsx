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
  takePasscodeParam, authWithPasscode, getCurrentMap, slugRedirectUrl,
  type MapConfig,
} from "./map/runtime";
import { reportMapLoaded } from "./utils/loadTelemetry";
import { captureSourceTag, withSourceTag } from "./utils/sourceTag";

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

  // The loader's first dismissal IS the user-perceived "map is up" moment —
  // beacon it once for the first-load P99 dashboard (utils/loadTelemetry.ts).
  useEffect(() => {
    if (!isInitialLoading) reportMapLoaded();
  }, [isInitialLoading]);

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
      {getCurrentMap()?.staging && (
        <div className="staging-ribbon" aria-hidden>STAGING</div>
      )}
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
    // Capture ?src= (campaign attribution) BEFORE any redirect or URL rewrite
    // can drop it — the beacon reads it when the loader dismisses.
    captureSourceTag();
    (async () => {
      let resolved = await resolveMapConfig();
      if (cancelled) return;
      // A retired slug (renamed map): follow its DB redirect, keeping deep-link
      // params and merging the redirect's appendQuery (campaign ?src tag).
      if (resolved?.redirect?.toSlug) {
        window.location.replace(slugRedirectUrl(resolved.redirect));
        return;
      }
      // A map with a canonical subdomain (presets + admin-assigned vanity hosts)
      // settles on that host: send apex /m/<slug> and shared/typed visitors to
      // e.g. bikepaths.cityedit.org, preserving any ?slat/?vt deep-link params.
      // Staging serves the same maps at its own (unguessable) host, so the
      // redirect would eject testers to prod — the server flag disables it.
      if (resolved?.subdomain && !resolved.staging) {
        const target = subdomainRedirectUrl(resolved.subdomain);
        if (target) {
          // withSourceTag: the ?src tag was captured+stripped above; carry it
          // across the host hop so the campaign still gets attributed.
          window.location.replace(withSourceTag(target));
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
