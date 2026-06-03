// ==========================================================================
// The single vote-cast path
// ==========================================================================
// Both the top-bar route cast and the in-map proposal +/- buttons call
// castVotes(). It is coverage-aware (only touches edges whose state actually
// changes), reversible (clicking the direction you already hold removes the
// vote), optimistic (instant heatmap update via the `optimistic-vote` event
// that GraphLayer applies), and self-healing (rolls back on failure; the
// server's authoritative vtCounts SET reconciles the rest).

import { CONFIG } from "../config";
import { getMapSlug, getPasscodeToken } from "../map/runtime";
import { getVoterId } from "./voterIdentity";
import { coverage, setVotes, type VoteDirection } from "./voteStore";

/** Detail of the `optimistic-vote` window event GraphLayer listens for. */
export interface OptimisticVoteDetail {
  mode: string;
  label: string;
  edgeIds: number[];
  prevDir: number; // -1 | 0 | 1
  newDir: number; // -1 | 0 | 1
}

export interface CastResult {
  ok: boolean;
  /** The direction actually applied: the request direction, or 0 if toggled off. */
  targetDir: VoteDirection | 0;
  /** Edges whose state changed (sent to the server). Empty if nothing to do. */
  changedEdges: number[];
}

export interface TransitionGroup {
  edges: number[];
  prevDir: number;
  newDir: number;
}

export interface VotePlan {
  /** Direction actually applied: the request direction, or 0 when toggling off. */
  targetDir: VoteDirection | 0;
  /** Edge transitions to apply (already excludes no-op edges). */
  groups: TransitionGroup[];
  /** Flattened edges that change (sent to the server). */
  changedEdges: number[];
}

/**
 * Decide what a cast should do given current coverage — the pure heart of the
 * multi-select rule, extracted so it can be unit-tested without network/DOM:
 *   - edges already at `direction` are left untouched (no-op),
 *   - unvoted edges get the vote,
 *   - edges holding the opposite are reversed,
 *   - if EVERY edge already holds `direction`, the whole cast toggles OFF.
 */
export function planVoteChange(
  mode: string,
  edgeIds: number[],
  label: string,
  direction: VoteDirection,
): VotePlan {
  const cov = coverage(mode, edgeIds, label, direction);

  let targetDir: VoteDirection | 0;
  let groups: TransitionGroup[];
  if (cov.unvoted.length === 0 && cov.opposite.length === 0) {
    targetDir = 0; // everything already at target → toggle off (remove)
    groups = [{ edges: cov.atTarget, prevDir: direction, newDir: 0 }];
  } else {
    targetDir = direction;
    groups = [
      { edges: cov.unvoted, prevDir: 0, newDir: direction },
      { edges: cov.opposite, prevDir: -direction, newDir: direction },
    ];
  }
  groups = groups.filter((g) => g.edges.length > 0);
  return { targetDir, groups, changedEdges: groups.flatMap((g) => g.edges) };
}

function dispatchOptimistic(detail: OptimisticVoteDetail) {
  if (typeof window === "undefined" || detail.edgeIds.length === 0) return;
  window.dispatchEvent(new CustomEvent("optimistic-vote", { detail }));
}

/**
 * Cast `direction` for `label` across `edgeIds`.
 *
 * Multi-select semantics (the key invariant): edges you've ALREADY voted in
 * this direction are left untouched; unvoted edges get the vote; edges holding
 * the OPPOSITE are reversed. If every edge already holds this direction, the
 * cast toggles OFF (removes your votes).
 */
export async function castVotes(params: {
  mode: string;
  edgeIds: number[];
  label: string;
  direction: VoteDirection;
}): Promise<CastResult> {
  const { mode, label, direction } = params;
  const edges = [...new Set(params.edgeIds)].filter((e) => e != null);
  if (edges.length === 0 || !label) {
    return { ok: false, targetDir: direction, changedEdges: [] };
  }

  const { targetDir, groups, changedEdges } = planVoteChange(mode, edges, label, direction);
  if (changedEdges.length === 0) {
    return { ok: true, targetDir, changedEdges: [] };
  }

  // Optimistic apply + local store update.
  for (const g of groups) {
    dispatchOptimistic({ mode, label, edgeIds: g.edges, prevDir: g.prevDir, newDir: g.newDir });
    setVotes(mode, g.edges, label, g.newDir as VoteDirection | 0);
  }

  const slug = getMapSlug();
  try {
    const body: Record<string, unknown> = {
      map: slug,
      mode,
      vote_type: label,
      direction: targetDir,
      edge_ids: changedEdges,
      voter_id: getVoterId(),
    };
    const token = getPasscodeToken(slug);
    if (token) body.passcode_token = token;

    const res = await fetch(`${CONFIG.apiUrl}/vote`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (res.status === 401) {
      window.dispatchEvent(new CustomEvent("map-passcode-required", { detail: { slug } }));
      throw new Error("Passcode required");
    }
    if (!res.ok) throw new Error(`Vote failed: ${res.status}`);

    // The server may decline some edges under the per-IP abuse cap, returning
    // them in `capped` (absent from `changed`, with no count broadcast). Roll
    // back our optimistic apply for those so the heatmap/button can't show a
    // vote the server never recorded.
    const result = await res.json();
    const declined = new Set<number>(result?.capped ?? []);
    if (declined.size > 0) {
      for (const g of groups) {
        const edges = g.edges.filter((e) => declined.has(e));
        if (edges.length === 0) continue;
        dispatchOptimistic({ mode, label, edgeIds: edges, prevDir: g.newDir, newDir: g.prevDir });
        setVotes(mode, edges, label, g.prevDir as VoteDirection | 0);
      }
    }
    return { ok: true, targetDir, changedEdges: changedEdges.filter((e) => !declined.has(e)) };
  } catch (err) {
    // Roll back the optimistic apply and the local store so a failed sync can't
    // leave the heatmap or button state showing a vote the server never took.
    for (const g of groups) {
      dispatchOptimistic({ mode, label, edgeIds: g.edges, prevDir: g.newDir, newDir: g.prevDir });
      setVotes(mode, g.edges, label, g.prevDir as VoteDirection | 0);
    }
    console.error("Failed to cast vote:", err);
    return { ok: false, targetDir, changedEdges: [] };
  }
}
