"""Distinct-voter counting for MANY edge sets at once (the batch behind the
floating proposal labels).

The rule under test is the one the map's two numbers now share: a corridor is
worth the number of PEOPLE who voted along it, not the number of vote rows a
route cast fanned across its edges. One voter drawing a route over 59 edges is
+1 here, and the label reads what the card reads because both read this.

The cursor is faked — what it emulates is the SQL's contract (unnest the
(set index, edge id) pairs, join, COUNT DISTINCT identity per set) — so these
pin the batching, the alignment of answers to inputs, and the degradation
paths. The SQL itself is exercised against real Postgres by the app.
"""
from unittest import mock

import database
import vote_identity


SLUG = "test-map"

# (edge_id, vote_type_id, direction, device) — one row per vote, exactly as
# edge_votes stores them: a route cast writes one per edge it covers.
VOTES = [
    # Device "solo" drew one route across three edges — ONE person.
    (1, 7, 1, "solo"),
    (2, 7, 1, "solo"),
    (3, 7, 1, "solo"),
    # Device "other" upvoted a single edge of it.
    (2, 7, 1, "other"),
    # ...and downvoted a different type elsewhere on the same edge.
    (2, 9, -1, "other"),
]


class FakeCursor:
    """Emulates the batch query: unnest(idx[], edge[]) ⋈ edge_votes, grouped."""

    def __init__(self, rows):
        self.rows = rows
        self.result: list[tuple] = []

    def execute(self, sql, params):
        idx_col, edge_col, slug = params
        assert slug == SLUG
        assert len(idx_col) == len(edge_col)
        # The identity must be spelled exactly as vote_identity defines it —
        # the same one the block layer counts by, so a pin and a card can't
        # disagree about how many people that is. It also must not be table-
        # qualified: it can be a COALESCE, and `v.COALESCE(...)` is a Postgres
        # syntax error the fake would otherwise never notice.
        assert f"COUNT(DISTINCT {vote_identity.SQL_IDENTITY})" in sql
        seen: dict[tuple[int, int, int], set[str]] = {}
        for i, edge in zip(idx_col, edge_col):
            for e, vt, direction, device in self.rows:
                if e != edge:
                    continue
                seen.setdefault((i, vt, direction), set()).add(device)
        self.result = [(i, vt, d, len(devices))
                       for (i, vt, d), devices in seen.items()]

    def fetchall(self):
        return self.result


def run(edge_sets, rows=VOTES):
    cm = mock.MagicMock()
    cm.__enter__ = mock.Mock(return_value=FakeCursor(rows))
    cm.__exit__ = mock.Mock(return_value=False)
    with mock.patch.object(database, "DATABASE_URL", "postgresql://fake"), \
            mock.patch.object(database, "get_cursor", return_value=cm):
        return database.count_unique_voters_for_edge_sets(SLUG, edge_sets)


def net(counts, vt=7):
    up = sum(c for v, d, c in counts if v == vt and d > 0)
    down = sum(c for v, d, c in counts if v == vt and d < 0)
    return up - down


def test_one_route_voter_counts_once_however_many_edges_they_covered():
    """The bug this exists for: the corridor's edge-net SUM said 3."""
    assert net(run([[1, 2, 3]])[0]) == 2  # "solo" + "other", not 4 rows


def test_each_set_is_answered_independently_and_in_order():
    answers = run([[1], [2], [3]])
    assert [net(a) for a in answers] == [1, 2, 1]


def test_an_edge_shared_between_sets_counts_for_both():
    a, b = run([[1, 2], [2, 3]])
    assert net(a) == 2 and net(b) == 2


def test_duplicate_edges_within_a_set_do_not_inflate():
    assert net(run([[2, 2, 2]])[0]) == 2


def test_directions_and_types_stay_separate():
    counts = run([[2]])[0]
    assert sorted(counts) == [(7, 1, 2), (9, -1, 1)]


def test_an_empty_set_answers_empty_without_dropping_its_slot():
    answers = run([[], [1], []])
    assert len(answers) == 3
    assert answers[0] == [] and answers[2] == []
    assert net(answers[1]) == 1


def test_no_edges_at_all_short_circuits_to_one_empty_answer_per_set():
    assert run([[], []]) == [[], []]


def test_no_database_answers_empty_rather_than_raising():
    with mock.patch.object(database, "DATABASE_URL", ""):
        assert database.count_unique_voters_for_edge_sets(SLUG, [[1], [2]]) == [[], []]


def test_a_db_error_degrades_to_empty_answers_aligned_with_the_input():
    cm = mock.MagicMock()
    cm.__enter__ = mock.Mock(side_effect=RuntimeError("connection refused"))
    with mock.patch.object(database, "DATABASE_URL", "postgresql://fake"), \
            mock.patch.object(database, "get_cursor", return_value=cm):
        assert database.count_unique_voters_for_edge_sets(SLUG, [[1], [2]]) == [[], []]


def test_the_single_set_helper_is_the_batch_with_one_set():
    """The card and the label must not be able to count differently — the
    single-set entry point is literally the batch, so there is one rule."""
    cm = mock.MagicMock()
    cm.__enter__ = mock.Mock(return_value=FakeCursor(VOTES))
    cm.__exit__ = mock.Mock(return_value=False)
    with mock.patch.object(database, "DATABASE_URL", "postgresql://fake"), \
            mock.patch.object(database, "get_cursor", return_value=cm):
        one = database.count_unique_voters_for_edges(SLUG, [1, 2, 3])
    assert sorted(one) == sorted(run([[1, 2, 3]])[0])


def test_the_single_set_helper_still_short_circuits_on_no_edges():
    with mock.patch.object(database, "DATABASE_URL", "postgresql://fake"):
        assert database.count_unique_voters_for_edges(SLUG, []) == []
