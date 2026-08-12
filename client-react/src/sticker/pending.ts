// ==========================================================================
// The sticker a visit came from (`?stk=`)
// ==========================================================================
// When a scan of an unresolved sticker hands us a location, the gate sends the
// browser to the map as a real navigation — so nothing in memory survives the
// hop and the sticker's identity has to ride in the URL to get there.
//
// `?stk=<code>` does that, and is captured + stripped at boot for the same two
// reasons `?src=` is (utils/sourceTag.ts): RouteContext rewrites the address bar
// from the selection and drops params it does not own, and a link re-shared out
// of the address bar must not carry someone else's sticker.
//
// Once the first vote of that visit lands, the code is spent: it is posted to
// /api/stickers/<code>/resolve, which pins the sticker to that spot for every
// future scan, and cleared so a second vote in the same session cannot move it.

const CODE_PATTERN = /^[a-zA-Z0-9]{4,12}$/;

let pending: string | null = null;
let captured = false;

export interface PendingCapture {
  /** The code, or null when absent or malformed. */
  code: string | null;
  /** The query string with `stk` removed, ready to write back. */
  search: string;
}

/**
 * Split `?stk=` out of a query string. Pure, so the capture rule is testable
 * without a DOM — a malformed code is dropped but still stripped, since junk in
 * the address bar is junk whether or not we could read it.
 */
export function readPendingSticker(search: string): PendingCapture {
  const params = new URLSearchParams(search);
  const raw = params.get("stk");
  params.delete("stk");
  const qs = params.toString();
  return {
    code: raw && CODE_PATTERN.test(raw) ? raw : null,
    search: qs ? `?${qs}` : "",
  };
}

/**
 * Read + strip `?stk=`. Idempotent, and must run before anything rewrites the
 * URL — App calls it beside captureSourceTag() at the top of map resolution.
 */
export function capturePendingSticker(): void {
  if (typeof window === "undefined" || captured) return;
  captured = true;
  if (!window.location.search.includes("stk=")) return;
  const { code, search } = readPendingSticker(window.location.search);
  pending = code;
  try {
    window.history.replaceState(
      null, "", window.location.pathname + search + window.location.hash
    );
  } catch {
    /* ignore */
  }
}

/**
 * Take the pending code, clearing it. Single-use on purpose: the first vote of
 * the visit is the one that decides where the sticker lives, and a later vote
 * somewhere else must not be able to drag it there.
 */
export function takePendingSticker(): string | null {
  const code = pending;
  pending = null;
  return code;
}

/** Whether this visit still owes a sticker its location. */
export function hasPendingSticker(): boolean {
  return pending !== null;
}
