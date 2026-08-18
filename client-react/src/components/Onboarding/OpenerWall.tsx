import { useEffect, useMemo, useRef } from "react";
import { createPortal } from "react-dom";
import { iconSrc } from "../../themes";
import { hashLabelToColor, suggestionGlyphSvg } from "../../utils/suggestionIcon";
import { buildTiles, type OpenerTile } from "../../onboarding/tiles";
import type { MapConfig } from "../../map/runtime";
import "./Onboarding.css";

// ==========================================================================
// The wall — a first screen made of half-finished sentences
// ==========================================================================
// Every other way of opening this app starts by explaining it: here is a map,
// here is a heat layer, here is a vote type, now pick one. This one starts by
// asking the user for something they already have — the sentence they would say
// about their own street — and lets the map be the second half of it.
//
// The look is the wordmark, exploded. CITY EDIT is drawn as letters in boxes
// (TopBar's mobile logo); the wall is the same boxes at paragraph size, ruled
// into one continuous 1px grid, with the live map showing faintly through. It is
// a wall of index cards in a monospace city.
//
// It never blocks anything permanently: Escape closes it, so does the line at
// the bottom, and closing it leaves the map exactly as it was.
// ==========================================================================

interface Props {
  map: MapConfig | null;
  onPick: (tile: OpenerTile) => void;
  onDismiss: () => void;
}

/** Cap on the reveal stagger, so the last slip of a 32-tile wall is not still
 *  arriving half a second after the first. */
const STAGGER_MS = 16;
const STAGGER_CAP = 22;

function TileMark({ tile }: { tile: OpenerTile }) {
  if (tile.icon) {
    return <img className="opener-tile-icon" src={iconSrc(tile.icon)} alt="" />;
  }
  // A vote type with no themed icon, or a generic sentence: the same sparkle the
  // map draws for an unthemed proposal, so the wall and the map agree.
  return (
    <span
      className="opener-tile-icon opener-tile-glyph"
      style={{ color: tile.voteType ? hashLabelToColor(tile.voteType) : undefined }}
      dangerouslySetInnerHTML={{ __html: suggestionGlyphSvg("currentColor", 18) }}
    />
  );
}

export function OpenerWall({ map, onPick, onDismiss }: Props) {
  const tiles = useMemo(() => buildTiles(map), [map]);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onDismiss();
    };
    window.addEventListener("keydown", onKey);
    // The wall owns the screen while it is up; the map underneath keeps its own
    // scroll/zoom, but the page must not scroll behind it (iOS rubber-band).
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [onDismiss]);

  useEffect(() => {
    ref.current?.focus();
  }, []);

  return createPortal(
    <div
      className="opener-wall"
      role="dialog"
      aria-modal="true"
      aria-label="Finish a sentence"
      ref={ref}
      tabIndex={-1}
    >
      <div className="opener-wall-inner">
        <header className="opener-head">
          <p className="opener-kicker">{map?.name ?? "City Edit"}</p>
          <h1 className="opener-title">Finish a sentence.</h1>
          <p className="opener-sub">
            Pick the one you'd actually say out loud. You'll point at the place on
            the map next — that's the whole thing.
          </p>
        </header>

        <div className="opener-grid">
          {tiles.map((tile, i) => (
            <button
              key={tile.id}
              type="button"
              className="opener-tile"
              style={{ animationDelay: `${Math.min(i, STAGGER_CAP) * STAGGER_MS}ms` }}
              onClick={() => onPick(tile)}
            >
              <TileMark tile={tile} />
              <span className="opener-tile-text">{tile.text}</span>
              <span className="opener-tile-foot">
                <span className="opener-tile-kind">
                  {tile.voteType
                    ? `${tile.pointType === "route" ? "a stretch" : "a spot"} · ${tile.voteType}`
                    : "your own words"}
                </span>
                <span className="opener-tile-go" aria-hidden>
                  →
                </span>
              </span>
            </button>
          ))}
        </div>

        <footer className="opener-foot">
          <button type="button" className="opener-skip" onClick={onDismiss}>
            or just look at the map →
          </button>
        </footer>
      </div>
    </div>,
    document.body
  );
}
