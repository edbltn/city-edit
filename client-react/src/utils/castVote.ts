// ==========================================================================
// The single vote-cast path — block-scoped clear-then-cast
// ==========================================================================
// Both the top-bar route cast and the in-map proposal +/- buttons call
// castVotes(). Semantics per docs/three-layer-model.md §4: coverage is computed
// over the selection's TOUCHED BLOCKS (edge-as-singleton fallback when no block
// artifacts exist). Pressing an already-active direction unvotes the whole
// touched-block set; anything else clears my same-type votes across those
// blocks and casts on exactly the selection edges. Optimistic (instant heatmap
// update via the `optimistic-vote` event GraphLayer applies) and self-healing
// (rolls back on failure; the server's authoritative deltas + `cleared`
// response reconcile the rest).

import { CONFIG } from "../config";
import { getMapSlug, getPasscodeToken } from "../map/runtime";
import { singletonBlocks, type TouchedBlock } from "./blockSelection";
import { ensureVoteTypeVisible } from "../map/voteTypeFilter";
import { registerVoteTypeLabel } from "../map/voteTypeRegistry";
import { selectionFromParams } from "../selection/serialize";
import { hasPendingSticker, resolveSticker, takePendingSticker } from "../sticker";
import { dlog, derror } from "./debugLog";
import {
  confirmPendingCast, isLatestPendingCast, registerPendingCast,
  resolvePendingCast, type PendingBlockDelta,
} from "./pendingVotes";
import { getVoterId } from "./voterIdentity";
import {
  getVote,
  setVotes,
  type BlockCoverage,
  type VoteDirection,
} from "./voteStore";

/**
 * Bind the code this visit was scanned from to the proposal the vote landed on.
 *
 * Only fires for a real cast: an unvote (direction 0) is someone taking a vote
 * back, which is no evidence at all about what a code means.
 *
 * The selection comes from the URL rather than from the cast's edge ids,
 * because the URL is its canonical serialization (selection/serialize.ts) and
 * is therefore exactly what a future scan will deep-link to. The WHOLE `w` is
 * taken, not its first waypoint: a route vote is an ordered list, and keeping
 * only the head would quietly demote a corridor proposal to a pin on its start.
 *
 * Map and vote type are read live rather than from what was printed. A scanner
 * can change either before casting, and when they do they are making a
 * decision — the code should mean what they decided.
 *
 * Read at cast time, not at boot: the scanner may well have dragged the pin
 * from where their phone thought they were onto the corner they actually mean.
 */
function pinPendingSticker(targetDir: VoteDirection | 0, label: string): void {
  if (targetDir === 0 || !hasPendingSticker()) return;
  const params = new URLSearchParams(window.location.search);
  const parsed = selectionFromParams(params);
  const point = parsed?.waypoints[0]?.coords;
  const w = params.get("w");
  if (!point || !w) return;
  const code = takePendingSticker();
  if (!code) return;
  const binding = {
    mapSlug: getMapSlug(),
    voteType: label,
    w,
    lat: point.lat,
    lng: point.lng,
  };
  dlog("sticker", `binding ${code} to`, binding);
  void resolveSticker(code, binding, getVoterId());
}

/**
 * Detail of the `optimistic-vote` window event GraphLayer listens for.
 *
 * ONE event per press, carrying every edge transition and the predicted move in
 * each touched block's deduped count. It is deliberately not one event per
 * transition group: the block prediction is a property of the whole press (a
 * clear on edge A and a cast on edge B in the same block cancel out), so
 * splitting it would make each half wrong.
 */
export interface OptimisticVoteDetail {
  mode: string;
  label: string;
  /** Edge transitions, grouped by the direction each edge held before. */
  groups: TransitionGroup[];
  /** Predicted deduped block-count moves — the number the UI actually paints. */
  blockDeltas: PendingBlockDelta[];
}

export interface CastResult {
  ok: boolean;
  /** The direction actually applied: the request direction, or 0 if unvoting. */
  targetDir: VoteDirection | 0;
  /** Edges whose state changed (cast + cleared). Empty if nothing to do. */
  changedEdges: number[];
}

export interface TransitionGroup {
  edges: number[];
  prevDir: number;
  newDir: number;
}

