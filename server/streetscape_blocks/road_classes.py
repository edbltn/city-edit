"""Road-class sets shared across the blocks pipeline.

STREET_CLASSES decides which junction clusters are STREET junctions: capture
(the "ladder" fix) and geometric cell containment only apply there. Plaza
streets (`pedestrian`) and pure walking fabric (footway/path/cycleway/steps)
deliberately don't count — flagging their forks as street junctions shredded
park and plaza networks into node-cell confetti; a real cross street at a
plaza still flags its own junctions through its street-class edges.
"""

STREET_CLASSES = {
    "motorway", "motorway_link", "trunk", "trunk_link", "primary",
    "primary_link", "secondary", "secondary_link", "tertiary", "tertiary_link",
    "residential", "living_street", "unclassified", "service", "busway",
}


def is_street_class(road_class: str) -> bool:
    return road_class in STREET_CLASSES
