// ==========================================================================
// useVotesVersion — the one hook that keeps every vote view in sync
// ==========================================================================
// voteStore is plain module-level state (no React). This hook is the bridge:
// it subscribes a component to the store and returns a version number that
// increments on EVERY vote mutation, from ANY path — the top-bar banner, the
// proposal modal, server reconciliation, or an optimistic rollback.
//
// Call it in any component that reads votes (getVote/coverage), then list the
// returned value in the dependency array of the memo/effect/callback that does
// the reading. That is the entire sync contract: one writer (the store), one
// signal (this version), every reader re-renders together.
//
// Do NOT replace this with a local useState counter you bump by hand — that is
// the bug this design removed (the banner and modal each bumped their own and
// never heard each other's votes).

import { useSyncExternalStore } from "react";

import { subscribeVotes, getVotesVersion } from "./voteStore";

export function useVotesVersion(): number {
  return useSyncExternalStore(subscribeVotes, getVotesVersion, getVotesVersion);
}
