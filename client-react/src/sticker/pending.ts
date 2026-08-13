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

/**
 * The code outlives the page, because the visit does.
 *
 * A scanner can change map before voting — the vote-type they want may only
 * exist on another map, and the switcher is right there. Switching maps is a
 * real navigation, so a module variable alone is gone by the time they cast and
 * the code silently never binds. sessionStorage is exactly the right lifetime:
 * it survives navigation within the tab and dies with it, which is the same
 * span as "this visit".
 */
const STORAGE_KEY = "cityedit.pendingSticker";

let pending: string | null = null;
let captured = false;

function remember(code: string | null): void {
  pending = code;
  try {
    if (code) window.sessionStorage.setItem(STORAGE_KEY, code);
    else window.sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // Private mode / storage disabled. Same-map binding still works off the
    // module variable; only the cross-map case is lost, which is better than
    // throwing on the boot path.
  }
}

function recall(): string | null {
  try {
    const code = window.sessionStorage.getItem(STORAGE_KEY);
    return code && CODE_PATTERN.test(code) ? code : null;
  } catch {
    return null;
  }
}

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

  if (!window.location.search.includes("stk=")) {
    // No `stk` in the URL is not the same as no pending code: on the second and
    // later pages of a visit it was stripped from the address bar long ago and
    // only the stored copy is left.
    pending = recall();
    return;
  }

  const { code, search } = readPendingSticker(window.location.search);
  remember(code);
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
 * the visit is the one that decides what the code means, and a later vote
 * somewhere else must not be able to drag it there.
 */
export function takePendingSticker(): string | null {
  const code = pending;
  remember(null);
  return code;
}

/** Whether this visit still owes a code its proposal. */
export function hasPendingSticker(): boolean {
  return pending !== null;
}

/** Drop any pending code without spending it. Tests and the reset path. */
export function clearPendingSticker(): void {
  captured = false;
  remember(null);
}
