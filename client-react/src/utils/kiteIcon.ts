// Kite marker HTML — rendered inside a MapMarker element (formerly a Leaflet
// DivIcon). Size/anchor constants replicate the old iconSize/iconAnchor.

export const KITE_SIZE: [number, number] = [26, 38];

export function kiteHtml(color: string, ghost = false): string {
  const cls = ghost ? "ascii-marker is-ghost" : "ascii-marker";
  return `<div class="${cls}" style="color: ${color};">
    <span class="ascii-kite">◆</span>
    <span class="ascii-stem"></span>
  </div>`;
}
