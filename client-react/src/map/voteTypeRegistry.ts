// ==========================================================================
// Vote-type registry — what this map is actually showing
// ==========================================================================
// One list answering "which vote types exist on this map, and how much support
// does each carry". It backs the legend/vote-type panel (VoteTypeSelector) and
// the topbar's filtered-state readout, and it is what makes a just-cast custom
// vote type searchable immediately instead of only after the next map-config
// fetch (the server reports custom types back via MapConfig.searchVoteTypes,
// which the client holds as a snapshot taken at load).
//
// Three sources merge here:
//   1. the map's authored vote types            (MapConfig.voteTypes — always listed)
//   2. every label carrying votes right now     (net totals published by GraphLayer)
//   3. labels cast during THIS session          (registerVoteTypeLabel, from castVote)
// plus MapConfig.searchVoteTypes, which is (2) as of page load.
//
// Nets are BLOCK-grain where the map has a block layer — one person, one block,
// mirroring the deduped counts the proposal modal shows — and per-edge nets
// otherwise. A net is signed: a counter-voted type reads negative, exactly as
// its blocks paint on the cold arm of the heat ramp.
// ==========================================================================

import { iconForLabel, pointTypeForLabel } from "../themes";
import type { MapConfig } from "./runtime";

/** label → signed net support (Σ up − Σ down) across the map. */
let nets: ReadonlyMap<string, number> = new Map();
/** Labels first seen this session (cast, or typed and committed), in order. */
const sessionLabels: string[] = [];
let version = 0;
const listeners = new Set<() => void>();

function notify(): void {
  version++;
  for (const fn of [...listeners]) fn();
}

/** Bumps whenever the nets or the session labels change — a snapshot key. */
export function getVoteTypeRegistryVersion(): number {
  return version;
}

export function getVoteTypeNets(): ReadonlyMap<string, number> {
  return nets;
}

/**
 * Replace the live per-type net totals. Called from GraphLayer's batched
 * proposal sweep, which already walks the vote table. No-ops when nothing moved
 * so an unchanged sweep doesn't re-render the panel.
 */
export function publishVoteTypeNets(next: ReadonlyMap<string, number>): void {
  if (next.size === nets.size) {
    let same = true;
    for (const [label, net] of next) {
      if (nets.get(label) !== net) { same = false; break; }
    }
    if (same) return;
  }
  nets = next;
  notify();
}

/**
 * Record a label the user just committed to (a cast, or a custom suggestion).
 * Makes it searchable and legend-listed before the server round-trips it back
 * in the next MapConfig.
 */
export function registerVoteTypeLabel(label: string): void {
  const trimmed = label.trim();
  if (!trimmed || sessionLabels.includes(trimmed)) return;
  sessionLabels.push(trimmed);
  notify();
}

export function getSessionVoteTypeLabels(): readonly string[] {
  return sessionLabels;
}

export function subscribeVoteTypeRegistry(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/** Test seam: drop all registry state (module-level singleton). */
export function resetVoteTypeRegistry(): void {
  nets = new Map();
  sessionLabels.length = 0;
  notify();
}

// ── Legend composition ─────────────────────────────────────────────────────

export interface VoteTypeLegendEntry {
  label: string;
  /** Themed icon path, or null → render the colorized suggestion glyph. */
  icon: string | null;
  /** Route/point kind, null when unknown (a legacy label nothing flagged). */
  pointType: "route" | "point" | null;
  /** Signed net support on the map; 0 when the type carries no votes yet. */
  net: number;
  /** Has real votes — i.e. this type is drawn somewhere on the map. */
  onMap: boolean;
  /**
   * Selectable as the current cast target. A route-kind type can't be cast on
   * a single point (and vice versa), but it still occupies the legend, because
   * its corridors are on screen and toggling them off is the whole point.
   */
  castable: boolean;
}

/**
 * The map's vote types in legend order: castable-in-this-mode first, then the
 * rest, each preserving discovery order (authored list → types already voted →
 * this session's casts). Every authored type is listed even at zero votes (it
 * is what the map invites you to propose); non-authored types are listed only
 * once they carry votes or you cast one, so the legend stays a description of
 * what's actually drawn rather than a dump of every string ever suggested.
 */
export function buildVoteTypeLegend(
  cfg: MapConfig | null,
  pointType: "route" | "point"
): VoteTypeLegendEntry[] {
  // Station networks vote on fixed points whatever kind their types were
  // authored as — same rule as mapVoteTypesForPointType, so every type is
  // castable there.
  const isStationNetwork = (cfg?.network ?? "streets") !== "streets";

  const ordered: string[] = [];
  const seen = new Set<string>();
  const push = (label: string) => {
    if (!label || seen.has(label)) return;
    seen.add(label);
    ordered.push(label);
  };

  for (const vt of cfg?.voteTypes ?? []) push(vt.label);
  for (const vt of cfg?.searchVoteTypes ?? []) if (nets.has(vt.label)) push(vt.label);
  for (const label of nets.keys()) push(label);
  for (const label of sessionLabels) push(label);

  const authored = new Set((cfg?.voteTypes ?? []).map((vt) => vt.label));
  const entries = ordered
    .map((label): VoteTypeLegendEntry => {
      const net = nets.get(label) ?? 0;
      const kind = pointTypeForLabel(label, cfg?.voteTypes, cfg?.searchVoteTypes);
      return {
        label,
        icon: iconForLabel(label, cfg?.voteTypes),
        pointType: kind,
        net,
        onMap: nets.has(label),
        // Unknown kind stays castable in both modes — the same "never drop an
        // unflagged label" rule the proposal families use.
        castable: isStationNetwork || !kind || kind === pointType,
      };
    })
    // An unauthored label with no votes only exists because this session cast
    // it — keep those; drop nothing else.
    .filter((e) => authored.has(e.label) || e.onMap || sessionLabels.includes(e.label));

  // Stable partition: castable first, discovery order preserved within each half.
  return [...entries.filter((e) => e.castable), ...entries.filter((e) => !e.castable)];
}
