"""Unit tests for the GTB2 binary topology layout (graph_registry.encode_topology_bin).

Layout: '<4sIII' header [b'GTB2', n_nodes, n_edges, n_blocks] + int32 coords
(deg×1e7 lat/lon pairs) + uint32 edge ends (from/to pairs) + optional trailing
int32[n_edges] edge_block_id section when the map has blocks.
"""
import struct

import numpy as np

from graph_registry import encode_topology_bin

NODES = np.array([[40.0, -74.0], [40.1, -74.1], [40.25, -73.9]], dtype=np.float64)
EDGES = np.array([[0, 1], [1, 2]], dtype=np.int32)

HEADER = struct.calcsize("<4sIII")  # 16 bytes
COORDS = len(NODES) * 2 * 4
ENDS = len(EDGES) * 2 * 4


def test_gtb2_without_blocks():
    blob = encode_topology_bin(NODES, EDGES)
    magic, n_nodes, n_edges, n_blocks = struct.unpack_from("<4sIII", blob, 0)
    assert magic == b"GTB2"
    assert (n_nodes, n_edges, n_blocks) == (3, 2, 0)
    assert len(blob) == HEADER + COORDS + ENDS  # no trailing block section

    coords = np.frombuffer(blob, dtype="<i4", count=6, offset=HEADER)
    assert coords.tolist() == [
        400000000, -740000000, 401000000, -741000000, 402500000, -739000000,
    ]
    ends = np.frombuffer(blob, dtype="<u4", count=4, offset=HEADER + COORDS)
    assert ends.tolist() == [0, 1, 1, 2]


def test_gtb2_with_blocks():
    ebid = np.array([7, -1], dtype=np.int32)
    blob = encode_topology_bin(NODES, EDGES, ebid, n_blocks=8)
    magic, n_nodes, n_edges, n_blocks = struct.unpack_from("<4sIII", blob, 0)
    assert magic == b"GTB2"
    assert (n_nodes, n_edges, n_blocks) == (3, 2, 8)
    assert len(blob) == HEADER + COORDS + ENDS + len(EDGES) * 4

    blocks = np.frombuffer(blob, dtype="<i4", count=2, offset=HEADER + COORDS + ENDS)
    assert blocks.tolist() == [7, -1]  # −1 (unmapped) survives the round-trip


def test_gtb2_block_section_accepts_plain_lists():
    blob = encode_topology_bin(NODES, EDGES, [3, 4], n_blocks=5)
    blocks = np.frombuffer(blob, dtype="<i4", count=2, offset=HEADER + COORDS + ENDS)
    assert blocks.tolist() == [3, 4]
