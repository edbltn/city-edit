import L from "leaflet";

function kiteHtml(color: string): string {
  return `<div class="ascii-marker" style="color: ${color};">
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
    html: kiteHtml(color),
    iconSize: [26, 38],
    iconAnchor: [13, 38],
  });
}
