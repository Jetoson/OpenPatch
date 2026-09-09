"""A table selection must not outlive the table it belonged to.
"""

import types
import hashlib

import pandas as pd
import pytest


@pytest.fixture
def helpers(app_functions):
    namespace = app_functions(["rows_digest", "selected_rows"], {"pd": pd, "hashlib": hashlib})
    return namespace["rows_digest"], namespace["selected_rows"]


def selection(rows):
    return types.SimpleNamespace(selection=types.SimpleNamespace(rows=rows))


def tasks(n, start=1):
    return pd.DataFrame({
        "id": list(range(start, start + n)),
        "status": ["PENDING"] * n,
        "hostname": [f"WS-{i}" for i in range(n)],
    })


def test_raw_iloc_really_does_raise():
    with pytest.raises(IndexError):
        tasks(10).iloc[[20]]


class TestSelectedRows:
    def test_positions_past_the_end_are_dropped(self, helpers):
        _, selected_rows = helpers
        assert list(selected_rows(tasks(10), selection([2, 20, 24]))["id"]) == [3]

    def test_an_ordinary_selection_is_unchanged(self, helpers):
        _, selected_rows = helpers
        assert list(selected_rows(tasks(25), selection([0, 4, 24]))["id"]) == [1, 5, 25]

    def test_no_selection_gives_an_empty_frame_of_the_same_shape(self, helpers):
        _, selected_rows = helpers
        frame = tasks(25)
        empty = selected_rows(frame, selection([]))
        assert empty.empty and list(empty.columns) == list(frame.columns)

    def test_negative_positions_are_ignored(self, helpers):
        _, selected_rows = helpers
        assert len(selected_rows(tasks(25), selection([-1, 0]))) == 1

    def test_an_empty_frame_is_safe(self, helpers):
        _, selected_rows = helpers
        assert len(selected_rows(tasks(0), selection([0, 3]))) == 0


class TestRowsDigest:
    def test_unchanged_rows_keep_the_same_key(self, helpers):
        """Otherwise the selection would be discarded on every refresh."""
        rows_digest, _ = helpers
        assert rows_digest(tasks(25), ["id", "status"]) == rows_digest(tasks(25), ["id", "status"])

    def test_removing_a_row_changes_the_key(self, helpers):
        rows_digest, _ = helpers
        assert rows_digest(tasks(10), ["id", "status"]) != rows_digest(tasks(25), ["id", "status"])

    def test_a_status_change_changes_the_key(self, helpers):
        rows_digest, _ = helpers
        cancelled = tasks(25)
        cancelled.loc[3, "status"] = "CANCELLED"
        assert rows_digest(cancelled, ["id", "status"]) != rows_digest(tasks(25), ["id", "status"])

    def test_reordering_changes_the_key(self, helpers):
        rows_digest, _ = helpers
        reordered = tasks(25).iloc[::-1].reset_index(drop=True)
        assert rows_digest(reordered, ["id", "status"]) != rows_digest(tasks(25), ["id", "status"])

    def test_missing_columns_do_not_raise(self, helpers):
        rows_digest, _ = helpers
        assert isinstance(rows_digest(tasks(25), ["nope"]), str)


def test_the_scenario_that_took_the_page_down(helpers):
    """Tick rows 18-22 of 25, cancel them, and 20 remain.
    """
    rows_digest, selected_rows = helpers
    before = tasks(25)
    ticked = [18, 19, 20, 21, 22]

    ids = list(selected_rows(before, selection(ticked))["id"])
    after = before[~before["id"].isin(ids)].reset_index(drop=True)

    assert max(ticked) >= len(after), "the stale positions really are out of bounds"
    selected_rows(after, selection(ticked))     # must not raise
    assert rows_digest(after, ["id", "status"]) != rows_digest(before, ["id", "status"]), \
        "and the key changed, so the selection is retired rather than reused"
