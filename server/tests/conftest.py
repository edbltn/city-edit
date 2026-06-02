"""Shared pytest fixtures for the backend suite.

Unit tests are pure or use fakeredis — no Postgres/Redis required. Integration
tests (marked @pytest.mark.integration) need live services and are opt-in.
"""
import pytest

import fakeredis


@pytest.fixture
def redis_client():
    """An in-process fake Redis (decode_responses=True, matching app config)."""
    return fakeredis.FakeStrictRedis(decode_responses=True)


# A tiny graph fixture: 3 nodes, 2 edges sharing node 1 (mirrors the frontend
# voteApply test graph). node_adj[n] = edge ids touching node n.
@pytest.fixture
def tiny_graph():
    return {
        "edge_count": 2,
        "node_count": 3,
        "node_adj": [[0], [0, 1], [1]],
    }