export interface BlockVotePlan {
  /** Direction actually applied: the request direction, or 0 when unvoting. */
  targetDir: VoteDirection | 0;
  /** Selection edges to set to targetDir (fresh casts + reversals; excludes
   *  edges already at targetDir — those are no-ops). */
  castEdges: number[];
  /** My existing same-type votes (in the touched blocks) to remove: everything
   *  on an unvote, everything outside the selection on a cast. */
  clearEdges: number[];
  /** Predicted move in each touched block's DEDUPED count. Empty without real
   *  block ids (singleton fallback / no block layer). */
  blockDeltas: PendingBlockDelta[];
}

/** Whether I hold an up / a down vote anywhere in one block's edges. */
function presenceIn(
  edges: ArrayLike<number>,
  dirOf: (edgeId: number) => VoteDirection | 0,
): { up: boolean; down: boolean } {
  let up = false;
  let down = false;
  for (let i = 0; i < edges.length; i++) {
    const v = dirOf(edges[i]);
    if (v === 1) up = true;
    else if (v === -1) down = true;
    if (up && down) break;
  }
  return { up, down };
}

/**
 * Predict how `changes` (edge → the direction it is about to hold) moves each
 * block's deduped count — the ONE quantity the heatmap paints and the server's
 * confirmation will overwrite.
 *
 * It is not the edge count and it is not their sum. A block counts a person
 * ONCE per (vote type, direction) however many of its edges carry their vote
 * (server: block_votes.py, `bd:` HLEN), so the move is purely a presence
 * boundary: −1 when my last vote of that direction leaves the block, +1 when my
 * first arrives, 0 whenever I was already there. That is what makes a second
 * press on the same corridor worth 0 rather than +1, and a flip worth
 * (+1 down, −1 up) rather than +1 down alone.
 *
 * Blind spot, by design: the server dedupes by COUNTING identity (an IP hash by
 * default), not by device, so a second device behind one IP that already holds
 * the block makes the real move 0 where this predicts ±1. Rare, and the
 * authoritative [up, down] SET in the confirming delta corrects it.
 */
export function blockVoteDeltas(
  mode: string,
  label: string,
  blocks: readonly TouchedBlock[],
  changes: ReadonlyMap<number, VoteDirection | 0>,
): PendingBlockDelta[] {
  const deltas: PendingBlockDelta[] = [];
  if (changes.size === 0) return deltas;
  const before = (e: number) => getVote(mode, e, label);
  const after = (e: number) => changes.get(e) ?? getVote(mode, e, label);
  for (const block of blocks) {
    if (block.key < 0) continue; // singleton stand-in — no block aggregate to move
    const was = presenceIn(block.edges, before);
    const now = presenceIn(block.edges, after);
    const up = (now.up ? 1 : 0) - (was.up ? 1 : 0);
    const down = (now.down ? 1 : 0) - (was.down ? 1 : 0);
    if (up !== 0 || down !== 0) deltas.push({ block: block.key, up, down });
  }
  return deltas;
}

/**
 * Decide what a press should do — the pure heart of the block-scoped rule
 * (docs §4.1–4.2), extracted so it can be unit-tested without network/DOM.
 * `blocks` is the materialized touched blocks; pass `singletonBlocks(edgeIds)`
 * for the no-blocks fallback.
 *
 *   - coverage(direction) === 'all' → UNVOTE: clear every one of my `label`
 *     votes across the touched blocks, cast nothing (wire direction 0).
 *   - else → CLEAR-THEN-CAST: cast `direction` on the selection edges (edges
 *     already there are no-ops; opposites are reversals, NOT clears), and clear
 *     my `label` votes on touched-block edges outside the selection.
 */
