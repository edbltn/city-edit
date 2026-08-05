/**
 * Style expressions for the POI label layer that are worth testing on their own.
 *
 * They live here rather than inline in the layer definition because MapLibre
 * enforces a rule that is easy to violate and expensive to violate silently:
 * an expression may contain only ONE zoom-based `interpolate`/`step`. Break it
 * and MapLibre rejects the whole layer — arriving as a *non-fatal* style error,
 * so nothing crashes and every place label simply stops rendering.
 */
import type maplibregl from "maplibre-gl";

/**
 * Label type size, in px, ramped by zoom.
 *
 * Mono is wide, so this runs smaller than a sans would — and smaller again than
 * legibility alone would ask for, because this layer has to stay quieter than
 * the vote heat and the top-proposal pins above it.
 *
 * Subway stops get a point more. They are the one category people scan the map
 * FOR rather than merely notice, and 496 of NYC's stations are named after the
 * street above them — so the name is doing real work distinguishing "14th
 * Street" the station from "14th Street" the street label, and it has to
 * survive being read at a glance. Keyed on `kind`, not `cat`: the rest of
 * transit (bus, ferry, rail, airport) stays at the base size, because bumping
 * the whole category would cost placements without buying legibility anywhere
 * it matters.
 *
 * Note the SHAPE: one zoom `interpolate` on the outside, with the per-kind
 * `match` inside each stop. Data-driven stop VALUES are allowed; a second
 * zoom ramp is not — see the module comment, and PLACE_LABEL_STYLE tests.
 */
export const PLACE_LABEL_TEXT_SIZE = [
  "interpolate", ["linear"], ["zoom"],
  11, ["match", ["get", "kind"], "subway", 9.6, 8.6],
  15, ["match", ["get", "kind"], "subway", 10.4, 9.4],
  18, ["match", ["get", "kind"], "subway", 11.5, 10.5],
] as unknown as maplibregl.ExpressionSpecification;

/** Every operator MapLibre treats as a zoom-based curve. */
const ZOOM_CURVE_OPS = new Set(["interpolate", "interpolate-hcl", "interpolate-lab", "step"]);

/**
 * Count the zoom-driven `interpolate`/`step` subexpressions in an expression.
 *
 * MapLibre allows at most one. Exported so the guard can be pointed at any
 * expression we add later, not just the text-size ramp.
 */
export function countZoomCurves(expr: unknown): number {
  if (!Array.isArray(expr)) return 0;
  const [op] = expr;
  const isZoomCurve =
    typeof op === "string" &&
    ZOOM_CURVE_OPS.has(op) &&
    // The input operand is ["zoom"] — for `step` it sits at [1], for the
    // `interpolate` family the interpolation type comes first and it sits at [2].
    JSON.stringify(expr[op === "step" ? 1 : 2]) === '["zoom"]';
  return (isZoomCurve ? 1 : 0) + expr.reduce<number>((n, child) => n + countZoomCurves(child), 0);
}
