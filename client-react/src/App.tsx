import { RouteProvider, WebSocketProvider, GhostPinProvider, GraphSnapProvider, ThemeProvider } from "./context";
import { TopBar, MapView, ErrorToast, Landing } from "./components";
import { useRoute } from "./context";
import { isLandingHost } from "./themes";

function AppContent() {
  const { error, clearError } = useRoute();

  return (
    <div id="app">
      <TopBar />
      <main id="map">
        <MapView />
      </main>
      <ErrorToast message={error} onDismiss={clearError} />
    </div>
  );
}

function App() {
  if (isLandingHost()) {
    return <Landing />;
  }

  return (
    <ThemeProvider>
      <RouteProvider>
        <WebSocketProvider>
          <GhostPinProvider>
            <GraphSnapProvider>
              <AppContent />
            </GraphSnapProvider>
          </GhostPinProvider>
        </WebSocketProvider>
      </RouteProvider>
    </ThemeProvider>
  );
}

export default App;
