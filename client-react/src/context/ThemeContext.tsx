import { createContext, useContext, useMemo, useEffect, type ReactNode } from "react";
import { detectTheme, themeFromMap, mapStyleForTheme, type Theme } from "../themes";
import { heatGradientCss } from "../mapStyles";
import { getCurrentMap } from "../map/runtime";

const ThemeContext = createContext<Theme | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  // The active theme is derived from the resolved map (slug-based). Falls back
  // to subdomain detection only when no map could be loaded.
  const theme = useMemo(() => {
    const map = getCurrentMap();
    return map ? themeFromMap(map) : detectTheme();
  }, []);

  // Update document title to reflect the active theme
  useMemo(() => {
    document.title = `${theme.name} — City Edit`;
  }, [theme]);

  // Apply the theme's map style to the document root. CSS keys palette
  // overrides off [data-basemap]; the accent + heat gradient are injected as
  // custom properties so components and the legend track the active style.
  useEffect(() => {
    const style = mapStyleForTheme(theme);
    const root = document.documentElement;
    root.dataset.theme = theme.id;
    root.dataset.basemap = style.basemap;
    root.style.setProperty("--accent", style.accent);
    root.style.setProperty("--selection", style.selection);
    root.style.setProperty("--heat-gradient", heatGradientCss(style.heat, style.basemap));
  }, [theme]);

  return (
    <ThemeContext.Provider value={theme}>{children}</ThemeContext.Provider>
  );
}

export function useTheme(): Theme {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
  return ctx;
}