export function planBlockVote(params: {
  mode: string;
  edgeIds: number[];
  label: string;
  direction: VoteDirection;
  blocks: readonly TouchedBlock[];
}): BlockVotePlan {
  const { mode, edgeIds, label, direction, blocks } = params;
  const dirOf = (e: number) => getVote(mode, e, label);

  // One pass over the touched blocks gives both halves of the rule: whether
  // every block already holds this direction (so the press is an unvote), and
  // which of my votes live in them (what an unvote or a clear has to remove).
  let atDirection = 0;
  const mine: number[] = [];
  const seen = new Set<number>();
  for (const block of blocks) {
    let holds = false;
    for (let i = 0; i < block.edges.length; i++) {
      const eid = block.edges[i];
      const v = dirOf(eid);
      if (v === direction) holds = true;
      if (v !== 0 && !seen.has(eid)) { seen.add(eid); mine.push(eid); }
    }
    if (holds) atDirection++;
  }
  const active = blocks.length > 0 && atDirection === blocks.length;

  const plan: Omit<BlockVotePlan, "blockDeltas"> = active
    ? { targetDir: 0, castEdges: [], clearEdges: mine.sort((a, b) => a - b) }
    : (() => {
        const selection = new Set(edgeIds);
        return {
          targetDir: direction,
          castEdges: edgeIds.filter((e) => dirOf(e) !== direction),
          clearEdges: mine.filter((e) => !selection.has(e)).sort((a, b) => a - b),
        };
      })();

  const changes = new Map<number, VoteDirection | 0>();
  for (const e of plan.clearEdges) changes.set(e, 0);
  for (const e of plan.castEdges) changes.set(e, plan.targetDir);
  return { ...plan, blockDeltas: blockVoteDeltas(mode, label, blocks, changes) };
}

/** Button render state for a direction (docs §4.1): active iff every touched
 *  block already holds my vote in that direction (pressing = unvote). */
export function voteButtonState(coverage: BlockCoverage, dir: VoteDirection): "active" | "neutral" {
  return (dir === 1 ? coverage.up : coverage.down) === "all" ? "active" : "neutral";
}

function dispatchOptimistic(detail: OptimisticVoteDetail) {
  if (typeof window === "undefined") return;
  if (detail.groups.length === 0 && detail.blockDeltas.length === 0) return;
  window.dispatchEvent(new CustomEvent("optimistic-vote", { detail }));
}

/** Reverse a set of transitions — what a rollback (or a partial decline) applies. */
function invertGroups(groups: readonly TransitionGroup[]): TransitionGroup[] {
  return groups.map((g) => ({ edges: g.edges, prevDir: g.newDir, newDir: g.prevDir }));
}

/**
 * Undo `groups` locally: the block prediction is recomputed against the store
 * as it stands NOW rather than negating the forward one, because a rollback can
 * land after other writes have moved the same blocks, and a negated stale
 * prediction would then be a second wrong number rather than a correction.
 */
function revertGroups(
  mode: string, label: string, blocks: readonly TouchedBlock[],
  groups: readonly TransitionGroup[],
) {
  const inverse = invertGroups(groups);
  const changes = new Map<number, VoteDirection | 0>();
  for (const g of inverse) {
    for (const e of g.edges) changes.set(e, g.newDir as VoteDirection | 0);
  }
  dispatchOptimistic({
    mode, label, groups: inverse,
    blockDeltas: blockVoteDeltas(mode, label, blocks, changes),
  });
  for (const g of inverse) {
    setVotes(mode, g.edges, label, g.newDir as VoteDirection | 0);
  }
}

/**
 * Tell the person their vote did not take. A silent revert is indistinguishable
 * from a bug, so every rollback says something — through the SAME toast the
 * server's own refusals already use (App.tsx listens for `vote-rejected` and
 * feeds it to ErrorToast), rather than a second notification surface.
 */
function announceRollback(message: string) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent("vote-rejected", { detail: { message } }));
}

/** Group edges by their CURRENT stored direction so each optimistic event (and
 *  its rollback) carries an accurate prevDir snapshot. */
function groupByPrevDir(
  mode: string,
  edgeIds: number[],
  label: string,
  newDir: number,
): TransitionGroup[] {
  const byPrev = new Map<number, number[]>();
  for (const e of edgeIds) {
    const prev = getVote(mode, e, label);
    if (prev === newDir) continue; // no-op — nothing to apply or roll back
    let bucket = byPrev.get(prev);
    if (!bucket) byPrev.set(prev, (bucket = []));
    bucket.push(e);
  }
  return [...byPrev.entries()].map(([prevDir, edges]) => ({ edges, prevDir, newDir }));
}

/**
 * Cast `direction` for `label` across `edgeIds` (the selection), with block
 * semantics from `blocks` (the touched blocks' materialized edge lists; omitted
 * → per-edge singleton blocks, identical to the old per-edge behavior).
 *
 * The POST always carries the ORIGINAL selection edges + the resolved direction
 * (0 = unvote); the server expands selection → touched blocks itself and
 * reports any extra edges it unvoted in `cleared`, which we fold back into the
 * local store (counts self-heal via the authoritative delta broadcast).
 */
