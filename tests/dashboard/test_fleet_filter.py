"""Clicking a count shows the endpoints behind it.
"""

import types

import pandas as pd
import pytest


class FakeState(dict):
    def __getattr__(self, key):
        return self[key]

    def __setattr__(self, key, value):
        self[key] = value


FLEET = pd.DataFrame([
    {"device_id": "dev-1", "online": True},
    {"device_id": "dev-2", "online": False},
    {"device_id": "dev-3", "online": False},
])


@pytest.fixture
def fleet(app_functions):
    st = types.SimpleNamespace(
        session_state=FakeState(),
        query_params=types.SimpleNamespace(clear=lambda: None),
    )
    namespace = app_functions(
        ["apply_fleet_filter", "select_fleet"],
        {
            "st": st,
            "pd": pd,
            "at_risk_device_ids": lambda level: {
                "eol": ["dev-2", "dev-3"], "critical": ["dev-3"],
            }[level],
        },
    )
    namespace["st"] = st
    return types.SimpleNamespace(**namespace)


class TestWhatEachCardSelects:
    def test_online_shows_the_ones_that_checked_in_recently(self, fleet):
        assert list(fleet.apply_fleet_filter(FLEET, "online")["device_id"]) == ["dev-1"]

    def test_offline_shows_the_rest(self, fleet):
        assert list(fleet.apply_fleet_filter(FLEET, "offline")["device_id"]) == [
            "dev-2", "dev-3"
        ]

    def test_the_at_risk_groups_come_from_the_server(self, fleet):
        """Whether a machine is end-of-life is a property of the software it
        carries."""
        assert list(fleet.apply_fleet_filter(FLEET, "eol")["device_id"]) == [
            "dev-2", "dev-3"
        ]
        assert list(fleet.apply_fleet_filter(FLEET, "critical")["device_id"]) == ["dev-3"]

    def test_no_filter_is_the_whole_fleet(self, fleet):
        assert len(fleet.apply_fleet_filter(FLEET, None)) == len(FLEET)

    def test_the_filtered_frame_keeps_its_columns(self, fleet):
        assert list(fleet.apply_fleet_filter(FLEET, "online").columns) == list(FLEET.columns)


class TestClickingACard:
    def test_it_selects_the_group_and_shows_the_fleet(self, fleet):
        fleet.select_fleet("offline")

        assert fleet.st.session_state["fleet_filter"] == "offline"
        assert fleet.st.session_state["page"] == "Overview"

    def test_clicking_the_same_card_again_clears_it(self, fleet):
        fleet.select_fleet("offline")
        fleet.select_fleet("offline")

        assert fleet.st.session_state["fleet_filter"] is None

    def test_clicking_a_different_card_switches_group(self, fleet):
        fleet.select_fleet("offline")
        fleet.select_fleet("eol")

        assert fleet.st.session_state["fleet_filter"] == "eol"

    def test_it_leaves_the_endpoint_detail_page(self, fleet):
        cleared = []
        fleet.st.query_params = types.SimpleNamespace(clear=lambda: cleared.append(True))

        fleet.select_fleet("online")

        assert cleared == [True]


def test_every_clickable_card_has_a_filter_that_exists(app_source):
    """The card and the fleet page share one table of groups."""
    assert 'FLEET_FILTERS = {' in app_source
    for group in ("online", "offline", "eol", "critical"):
        assert f'"{group}"' in app_source.split("FLEET_FILTERS = {")[1].split("}")[0]
