// Referenced by: components/MapLibreBackground/MapLibreBackground.tsx
// ==========================================================================
// Vote-arrival animation — the bloom a landing vote makes
// ==========================================================================
// When a vote lands (the caster's own, another user's, an agent's), it reaches
// the client as a delta over the merged WebSocket hub, GraphLayer folds it into
// the block totals, and MapLibreBackground re-derives the affected blocks' heat.
// Without this module that new heat simply appears: a block snaps from
// invisible to lit between two frames, which reads as a rendering glitch rather
// than as news.
//
// This module turns that snap into a short, deliberate gesture with two parts:
//
//   1. the HEAT TWEEN — the block's `heat` feature-state eases from its old
//      value to its new one, so the fill and outline ride the existing ramp up
//      (or down) instead of jumping. This IS the fade-in: heat 0 is fully
//      transparent by design, so a newly-lit block genuinely fades in, and it
//      does so through the same warm→hot→tip ramp the map already speaks.
//   2. the SPARK — a separate `arrive` feature-state ∈ [0,1] with a fast attack
//      and a quadratic decay, which the `block-arrival` line layer renders as a
//      brief bright ring tracing the block. It says "here, just now", then
//      leaves nothing behind.
//
// Three properties are load-bearing, in the "don't regress this" sense:
//
//   - DIFF-DRIVEN, never a sweep. The caller hands us only the blocks whose
//     heat actually changed. We never enumerate the block table, and nothing
//     here is hooked to `sourcedata` — feature-state lives on the SOURCE, so a
//     re-application pass would fire once per TILE (the 4.4M-writes-per-zoom
//     bug). Peak cost here is one setFeatureState per ANIMATING block per
//     frame, capped by MAX_CONCURRENT.
//   - PAINT-ONLY. Both keys feed paint properties that MapLibre evaluates on
//     the GPU per frame (fill/line opacity, colour, line opacity). No geometry
//     is rebuilt, no layer is added or removed, no layout property moves.
//   - GRACEFUL UNDER BURSTS. A busy map can land many votes a second, and a
//     legend toggle can move thousands of blocks at once. So: one cohort per
//     apply (a corridor blooms as ONE gesture, not N staggered ones), spark
//     gain damped as the cohort grows, a hard cap past which everything paints
//     instantly, and a block re-hit mid-episode keeps its ORIGINAL spark clock
//     so a run of votes on one block glows once instead of strobing.
//
// The module is deliberately free of MapLibre, DOM and clock dependencies —
// everything comes in through options — so the timing curves and the burst
// policy are unit-testable (arrivalAnimation.test.ts).

/** One block's heat moving from `from` to `to` (both ramp positions ∈ [−1,1]). */
export interface HeatChange {
  id: number;
  from: number;
  to: number;
}

export interface ArrivalAnimatorOptions {
  /** Commit one block's animated state. Both keys are written in a single call
   *  so an animating block costs exactly one setFeatureState per frame. */
  write: (id: number, heat: number, arrive: number) => void;
  now: () => number;
  requestFrame: (cb: () => void) => number;
  cancelFrame: (handle: number) => void;
  /** Overridable for tests; defaults to the OS/browser setting. */
  reducedMotion?: () => boolean;
  /** True while something else owns the map's pixels — in practice a Leaflet
   *  zoom ride, during which the GL frame is a frozen bitmap under a CSS
   *  transform. Arrivals then paint instantly rather than animating against a
   *  camera that isn't tracking them. */
  suspended?: () => boolean;
}

export interface ArrivalAnimator {
  /** Animate a cohort of changed blocks. Called once per vote-driven apply. */
  arrive(changes: HeatChange[]): void;
  /** Jump every in-flight block to its final heat and clear the spark. Used
   *  when the animation would fight something else for the same pixels — a
   *  Leaflet zoom ride, or an apply that must paint instantly. */
  settle(): void;
  /** Drop everything WITHOUT writing — for the full-rewrite path, which is
   *  about to removeFeatureState the whole source anyway. */
  reset(): void;
  /** Blocks currently animating (debug/logging). */
  readonly size: number;
}

/** Heat eases to its new value over this long. Short enough to feel like a
 *  response, long enough that the eye reads it as a fade rather than a flicker. */
export const HEAT_FADE_MS = 420;

/** The spark's full life. Outlives the heat tween slightly so the ring is still
 *  dying as the fill settles — the pulse hands off to the heat, rather than the
 *  two ending together in one hard stop. */
export const SPARK_MS = 560;

/** Fraction of SPARK_MS spent rising. Fast in, slow out: the classic attack the
 *  eye reads as "something happened" rather than "something is pulsing". */
const SPARK_ATTACK = 0.16;

/** Cohorts up to this size spark at full strength — the common case, one vote
 *  lighting a corridor of a few blocks. */
const SOFT_COHORT = 12;

/** …and by this size the spark is damped to MIN_GAIN. Between the two it fades
 *  out linearly, so the display gets quieter exactly as it gets busier. */
const HARD_COHORT = 140;
const MIN_GAIN = 0.34;

