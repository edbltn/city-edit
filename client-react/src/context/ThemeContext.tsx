import { createContext, useContext, useMemo, type ReactNode } from "react";
import { detectTheme, type Theme } from "../themes";

const ThemeContext = createContext<Theme | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  // detectTheme() reads hostname once — stable across renders
  const theme = useMemo(() => detectTheme(), []);

  // Update document title to reflect the active theme
  useMemo(() => {
    document.title = `${theme.name} — City Edit`;
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
