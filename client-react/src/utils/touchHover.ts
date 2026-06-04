// Touch sticky-hover guard.
//
// On touch devices a tap leaves the tapped element in the `:hover` state until
// the next tap elsewhere — so a −/+ vote button stays tinted after you press it.
// Mirror the map's placement-ghost rule (see MapView.tsx): ANY click/tap flags
// hover off (adds `hover-off` to <html>), and the next real cursor move clears
// it. On desktop a mousemove follows the click almost immediately, so hover
// returns at once (a brief, intended flicker); on touch no mousemove follows, so
// hover stays off until the next interaction. Hover-styled CSS that would
// otherwise stick gates itself behind `html:not(.hover-off)`.

const HOVER_OFF = "hover-off";

export function installTouchHoverGuard() {
  const root = document.documentElement;
  // Capture phase so handlers that stopPropagation (markers, proposal cards)
  // can't hide the tap from us. We only toggle a class — never preventDefault —
  // so every other click handler is completely untouched.
  document.addEventListener("click", () => root.classList.add(HOVER_OFF), true);
  // A genuine cursor move re-enables hover. A mobile tap emits a synthetic
  // mousemove *before* its click, but `click` fires last in the sequence, so it
  // wins and hover stays off on touch.
  document.addEventListener("mousemove", () => root.classList.remove(HOVER_OFF));
}