/** Past this many concurrently animating blocks there is no animation at all:
 *  the whole cohort paints instantly. A legend toggle re-ranks every block on
 *  the map, and a citywide shimmer is neither informative nor cheap. */
export const MAX_CONCURRENT = 240;

const clamp01 = (t: number) => (t < 0 ? 0 : t > 1 ? 1 : t);

/** Decelerating: most of the distance is covered early, so the block arrives
 *  at its new heat quickly and then settles the last few percent. */
const easeOutCubic = (t: number) => 1 - (1 - t) ** 3;

const smoothstep = (t: number) => t * t * (3 - 2 * t);

/** The spark's shape over its normalized life: eased rise to 1, quadratic fall
 *  back to 0. Zero outside [0,1] so an un-started or finished spark is free. */
export function sparkEnvelope(t: number): number {
  if (t <= 0 || t >= 1) return 0;
  if (t < SPARK_ATTACK) return smoothstep(t / SPARK_ATTACK);
  const decay = (t - SPARK_ATTACK) / (1 - SPARK_ATTACK);
  return (1 - decay) ** 2;
}

/** Spark strength for a cohort of `n` blocks — full below SOFT_COHORT, damped
 *  toward MIN_GAIN by HARD_COHORT. Exported for the test's benefit. */
export function cohortGain(n: number): number {
  if (n <= SOFT_COHORT) return 1;
  if (n >= HARD_COHORT) return MIN_GAIN;
  const t = (n - SOFT_COHORT) / (HARD_COHORT - SOFT_COHORT);
  return 1 - t * (1 - MIN_GAIN);
}

function systemPrefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined"
    && typeof window.matchMedia === "function"
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

interface Tween {
  /** Where this tween started — the block's live value at the moment it was
   *  (re)targeted, NOT its last committed final, so a block re-hit mid-flight
   *  continues from where it is rather than snapping back. */
  from: number;
  to: number;
  /** Last value written, and the `from` of any re-target. */
  cur: number;
  heatStart: number;
  sparkStart: number;
  /** 0 for a block that is only cooling — the spark marks intensification, and
   *  a block losing heat should recede quietly, not flash. */
  gain: number;
}

export function createArrivalAnimator(opts: ArrivalAnimatorOptions): ArrivalAnimator {
  const { write, now, requestFrame, cancelFrame } = opts;
  const reducedMotion = opts.reducedMotion ?? systemPrefersReducedMotion;
  const suspended = opts.suspended ?? (() => false);

  const live = new Map<number, Tween>();
  let frame: number | null = null;

  const cancel = () => {
    if (frame !== null) {
      cancelFrame(frame);
      frame = null;
    }
  };

  const tick = () => {
    frame = null;
    const t = now();
    for (const [id, tw] of live) {
      const heatT = clamp01((t - tw.heatStart) / HEAT_FADE_MS);
      const heat = tw.from + (tw.to - tw.from) * easeOutCubic(heatT);
      const spark = tw.gain > 0
        ? sparkEnvelope((t - tw.sparkStart) / SPARK_MS) * tw.gain
        : 0;
      if (heatT >= 1 && spark <= 0) {
        // Land exactly on the target — never on the eased approximation of it,
        // or the source would slowly accumulate float drift against the diff
        // bookkeeping that decides what to write next time.
        write(id, tw.to, 0);
        live.delete(id);
      } else {
        tw.cur = heat;
        write(id, heat, spark);
      }
    }
    if (live.size > 0) frame = requestFrame(tick);
  };

  const settle = () => {
    for (const [id, tw] of live) write(id, tw.to, 0);
    live.clear();
    cancel();
  };

  const arrive = (changes: HeatChange[]) => {
    if (changes.length === 0) return;

    // Instant paths: the user asked for less motion, the map's pixels are
    // busy elsewhere, or so much moved at once that animating it would be a
    // strobe rather than a signal. Settle first so any in-flight tween can't
    // overwrite these finals on its next frame.
    if (reducedMotion() || suspended() || changes.length + live.size > MAX_CONCURRENT) {
      settle();
      for (const c of changes) write(c.id, c.to, 0);
      return;
    }

    const t = now();
    const gain = cohortGain(changes.length + live.size);
    for (const c of changes) {
      const prev = live.get(c.id);
      const from = prev ? prev.cur : c.from;
      const rising = Math.abs(c.to) > Math.abs(from);
      // A block already mid-episode keeps its original spark clock and gain:
      // the second vote extends the glow it is already wearing instead of
      // restarting it. That single rule is what keeps a rapid run of votes on
      // one corner from flashing once per vote.
      const sparking = prev && prev.gain > 0;
      live.set(c.id, {
        from,
        to: c.to,
        cur: from,
        heatStart: t,
        sparkStart: sparking ? prev.sparkStart : t,
        gain: sparking ? prev.gain : (rising ? gain : 0),
      });
    }
    if (frame === null) frame = requestFrame(tick);
  };

  return {
    arrive,
    settle,
    reset: () => {
      live.clear();
      cancel();
    },
    get size() {
      return live.size;
    },
  };
}