export async function castVotes(params: {
  mode: string;
  edgeIds: number[];
  label: string;
  direction: VoteDirection;
  blocks?: readonly TouchedBlock[];
  /** Kind of the selection being voted ("route" corridor vs single "point").
   *  Sent so the server can record the kind of a brand-new suggestion label —
   *  the creating cast is the only witness. Optional: casts on existing labels
   *  (proposal pins, route cards) don't need it. */
  pointType?: "route" | "point";
}): Promise<CastResult> {
  const { mode, label, direction } = params;
  const edges = [...new Set(params.edgeIds)].filter((e) => e != null);
  if (edges.length === 0 || !label) {
    return { ok: false, targetDir: direction, changedEdges: [] };
  }

  // Casting is the strongest possible statement that this vote type matters to
  // you, so it always (a) enters the registry — making a brand-new custom label
  // searchable and legend-listed immediately, instead of only after the next
  // map-config fetch reports it back — and (b) clears any legend toggle hiding
  // it. Without (b) you could vote for something and watch the map not change.
  registerVoteTypeLabel(label);
  ensureVoteTypeVisible(label);

  const blocks = params.blocks ?? singletonBlocks(edges);
  const { targetDir, castEdges, clearEdges, blockDeltas } = planBlockVote({
    mode, edgeIds: edges, label, direction, blocks,
  });
  dlog("cast", `press ${direction > 0 ? "+" : "−"} "${label}" →`,
    targetDir === 0 ? "UNVOTE-ALL" : `cast ${targetDir}`,
    { selection: edges.length, blocks: blocks.length,
      cast: castEdges.length, clear: clearEdges.length });

  // Optimistic transitions: clears (prev → 0) then casts (prev → targetDir),
  // grouped by prevDir so rollback can restore the exact prior state.
  const groups = [
    ...groupByPrevDir(mode, clearEdges, label, 0),
    ...groupByPrevDir(mode, castEdges, label, targetDir),
  ];
  const changedEdges = groups.flatMap((g) => g.edges);
  if (changedEdges.length === 0) {
    return { ok: true, targetDir, changedEdges: [] };
  }

  // Optimistic apply, in the press's own tick: ONE event carrying every edge
  // transition and the predicted block moves, then the local store write that
  // flips the button's cast state. Nothing here awaits the network.
  dispatchOptimistic({ mode, label, groups, blockDeltas });
  for (const g of groups) {
    setVotes(mode, g.edges, label, g.newDir as VoteDirection | 0);
  }

  // Register it as in flight BEFORE the request goes out. Until its echo comes
  // back this shields the write from the background refreshes that would
  // otherwise install a server snapshot taken before the vote existed
  // (utils/pendingVotes.ts).
  const pendingEdges = new Map<number, VoteDirection | 0>();
  for (const g of groups) {
    for (const e of g.edges) pendingEdges.set(e, g.newDir as VoteDirection | 0);
  }
  const pendingId = registerPendingCast({
    mode, label, edges: pendingEdges, groups, blockDeltas,
  });

  const slug = getMapSlug();
  // Whether the failure path has already told the user something specific.
  let announced = false;
  try {
    const body: Record<string, unknown> = {
      map: slug,
      mode,
      vote_type: label,
      direction: targetDir,
      edge_ids: edges, // the selection — the server expands to blocks itself
      voter_id: getVoterId(),
    };
    if (params.pointType) body.point_type = params.pointType;
    const token = getPasscodeToken(slug);
    if (token) body.passcode_token = token;

    const res = await fetch(`${CONFIG.apiUrl}/vote`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (res.status === 401) {
      // The passcode gate is the message here; a toast on top would be noise.
      announced = true;
      window.dispatchEvent(new CustomEvent("map-passcode-required", { detail: { slug } }));
      throw new Error("Passcode required");
    }
    if (!res.ok) {
      // Surface the server's OWN message when it sent one, rather than a bare
      // status. The vote-type id-space limit is the case this exists for: the
      // server refuses a NEW vote type whose id would not fit the packed vote
      // key, and a rolled-back optimistic apply with nothing on screen would
      // look exactly like the vote having worked and then vanished.
      const reason = await res.json().catch(() => null);
      if (reason?.error) {
        announced = true;
        announceRollback(reason.error as string);
      }
      throw new Error(reason?.error || `Vote failed: ${res.status}`);
    }

    const result = await res.json();
    dlog("cast", "server:", {
      changed: result?.changed?.length ?? 0, cleared: result?.cleared?.length ?? 0,
      capped: result?.capped?.length ?? 0, evicted: Object.keys(result?.evicted ?? {}).length,
    });

    // The server may decline some edges under the per-IP abuse cap, returning
    // them in `capped` (absent from `changed`, with no count broadcast). Roll
    // back our optimistic apply for those so the heatmap/button can't show a
    // vote the server never recorded.
    const declined = new Set<number>(result?.capped ?? []);
    if (declined.size > 0) {
      const cappedGroups = groups
        .map((g) => ({ ...g, edges: g.edges.filter((e) => declined.has(e)) }))
        .filter((g) => g.edges.length > 0);
      revertGroups(mode, label, blocks, cappedGroups);
      // Dropped from the shield too: the server never took these, so a refresh
      // that omits them is telling the truth, not lagging.
      for (const g of cappedGroups) for (const e of g.edges) pendingEdges.delete(e);
    }

    // At the cap the server took over an existing vote instead of adding one
    // (`evicted`: edge → direction now owned by this device). The vote is real
    // for us — keep the local "you voted" state — but the TOTAL didn't move, so
    // undo the optimistic count bump (no server delta corrects it otherwise).
    const evicted: Record<string, number> = result?.evicted ?? {};
    const evictedGroups = groups
      .map((g) => ({ ...g, edges: g.edges.filter((e) => evicted[String(e)] !== undefined) }))
      .filter((g) => g.edges.length > 0);
    if (evictedGroups.length > 0) {
      // Take the counts back off (the totals never moved) …
      revertGroups(mode, label, blocks, evictedGroups);
      // … then restore MY direction, which is real: the row is now ours.
      for (const g of evictedGroups) {
        for (const e of g.edges) {
          const dir = evicted[String(e)] as VoteDirection | 0;
          setVotes(mode, [e], label, dir);
          pendingEdges.set(e, dir);
        }
      }
    }

    // The server expanded the selection to its touched blocks and reports every
    // edge it unvoted beyond the selection in `cleared`. Our optimistic plan
    // usually predicted these, but the server is authoritative (another tab may
    // hold votes this store never saw) — fold them in. Counts self-heal via the
    // authoritative [up, down] delta broadcast, so no optimistic event here.
    const cleared: number[] = Array.isArray(result?.cleared) ? result.cleared : [];
    if (cleared.length > 0) {
      setVotes(mode, cleared, label, 0);
    }

    // If this visit came from a scanned code that had never been bound, THIS is
    // the moment it acquires a meaning — a cast vote, not a shared coordinate,
    // is what earns a code its proposal.
    pinPendingSticker(targetDir, label);

    // Written and accepted — no longer rollback-able, but it keeps shielding
    // until its own delta lands (or the TTL fires): a /api/my-votes response
    // already in flight was read BEFORE this row existed.
    confirmPendingCast(pendingId);
    return { ok: true, targetDir, changedEdges: changedEdges.filter((e) => !declined.has(e)) };
  } catch (err) {
    // A press that has already been superseded must NOT restore what it saw:
    // the later press on the same control re-wrote these edges deliberately,
    // and undoing it here would revert the user's newest intent instead of
    // this cast. Its own response reconciles the server's view.
    const superseded = !isLatestPendingCast(pendingId);
    resolvePendingCast(pendingId);
    if (superseded) {
      derror("cast", "failed but superseded by a newer press — not rolling back:", err);
      return { ok: false, targetDir, changedEdges: [] };
    }
    // Roll back the optimistic apply and the local store so a failed sync can't
    // leave the heatmap or button state showing a vote the server never took.
    revertGroups(mode, label, blocks, groups);
    derror("cast", "failed, rolled back:", err);
    // The 401 and the server's own refusals have already said their piece;
    // anything else (offline, 500, timeout) would otherwise revert in silence.
    if (!announced) announceRollback("Couldn't save your vote — please try again.");
    return { ok: false, targetDir, changedEdges: [] };
  }
}
