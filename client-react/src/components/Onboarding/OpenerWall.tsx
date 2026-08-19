import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { iconSrc } from "../../themes";
import { hashLabelToColor, suggestionGlyphSvg } from "../../utils/suggestionIcon";
import type { OpenerTile } from "../../onboarding/tiles";
import type { MapConfig } from "../../map/runtime";
import "./Onboarding.css";

// ==========================================================================
// The wall — the first screen asks what needs fixing
// ==========================================================================
// Every other way of opening this app starts by explaining it: here is a map,
// here is a heat layer, here is a vote type, now pick one. This one starts by
// asking the user for something they already have — what is wrong with their
// own street — in their words rather than in the work-order phrasing the vote
// types are written in.
//
// Every pill is one of the map's REAL vote types (onboarding/tiles.ts): picking
// one is picking that type, so the pill says what the vote will be. The vote
// type's own name is deliberately NOT printed underneath it. The sentence is the
// whole affordance, and a label under every pill was both noise and a second,
// worse name for the same thing.
//
// The arrangement is a tag flow, not a grid: each pill is as wide as its own
// sentence, they wrap left-aligned with a ragged edge, and the variation in
// width IS the texture — a wall of equal cells reads as a form. Square corners,
// like the rest of the app, at every width.
//
// It never blocks anything permanently: Escape closes it, so does the line at
// the bottom, and closing it leaves the map exactly as it was.
// ==========================================================================

interface Props {
  map: MapConfig | null;
  /** Built by the caller, which also needs to know whether there are any: a map
   *  that authors no vote types has no wall (onboarding/tiles.ts). */
  tiles: OpenerTile[];
  onPick: (tile: OpenerTile) => void;
  onDismiss: () => void;
}

/** Cap on the reveal stagger, so the last pill of a 32-tile wall is not still
 *  arriving half a second after the first. */
const STAGGER_MS = 16;
const STAGGER_CAP = 22;

function TileMark({ tile }: { tile: OpenerTile }) {
  if (tile.icon) {
    return <img className="opener-tile-icon" src={iconSrc(tile.icon)} alt="" />;
  }
  // A vote type with no themed icon: the same sparkle the map draws for an
  // unthemed proposal, so the wall and the map agree.
  return (
    <span
      className="opener-tile-icon opener-tile-glyph"
      style={{ color: hashLabelToColor(tile.voteType) }}
      dangerouslySetInnerHTML={{ __html: suggestionGlyphSvg("currentColor", 16) }}
    />
  );
}

export function OpenerWall({ map, tiles, onPick, onDismiss }: Props) {
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
      aria-label="What needs fixing?"
      ref={ref}
      tabIndex={-1}
    >
      <div className="opener-wall-inner">
        <header className="opener-head">
          <p className="opener-kicker">{map?.name ?? "City Edit"}</p>
          <h1 className="opener-title">What needs fixing?</h1>
        </header>

        <div className="opener-pills">
          {tiles.map((tile, i) => (
            <button
              key={tile.id}
              type="button"
              className="opener-tile"
              style={{ animationDelay: `${Math.min(i, STAGGER_CAP) * STAGGER_MS}ms` }}
              onClick={() => onPick(tile)}
              title={tile.voteType}
            >
              <TileMark tile={tile} />
              <span className="opener-tile-text">{tile.text}</span>
            </button>
          ))}
        </div>

        <footer className="opener-foot">
          <button type="button" className="opener-skip" onClick={onDismiss}>
            or just take me to the map
          </button>
        </footer>
      </div>
    </div>,
    document.body
  );
}
