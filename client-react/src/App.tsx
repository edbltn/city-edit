import { RouteProvider, WebSocketProvider, GhostPinProvider, GraphSnapProvider } from "./context";
import { TopBar, MapView, ErrorToast } from "./components";
import { useRoute } from "./context";

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
  return (
    <RouteProvider>
      <WebSocketProvider>
        <GhostPinProvider>
          <GraphSnapProvider>
            <AppContent />
          </GraphSnapProvider>
        </GhostPinProvider>
      </WebSocketProvider>
    </RouteProvider>
  );
}

export default App;
