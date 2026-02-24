/**
 * Gradient line icon — blackbody heat ramp matching WeightedSegmentsLayer.
 */
export function HeatLineIcon() {
  return (
    <svg width="16" height="11" viewBox="0 0 16 11" className="legend-heatline">
      <defs>
        <linearGradient id="heat-grad" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#B41E0A" />
          <stop offset="40%" stopColor="#E65A0A" />
          <stop offset="70%" stopColor="#FFC828" />
          <stop offset="100%" stopColor="#FFFFDC" />
        </linearGradient>
      </defs>
      <polygon
        points="0,4 16,2 16,9 0,7"
        fill="url(#heat-grad)"
        opacity="0.9"
      />
    </svg>
  );
}
