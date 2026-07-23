// Inline version of the crossword-grid logo (formerly public/logo.svg).
// Inlined so the letters render in the app webfont (--font-main / Red Hat
// Mono) — an <img>-embedded SVG can't use page webfonts, so generic
// "monospace" fell back to Menlo on desktop but Courier on iOS.

const CELL = 24;
const GAP = 6;
const STROKE = "#d4d4d4";

const LOGO_FONT = "var(--font-main)";

// One entry per grid cell: [col, row, kind] where kind is a letter,
// "x" (crossed-out box), "empty", or "beta".
type Cell = [number, number, string];

const CELLS: Cell[] = [
  // Row 1: [X] [ ] [ ] [ ] [ ] [X]
  [0, 0, "x"], [1, 0, "empty"], [2, 0, "empty"], [3, 0, "empty"], [4, 0, "empty"], [5, 0, "x"],
  // Row 2: C I T Y [ ] [ ]
  [0, 1, "C"], [1, 1, "I"], [2, 1, "T"], [3, 1, "Y"], [4, 1, "empty"], [5, 1, "empty"],
  // Row 3: [ ] [ ] E D I T
  [0, 2, "empty"], [1, 2, "empty"], [2, 2, "E"], [3, 2, "D"], [4, 2, "I"], [5, 2, "T"],
  // Row 4: [ ] [X] [ ] [ ] [ ] [BETA]
  [0, 3, "empty"], [1, 3, "x"], [2, 3, "empty"], [3, 3, "empty"], [4, 3, "empty"], [5, 3, "beta"],
];

// Boxes rendered at reduced opacity in the original art (the "faded" squares).
const FADED_LETTER_BOXES = new Set(["3,1", "2,2"]); // Y and E

function cellBoxOpacity(col: number, row: number, kind: string): number | undefined {
  if (kind === "empty") return 0.15;
  if (kind === "x" || kind === "beta") return 0.3;
  if (FADED_LETTER_BOXES.has(`${col},${row}`)) return 0.3;
  return undefined;
}

export function Logo({ className }: { className?: string }) {
  return (
    <svg
      viewBox="-5 0 183 118"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      role="img"
      aria-label="City Edit"
    >
      {CELLS.map(([col, row, kind]) => {
        const x = 1 + col * (CELL + GAP);
        const y = 1 + row * (CELL + GAP);
        return (
          <g key={`${col},${row}`}>
            <rect
              x={x} y={y} width={CELL} height={CELL}
              fill="none" stroke={STROKE} strokeWidth="1.5"
              opacity={cellBoxOpacity(col, row, kind)}
            />
            {kind === "x" && (
              <>
                <line x1={x + 4} y1={y + 4} x2={x + 20} y2={y + 20} stroke="#bbb" strokeWidth="2.5" />
                <line x1={x + 20} y1={y + 4} x2={x + 4} y2={y + 20} stroke="#bbb" strokeWidth="2.5" />
              </>
            )}
            {kind === "beta" && (
              <text
                x={x + 12} y={y + 12}
                fontFamily={LOGO_FONT} fontSize="7.5" fontWeight="400" fill={STROKE}
                textAnchor="middle" dominantBaseline="central"
                textLength="18" lengthAdjust="spacingAndGlyphs"
              >
                BETA
              </text>
            )}
            {kind.length === 1 && kind !== "x" && (
              <text
                x={x + 12} y={y + 12}
                fontFamily={LOGO_FONT} fontSize="17" fontWeight="600" fill={STROKE}
                textAnchor="middle" dominantBaseline="central"
              >
                {kind}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}
