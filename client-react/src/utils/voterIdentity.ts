// ==========================================================================
// Voter identity
// ==========================================================================
// A persistent per-browser id, generated once and stored in localStorage. Sent
// as `voter_id` with every vote; the server hashes it (falling back to client
// IP) to dedupe and to resolve "my votes". This gives stable per-browser vote
// state even across shared networks/NAT, while remaining anonymous.

const STORAGE_KEY = "cityedit_voter_id";

let cached: string | null = null;

function generateId(): string {
  // Prefer crypto.randomUUID; fall back to a random hex string.
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `v-${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
}

export function getVoterId(): string {
  if (cached) return cached;
  if (typeof window === "undefined") return "anonymous";

  try {
    let id = window.localStorage.getItem(STORAGE_KEY);
    if (!id) {
      id = generateId();
      window.localStorage.setItem(STORAGE_KEY, id);
    }
    cached = id;
    return id;
  } catch {
    // localStorage unavailable (private mode / blocked) — use an ephemeral id
    cached = generateId();
    return cached;
  }
}
