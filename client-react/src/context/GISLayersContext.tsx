import {
  createContext,
  useContext,
  useState,
  useCallback,
  useMemo,
  type ReactNode,
} from "react";

export type GISLayerType = "crosswalks" | "stopSigns" | "trafficSignals" | "trees";

interface GISLayerState {
  visible: boolean;
  loading: boolean;
  data: GeoJSON.FeatureCollection | null;
}

interface GISLayersContextValue {
  layers: Record<GISLayerType, GISLayerState>;
  toggleLayer: (layer: GISLayerType) => void;
  setLayerData: (layer: GISLayerType, data: GeoJSON.FeatureCollection) => void;
  mergeLayerData: (layer: GISLayerType, data: GeoJSON.FeatureCollection) => void;
  setLayerLoading: (layer: GISLayerType, loading: boolean) => void;
}

const defaultLayerState: GISLayerState = {
  visible: false,
  loading: false,
  data: null,
};

const GISLayersContext = createContext<GISLayersContextValue | null>(null);

export function GISLayersProvider({ children }: { children: ReactNode }) {
  const [layers, setLayers] = useState<Record<GISLayerType, GISLayerState>>({
    crosswalks: { ...defaultLayerState },
    stopSigns: { ...defaultLayerState },
    trafficSignals: { ...defaultLayerState },
    trees: { ...defaultLayerState },
  });

  const toggleLayer = useCallback((layer: GISLayerType) => {
    setLayers((prev) => ({
      ...prev,
      [layer]: {
        ...prev[layer],
        visible: !prev[layer].visible,
      },
    }));
  }, []);

  const setLayerData = useCallback(
    (layer: GISLayerType, data: GeoJSON.FeatureCollection) => {
      setLayers((prev) => ({
        ...prev,
        [layer]: {
          ...prev[layer],
          data,
          loading: false,
        },
      }));
    },
    []
  );

  // Merge new data with existing cached data (deduplicating by feature ID)
  const mergeLayerData = useCallback(
    (layer: GISLayerType, newData: GeoJSON.FeatureCollection) => {
      setLayers((prev) => {
        const existing = prev[layer].data;
        if (!existing) {
          // No existing data, just set the new data
          return {
            ...prev,
            [layer]: {
              ...prev[layer],
              data: newData,
              loading: false,
            },
          };
        }

        // Merge features, deduplicating by ID
        const existingIds = new Set(
          existing.features.map((f) => f.properties?.id)
        );
        const newFeatures = newData.features.filter(
          (f) => !existingIds.has(f.properties?.id)
        );

        const mergedData: GeoJSON.FeatureCollection = {
          type: "FeatureCollection",
          features: [...existing.features, ...newFeatures],
        };

        return {
          ...prev,
          [layer]: {
            ...prev[layer],
            data: mergedData,
            loading: false,
          },
        };
      });
    },
    []
  );

  const setLayerLoading = useCallback((layer: GISLayerType, loading: boolean) => {
    setLayers((prev) => ({
      ...prev,
      [layer]: {
        ...prev[layer],
        loading,
      },
    }));
  }, []);

  const value = useMemo(
    () => ({
      layers,
      toggleLayer,
      setLayerData,
      mergeLayerData,
      setLayerLoading,
    }),
    [layers, toggleLayer, setLayerData, mergeLayerData, setLayerLoading]
  );

  return (
    <GISLayersContext.Provider value={value}>
      {children}
    </GISLayersContext.Provider>
  );
}

export function useGISLayers(): GISLayersContextValue {
  const context = useContext(GISLayersContext);
  if (!context) {
    throw new Error("useGISLayers must be used within a GISLayersProvider");
  }
  return context;
}
