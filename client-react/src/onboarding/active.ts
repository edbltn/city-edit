// ==========================================================================
// "The first-run flow is talking" — one flag, so the app can stand aside
// ==========================================================================
// The bottom-centre of the map is a single slot (MapNotice), and during a first
// run the coach owns it. The one other thing that wants it is the event banner:
// a durable-dismissible invitation to a meetup next week, which is exactly the
// message that can wait until somebody's second visit.
//
// A module-level flag with a subscription rather than a context or a prop, for
// the same reason the vote-rejected toast is a window event: the two components
// are siblings under different parents, and neither owns the other. It is a
// single boolean, written by one component and read by one, so a store is the
// smallest thing that can carry it without threading props through App.
// ==========================================================================

import { useSyncExternalStore } from "react";

import { unsuppress } from "./firstRun";

let active = false;
/** Bumped when somebody asks for the flow back by hand. */
let requests = 0;
const listeners = new Set<() => void>();

function notify(): void {
  for (const listen of listeners) listen();
}

export function setOnboardingActive(next: boolean): void {
  if (next === active) return;
  active = next;
  notify();
}

/**
 * Open the flow on demand — "Start a sentence" in How it Works.
 *
 * This is the escape hatch for the deliberate false negative in the first-run
 * key: counting on the IP hash means a genuinely new person behind a network
 * somebody else has voted from is treated as a returning visitor and never sees
 * the wall. Rather than loosen the key (which re-opens the fragmentation the key
 * exists to fix), the flow is one menu item away for anyone who wants it.
 */
export function requestOnboarding(): void {
  unsuppress();
  requests += 1;
  notify();
}

/** Re-renders the caller when somebody asks for the flow back. */
export function useOnboardingRequests(): number {
  return useSyncExternalStore(subscribe, () => requests, () => 0);
}

export function isOnboardingActive(): boolean {
  return active;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Re-renders the caller when the first-run flow opens or closes. */
export function useOnboardingActive(): boolean {
  return useSyncExternalStore(subscribe, isOnboardingActive, () => false);
}
