// ==========================================================================
// Debug logging: off by default, and bursts cost two lines, not two hundred
// ==========================================================================
// The console is a debugging aid only while it is readable. These pin the two
// promises that keep it that way: a channel nobody asked for prints nothing at
// all, and a stream that fires per hover / per delta / per frame collapses to
// its first line plus one summary.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { dlog, dburst, dwarn, derror, _setDebugChannels } from "./debugLog";

let log: ReturnType<typeof vi.spyOn>;
let warn: ReturnType<typeof vi.spyOn>;
let error: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  // `performance` too: the burst summaries measure elapsed time with
  // performance.now(), and a frozen clock would hide the max-window flush.
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date", "performance"] });
  log = vi.spyOn(console, "log").mockImplementation(() => {});
  warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  error = vi.spyOn(console, "error").mockImplementation(() => {});
  _setDebugChannels("");
});

afterEach(() => {
  _setDebugChannels("");
  vi.useRealTimers();
  vi.restoreAllMocks();
});

/** Every line the console was handed, joined the way devtools shows it. */
function lines(): string[] {
  return log.mock.calls.map((args) => args.join(" "));
}

describe("dlog", () => {
  it("says nothing on a channel nobody enabled", () => {
    dlog("blocks", "select: [1]");
    dburst("blocks", "select", "select: [1]");
    vi.advanceTimersByTime(10000);
    expect(log).not.toHaveBeenCalled();
  });

  it("prints on an enabled channel only", () => {
    _setDebugChannels("blocks");
    dlog("blocks", "in");
    dlog("votes", "out");
    expect(lines()).toEqual(["[blocks] in"]);
  });
});

describe("dburst", () => {
  beforeEach(() => _setDebugChannels("blocks"));

  it("prints the first event and summarises the rest of the burst", () => {
    dburst("blocks", "select", "select: [1]");
    for (let i = 2; i <= 40; i++) {
      vi.advanceTimersByTime(50);
      dburst("blocks", "select", `select: [${i}]`);
    }
    expect(lines()).toEqual(["[blocks] select: [1]"]);   // 39 events, one line

    vi.advanceTimersByTime(1000);                        // …the burst goes quiet
    expect(lines()).toHaveLength(2);
    expect(lines()[1]).toMatch(/^\[blocks\] \+39 more in \d+ms — last: select: \[40\]$/);
  });

  it("reports a stream that never goes quiet instead of hiding behind line one", () => {
    for (let i = 0; i < 400; i++) {           // 20s of an every-50ms loop
      dburst("blocks", "heat", `heat apply: ${i}`);
      vi.advanceTimersByTime(50);
    }
    // One opening line, then a summary every 5s — never one line per event.
    expect(lines().length).toBeGreaterThan(1);
    expect(lines().length).toBeLessThan(12);
    expect(lines().filter((l) => l.includes("more in")).length).toBeGreaterThan(0);
  });

  it("keeps separate streams separate", () => {
    dburst("blocks", "select", "select: [1]");
    dburst("blocks", "heat", "heat apply: 1");
    expect(lines()).toEqual(["[blocks] select: [1]", "[blocks] heat apply: 1"]);
  });

  it("starts a fresh line once a burst has been summarised", () => {
    dburst("blocks", "select", "a");
    dburst("blocks", "select", "b");
    vi.advanceTimersByTime(1000);
    dburst("blocks", "select", "c");
    expect(lines()).toEqual([
      "[blocks] a",
      expect.stringMatching(/^\[blocks\] \+1 more in \d+ms — last: b$/),
      "[blocks] c",
    ]);
  });
});

describe("dwarn / derror", () => {
  it("survive the channel being off — a failure is not chatter", () => {
    dwarn("votes", "topology/vote dimension mismatch");
    derror("topo", "topology decode failed");
    expect(warn).toHaveBeenCalledWith("[votes]", "topology/vote dimension mismatch");
    expect(error).toHaveBeenCalledWith("[topo]", "topology decode failed");
  });
});
