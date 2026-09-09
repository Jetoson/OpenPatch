"""Clicking an endpoint opens it in the same page.

"""

import types

import pytest


class FakeState(dict):
    def __getattr__(self, key):
        return self[key]

    def __setattr__(self, key, value):
        self[key] = value


@pytest.fixture
def navigate(app_functions):
    st = types.SimpleNamespace(session_state=FakeState(), query_params={})
    namespace = app_functions(["open_endpoint"], {"st": st})
    return st, namespace["open_endpoint"]


DEVICES = ["seed0", "seed1", "seed2"]


def test_a_click_opens_that_endpoint(navigate):
    st, open_endpoint = navigate
    st.session_state["fleet_host_click"] = {"row": 1}

    open_endpoint("fleet_host_click", DEVICES)

    assert st.query_params["device_id"] == "seed1"
    assert st.session_state["page"] == "Endpoint Detail"


def test_the_destination_stays_bookmarkable(navigate):
    st, open_endpoint = navigate
    st.session_state["fleet_host_click"] = {"row": 0}

    open_endpoint("fleet_host_click", DEVICES)

    assert "device_id" in st.query_params


def test_the_row_is_positional_against_what_was_rendered(navigate):
    st, open_endpoint = navigate
    st.session_state["fleet_host_click"] = {"row": 0}

    open_endpoint("fleet_host_click", ["seed2"])   # one row survived the filter

    assert st.query_params["device_id"] == "seed2"


def test_the_update_counts_navigate_too(navigate):
    st, open_endpoint = navigate
    st.session_state["fleet_windows_click"] = {"row": 2}

    open_endpoint("fleet_windows_click", DEVICES)

    assert st.query_params["device_id"] == "seed2"


@pytest.mark.parametrize("click", [None, {"row": None}, {"row": 99}, {"row": -1}])
def test_a_stale_or_impossible_click_does_nothing(navigate, click):
    """The click value is only present during the rerun it triggers."""
    st, open_endpoint = navigate
    if click is not None:
        st.session_state["fleet_host_click"] = click

    open_endpoint("fleet_host_click", DEVICES)

    assert "device_id" not in st.query_params
    assert "page" not in st.session_state


def test_no_link_column_remains(app_source):
    """A LinkColumn is what opened the new tab."""
    assert "LinkColumn" not in app_source


def test_the_device_ids_are_captured_after_filtering(app_source):
    assert app_source.index('visible_device_ids = df["device_id"].tolist()') > \
        app_source.index('df = df[df["department"].isin(selected)]')
