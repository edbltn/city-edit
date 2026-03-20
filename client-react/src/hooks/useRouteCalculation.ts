import { useState, useCallback, useRef } from "react";
import { CONFIG } from "../config";
import type {
  LatLng,
  RouteData,
  DesirePathData,
  RouteResponse,
} from "../types";

interface RouteCalculationState {
  isCalculating: boolean;
  error: string | null;
  routeData: RouteData | null;
  desirePathData: DesirePathData | null;
  desirePathSegments: [number, number][][] | null;
}

interface CalculateParams {
  start: LatLng;
  end: LatLng;
  waypoints?: LatLng[];
}

// Fetch with retry and exponential backoff
async function fetchWithRetry(
  url: string,
  options: RequestInit,
  maxRetries = 5
): Promise<Response> {
  let lastError: Error | null = null;

  for (let attempt = 0; attempt < maxRetries; attempt++) {
    try {
      const response = await fetch(url, options);

      // Retry on server errors (5xx)
      if (response.status >= 500) {
        throw new Error(`Server error: ${response.status}`);
      }

      return response;
    } catch (error) {
      lastError = error as Error;

      // Don't retry on the last attempt
      if (attempt < maxRetries - 1) {
        // Exponential backoff: 1s, 2s, 4s, 8s, 16s
        const delay = Math.pow(2, attempt) * 1000;
        await new Promise((resolve) => setTimeout(resolve, delay));
      }
    }
  }

  throw lastError;
}

export function useRouteCalculation() {
  const [state, setState] = useState<RouteCalculationState>({
    isCalculating: false,
    error: null,
    routeData: null,
    desirePathData: null,
    desirePathSegments: null,
  });

  const requestIdRef = useRef(0);

  const calculateRoute = useCallback(
    async ({ start, end, waypoints }: CalculateParams) => {
      // Increment request ID and capture it for this request
      requestIdRef.current++;
      const requestId = requestIdRef.current;

      setState((prev) => ({
        ...prev,
        isCalculating: true,
        error: null,
      }));

      try {
        const response = await fetchWithRetry(`${CONFIG.apiUrl}/routes`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            start: [start.lat, start.lng],
            end: [end.lat, end.lng],
            waypoints: waypoints?.map((wp) => [wp.lat, wp.lng]) || [],
          }),
        });

        if (!response.ok) {
          throw new Error(`Route calculation failed: ${response.statusText}`);
        }

        const data: RouteResponse = await response.json();

        // Ignore response if a newer request was made
        if (requestId !== requestIdRef.current) {
          return null;
        }

        setState({
          isCalculating: false,
          error: null,
          routeData: data.route,
          desirePathData: data.desire_path,
          desirePathSegments: data.desire_path_segments || null,
        });

        return data;
      } catch (error) {
        // Only update state if this is still the current request
        if (requestId === requestIdRef.current) {
          const errorMessage =
            error instanceof Error ? error.message : "Route calculation failed";
          setState({
            isCalculating: false,
            error: errorMessage,
            routeData: null,
            desirePathData: null,
            desirePathSegments: null,
          });
        }
        return null;
      }
    },
    []
  );

  const clearRoute = useCallback(() => {
    // Invalidate any in-flight requests so they don't overwrite the cleared state
    requestIdRef.current++;
    setState({
      isCalculating: false,
      error: null,
      routeData: null,
      desirePathData: null,
      desirePathSegments: null,
    });
  }, []);

  const clearError = useCallback(() => {
    setState((prev) => ({ ...prev, error: null }));
  }, []);

  const setError = useCallback((message: string) => {
    setState((prev) => ({ ...prev, error: message }));
  }, []);

  return {
    ...state,
    calculateRoute,
    clearRoute,
    clearError,
    setError,
  };
}
