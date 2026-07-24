"""Shared pytest fixtures for the backend suite.

Unit tests are pure or use fakeredis — no Postgres/Redis required. Integration
tests (marked @pytest.mark.integration) need live services and are opt-in.
"""
import pytest

import fakeredis
import numpy as np


@pytest.fixture
def redis_client():
    """An in-process fake Redis (decode_responses=True, matching app config)."""
    return fakeredis.FakeStrictRedis(decode_responses=True)


# A tiny graph fixture: 3 nodes, 2 edges sharing node 1 (mirrors the frontend
# voteApply test graph). edge_ends[e] = [node_u, node_v] for edge e.
@pytest.fixture
def tiny_graph():
    return {
        "edge_count": 2,
        "node_count": 3,
        "edge_ends": np.array([[0, 1], [1, 2]], dtype=np.int32),
    }
