/**
 * Tessellated hexagon icon using mathematically precise honeycomb geometry.
 *
 * For pointy-top hexagons with circumradius r:
 *   - width = √3 × r
 *   - height = 2r
 *   - row vertical offset = 1.5r
 *   - column horizontal offset = √3r/2
 *
 * Using r = 3:
 *   - half-width = √3/2 × 3 ≈ 2.6
 *   - Top hex center: (8, 3)
 *   - Bottom-left center: (5.4, 7.5)
 *   - Bottom-right center: (10.6, 7.5)
 */
export function HexagonIcon() {
  return (
    <svg width="16" height="11" viewBox="0 0 16 11" className="legend-hexagons">
      {/* Top hexagon - light gold */}
      <polygon
        points="8,0 10.6,1.5 10.6,4.5 8,6 5.4,4.5 5.4,1.5"
        fill="#F5D76E"
      />
      {/* Bottom-left hexagon - medium amber */}
      <polygon
        points="5.4,4.5 8,6 8,9 5.4,10.5 2.8,9 2.8,6"
        fill="#E8A838"
      />
      {/* Bottom-right hexagon - dark burnt orange */}
      <polygon
        points="10.6,4.5 13.2,6 13.2,9 10.6,10.5 8,9 8,6"
        fill="#CC4400"
      />
    </svg>
  );
}
