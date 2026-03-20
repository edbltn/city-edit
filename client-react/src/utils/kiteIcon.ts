import L from "leaflet";

function kiteHtml(color: string, ghost = false): string {
  const cls = ghost ? "ascii-marker is-ghost" : "ascii-marker";
  return `<div class="${cls}" style="color: ${color};">
    <span class="ascii-kite">◆</span>
    <span class="ascii-stem"></span>
  </div>`;
}

export function kiteIcon(color: string): L.DivIcon {
  return L.divIcon({
    className: "custom-marker",
    html: kiteHtml(color),
    iconSize: [26, 38],
    iconAnchor: [13, 38],
  });
}

export function kiteGhostIcon(color: string): L.DivIcon {
  return L.divIcon({
    className: "hover-ghost-marker",
    html: kiteHtml(color, true),
    iconSize: [26, 38],
    iconAnchor: [13, 38],
  });
}
