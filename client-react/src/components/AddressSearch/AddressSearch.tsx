import { type JSX, useState, useCallback, useRef, useEffect, memo } from "react";
import { CONFIG } from "../../config";
import { withMap } from "../../map/runtime";
import type { LatLng } from "../../types";
import "./AddressSearch.css";

const MIN_QUERY_LENGTH = 3;
const DEBOUNCE_MS = 400;

interface GeocodeResult {
  lat: number;
  lon: number;
  display_name: string;
}

interface AddressSearchProps {
  onSelect: (coords: LatLng, address: string) => void;
  onClose: () => void;
  placeholder?: string;
  accentColor?: string;
}

function highlightMatch(text: string, query: string): (string | JSX.Element)[] {
  if (!query) return [text];
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return [text];

  const lowerText = text.toLowerCase();
  const marks: boolean[] = new Array(text.length).fill(false);
  for (const word of words) {
    let start = 0;
    while (start < lowerText.length) {
      const idx = lowerText.indexOf(word, start);
      if (idx === -1) break;
      for (let i = idx; i < idx + word.length; i++) marks[i] = true;
      start = idx + 1;
    }
  }

  const parts: (string | JSX.Element)[] = [];
  let i = 0;
  while (i < text.length) {
    const isBold = marks[i];
    let j = i;
    while (j < text.length && marks[j] === isBold) j++;
    const slice = text.slice(i, j);
    parts.push(isBold ? <strong key={i}>{slice}</strong> : slice);
    i = j;
  }
  return parts;
}

export const AddressSearch = memo(function AddressSearch({
  onSelect,
  onClose,
  placeholder = "Search address...",
  accentColor = "var(--color-start)",
}: AddressSearchProps) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<GeocodeResult[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const [hasSearched, setHasSearched] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const abortRef = useRef<AbortController | undefined>(undefined);

  useEffect(() => {
    const id = requestAnimationFrame(() => inputRef.current?.focus());
    return () => cancelAnimationFrame(id);
  }, []);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose]);

  const fetchResults = useCallback(async (q: string) => {
    // The geocode backend (Photon) is a shared public service; debounce and a
    // min length keep request volume down. Don't search on a stray keystroke.
    if (q.trim().length < MIN_QUERY_LENGTH) {
      setResults([]);
      setHasSearched(false);
      return;
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setIsLoading(true);
    try {
      const res = await fetch(
        withMap(`${CONFIG.apiUrl}/geocode?q=${encodeURIComponent(q)}`),
        { signal: controller.signal }
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setResults(data.results || []);
      setHasSearched(true);
      setHighlightedIndex(0);
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        setResults([]);
        setHasSearched(true);
      }
    } finally {
      if (!controller.signal.aborted) {
        setIsLoading(false);
      }
    }
  }, []);

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const value = e.target.value;
      setQuery(value);
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => fetchResults(value), DEBOUNCE_MS);
    },
    [fetchResults]
  );

  const handleSelect = useCallback(
    (result: GeocodeResult) => {
      onSelect({ lat: result.lat, lng: result.lon }, result.display_name);
    },
    [onSelect]
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setHighlightedIndex((prev) => Math.min(prev + 1, results.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setHighlightedIndex((prev) => Math.max(prev - 1, 0));
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (results.length > 0 && results[highlightedIndex]) {
          handleSelect(results[highlightedIndex]);
        }
      } else if (e.key === "Escape") {
        onClose();
      }
    },
    [results, highlightedIndex, handleSelect, onClose]
  );

  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      abortRef.current?.abort();
    };
  }, []);

  const showDropdown = hasSearched || isLoading;

  return (
    <div ref={containerRef} className="address-search">
      <input
        ref={inputRef}
        type="text"
        className="address-search-input"
        value={query}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        spellCheck={false}
        autoComplete="off"
        autoCorrect="off"
        autoCapitalize="off"
        style={{ borderBottomColor: accentColor }}
      />
      {showDropdown && (
        <div className="autocomplete-results active">
          {isLoading && results.length === 0 && (
            <div className="autocomplete-item autocomplete-loading">
              Searching...
            </div>
          )}
          {!isLoading && results.length === 0 && hasSearched && (
            <div className="autocomplete-item autocomplete-empty">
              No results found
            </div>
          )}
          {results.map((result, index) => (
            <div
              key={`${result.lat}-${result.lon}-${index}`}
              className={`autocomplete-item${index === highlightedIndex ? " selected" : ""}`}
              onMouseDown={() => handleSelect(result)}
              onMouseEnter={() => setHighlightedIndex(index)}
            >
              {highlightMatch(result.display_name, query)}
            </div>
          ))}
        </div>
      )}
    </div>
  );
});
