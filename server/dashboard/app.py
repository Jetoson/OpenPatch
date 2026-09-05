import hashlib
import logging
import os
import sys
from datetime import datetime

import deploy
import login
import pandas as pd
import requests
import streamlit as st
import theme

# The dashboard is implemented as its own process, so it loads .env itself rather than
# importing the API's config module.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard_auth  # noqa: E402  - the login credential, shared with the API
import env_file  # noqa: E402  - must run before the os.environ reads below
import paths  # noqa: E402

env_file.load()


def _default_server_url() -> str:
    """Best guess for the API's address when OPENPATCH_SERVER_URL is not set.
    """
    self_issued = os.path.join(paths.data_dir(), "certs", "ca.crt")
    scheme = "https" if os.path.exists(self_issued) else "http"
    host = os.environ.get("OPENPATCH_HOST", "").strip() or "127.0.0.1"
    if host == "0.0.0.0":
        host = "127.0.0.1"
    port = os.environ.get("OPENPATCH_PORT", "8000")
    return f"{scheme}://{host}:{port}"


SERVER_URL = os.environ.get("OPENPATCH_SERVER_URL", "").strip() or _default_server_url()
API = f"{SERVER_URL}/api/v1"

# One session so TLS verification is set once rather than at each call site.
SESSION = requests.Session()


def _ca_bundle() -> str:
    """Returrns the CA the API's certificate was signed by, or "" for the public ones.
    """
    from_env = os.environ.get("OPENPATCH_CA_BUNDLE", "").strip()
    if from_env:
        return from_env
    self_issued = os.path.join(paths.data_dir(), "certs", "ca.crt")
    return self_issued if os.path.exists(self_issued) else ""


CA_BUNDLE = _ca_bundle()
if CA_BUNDLE:
    SESSION.verify = CA_BUNDLE


def _admin_key() -> str:
    """Returns the admin key the API requires on every operator route.
    """
    from_env = os.environ.get("OPENPATCH_ADMIN_API_KEY", "").strip()
    if from_env:
        return from_env
    path = os.environ.get("OPENPATCH_ADMIN_KEY_FILE") or os.path.join(
        paths.data_dir(), "admin_key"
    )
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


ADMIN_KEY = _admin_key()
# Set on the session rather than per call
if ADMIN_KEY:
    SESSION.headers["X-Admin-Key"] = ADMIN_KEY


class _DropProactorResetNoise(logging.Filter):
    """Drops the traceback asyncio logs every time a browser tab drops its
    websocket.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        return not (
            isinstance(exc, ConnectionResetError)
            and "_call_connection_lost" in record.getMessage()
        )


def _silence_proactor_disconnect_noise() -> None:
    if sys.platform != "win32":
        return
    logger = logging.getLogger("asyncio")
    # Streamlit re-runs this script on every interaction
    if not any(isinstance(f, _DropProactorResetNoise) for f in logger.filters):
        logger.addFilter(_DropProactorResetNoise())


_silence_proactor_disconnect_noise()

ASSETS = paths.dashboard_assets_dir()
LOGO_BANNER = os.path.join(ASSETS, "logo_full.png")
LOGO_MARK = os.path.join(ASSETS, "logo_mark.png")

st.set_page_config(page_title="OpenPatch", page_icon=LOGO_MARK, layout="wide", initial_sidebar_state="expanded")
st.markdown(theme.css(), unsafe_allow_html=True)

# The login page before anything else runs
login.require_login(SERVER_URL)

# API helpers

def rows_digest(frame: pd.DataFrame, columns: list[str]) -> str:
    """Returns a short fingerprint of what a table is about to show.
    """
    present = [c for c in columns if c in frame.columns]
    if frame.empty or not present:
        return f"empty-{len(frame)}"
    joined = "|".join(frame[c].astype(str).str.cat(sep=",") for c in present)
    return hashlib.sha1(joined.encode("utf-8", "replace")).hexdigest()[:12]


def selected_rows(frame: pd.DataFrame, selection) -> pd.DataFrame:
    """Returns the ticked rows, guarded against a selection that outlived its table.
    """
    rows = [r for r in getattr(selection.selection, "rows", []) if 0 <= r < len(frame)]
    return frame.iloc[rows] if rows else frame.iloc[0:0]


def flash(message: str, icon: str = "✅") -> None:
    """Queues a toast for the next render.
    """
    st.session_state.setdefault("_flash", []).append((message, icon))


def render_flashes() -> None:
    for message, icon in st.session_state.pop("_flash", []):
        st.toast(message, icon=icon)


def invalidate_api_cache() -> None:
    """Drops cached API reads after a write.
    """
    fetch.clear()


def _auth_error(response) -> bool:
    """Report a rejected admin key.
    """
    if response.status_code not in (401, 403):
        return False
    st.error(
        "The OpenPatch API rejected this dashboard's admin key. Set "
        "OPENPATCH_ADMIN_API_KEY to the same value as the server, or point "
        "OPENPATCH_ADMIN_KEY_FILE at the key file the server generated "
        "(printed in its startup output)."
    )
    return True


@st.cache_data(ttl=15, show_spinner=False)
def fetch(path: str, params: dict | None = None):
    try:
        response = SESSION.get(f"{API}{path}", params=params, timeout=10)
        if _auth_error(response):
            return None
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        st.error(f"Could not reach OpenPatch server at {SERVER_URL}: {exc}")
        return None


def fetch_items(path: str, params: dict | None = None) -> list:
    """A paged list route's rows.
    """
    payload = fetch(path, params)
    if payload is None:
        return []
    return payload.get("items", []) if isinstance(payload, dict) else payload


def queue_task(device_id: str, action: str, target: str | None = None):
    """Runs as a widget callback.
    """
    params = {"action": action}
    if target:
        params["target"] = target
    try:
        response = SESSION.post(
            f"{API}/agent/{device_id}/queue_task", params=params, timeout=10
        )
        if response.status_code in (401, 403):
            flash("The API rejected this dashboard's admin key, check the server's startup output.", "⚠️")
            return
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            flash(data["error"], "⚠️")
        else:
            invalidate_api_cache()
            flash(f"Queued {action} (task #{data['task_id']})")
    except requests.RequestException as exc:
        flash(f"Failed to queue task: {exc}", "⚠️")


def queue_update(device_id: str, package_id: str, display_name: str) -> bool:
    """Queues an UPDATE_WINGET aimed at one package and returns whether it was
    accepted.
    """
    if not package_id or not str(package_id).strip():
        flash(f"{display_name} has no winget package id recorded.", "⚠️")
        return False
    try:
        response = SESSION.post(
            f"{API}/agent/{device_id}/queue_task",
            params={"action": "UPDATE_WINGET", "target": str(package_id).strip()},
            timeout=10,
        )
        response.raise_for_status()
        if "error" in response.json():
            flash(response.json()["error"], "⚠️")
            return False
        return True
    except requests.RequestException as exc:
        flash(f"Could not queue {display_name}: {exc}", "⚠️")
        return False


def cancel_tasks(**selector) -> None:
    """Dequeues tasks that have not run yet.
    """
    try:
        response = SESSION.post(f"{API}/tasks/cancel", json=selector, timeout=30)
        if response.status_code in (401, 403):
            flash("The API rejected this dashboard's admin key, check the server's startup output.", "⚠️")
            return
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        flash(f"Could not cancel tasks: {exc}", "⚠️")
        return

    invalidate_api_cache()

    if not data["cancelled"]:
        flash("Nothing to cancel, no matching task was still queued.", "ℹ️")
        return

    message = f"Cancelled {data['cancelled']} queued task(s)."
    if data["skipped_not_pending"]:
        message += f" {data['skipped_not_pending']} had already run."
    flash(message)


def _clear_selected_endpoint() -> None:
    st.query_params.clear()
    st.session_state.page = "Overview"


def open_endpoint(state_key: str, device_ids: list[str]) -> None:
    """Navigate to one endpoint's detail page, in place.
    """
    click = st.session_state.get(state_key)
    if not click:
        return
    row = click["row"]
    if row is None or not (0 <= row < len(device_ids)):
        return

    # Still written to the URL, so the detail page stays bookmarkable.
    st.query_params["device_id"] = device_ids[row]
    st.session_state.page = "Endpoint Detail"


def _install_selected(device_id: str, rows: list[dict]) -> None:
    """Queues one targeted UPDATE_WINGET per selected package.
    """
    queued = sum(1 for row in rows if queue_update(device_id, row["kb"], row["name"]))
    if queued:
        invalidate_api_cache()
        flash(f"Queued {queued} package install(s).")


@st.cache_data(ttl=300, show_spinner=False)
def fetch_rings() -> list[str]:
    """Ring names come from the API rather than a local constant, so the
    dropdowns can only ever offer values the server will accept."""
    data = fetch("/dashboard/rings")
    return (data or {}).get("rings") or []


def set_ring(device_id: str, ring: str) -> bool:
    try:
        response = SESSION.patch(
            f"{API}/agent/{device_id}/ring", json={"deployment_ring": ring}, timeout=10
        )
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        flash(f"Could not move {device_id} to {ring}: {exc}", "⚠️")
        return False


def save_verification_settings(device_id: str, verify_command: str, critical_programs: str) -> None:
    """Runs as a widget callback."""
    try:
        response = SESSION.patch(
            f"{API}/agent/{device_id}/verification",
            json={"verify_command": verify_command, "critical_programs": critical_programs},
            timeout=10,
        )
        response.raise_for_status()
        invalidate_api_cache()
        flash("Verification settings saved.")
    except requests.RequestException as exc:
        flash(f"Could not save the verification settings: {exc}", "⚠️")


def revert_endpoint(device_id: str, max_age_hours: int) -> None:
    """Queue a revert to the checkpoint taken before the last patch."""
    queue_task(device_id, "ROLLBACK", target=str(max_age_hours))


def revert_ring(ring_name: str, max_age_hours: int) -> None:
    """Revert a whole ring."""
    try:
        response = SESSION.post(
            f"{API}/tasks/revert/ring",
            json={"ring_name": ring_name, "max_age_hours": int(max_age_hours)},
            timeout=30,
        )
        if response.status_code == 404:
            flash(f"No endpoints are assigned to {ring_name} yet.", "⚠️")
            return
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        flash(f"Ring revert failed: {exc}", "⚠️")
        return

    invalidate_api_cache()
    flash(
        f"Queued a revert on {data['endpoints_targeted']} endpoint(s) in {ring_name}. "
        "Each restarts to apply it."
    )


def deploy_to_ring(ring_name: str, software_name: str):
    try:
        response = SESSION.post(
            f"{API}/tasks/remediate/ring",
            json={"ring_name": ring_name, "software_name": software_name},
            timeout=30,
        )
        if response.status_code == 404:
            flash(f"No endpoints are assigned to {ring_name} yet.", "⚠️")
            return
        response.raise_for_status()
        data = response.json()
        invalidate_api_cache()
        flash(
            f"Queued {data['action']} for {software_name} on "
            f"{data['endpoints_targeted']} endpoint(s) in {ring_name}."
        )
    except requests.RequestException as exc:
        flash(f"Ring deployment failed: {exc}", "⚠️")


# Task statuses the agent can report.
_STATUS_STYLES = {
    "SUCCESS_VERIFIED": f"background-color: {theme.PALETTE['success-tint']}; color: {theme.PALETTE['success']};",
    "SUCCESS": f"background-color: {theme.PALETTE['success-tint']}; color: {theme.PALETTE['success']};",
    "SUCCESS_WORKFLOW_FAILED": f"background-color: {theme.PALETTE['warning-tint']}; color: {theme.PALETTE['warning']};",
    "FAILED_AUTO_ROLLED_BACK": f"background-color: {theme.PALETTE['warning-tint']}; color: {theme.PALETTE['warning']};",
    "FAILED_ROLLBACK_FAILED": f"background-color: {theme.PALETTE['danger-tint']}; color: {theme.PALETTE['danger']};",
    "FAILED": f"background-color: {theme.PALETTE['danger-tint']}; color: {theme.PALETTE['danger']};",
    "PENDING": f"color: {theme.PALETTE['text-dim']};",
    "CANCELLED": f"color: {theme.PALETTE['text-dim']}; font-style: italic;",
}


def style_task_status(df: pd.DataFrame):
    """Colour the status column.
    """
    return df.style.map(lambda v: _STATUS_STYLES.get(v, ""), subset=["status"])


# Pages

def render_overview_page(endpoints: list[dict]):
    st.markdown(theme.section_title("server", "Fleet"), unsafe_allow_html=True)

    if not endpoints:
        st.info("No endpoints have checked in yet.")
        return

    df = pd.DataFrame(endpoints)

    group = st.session_state.get("fleet_filter")
    if group:
        df = apply_fleet_filter(df, group)
        heading, clear = st.columns([4, 1])
        with heading:
            st.markdown(
                theme.badge(f"{FLEET_FILTERS[group]} · {len(df)}", "warn"),
                unsafe_allow_html=True,
            )
        with clear:
            st.button(
                "Show all", key="op_clear_fleet_filter", width="stretch",
                on_click=select_fleet, args=(None,),
            )
        if df.empty:
            st.info(
                "No endpoints are in that group right now. The count on the card "
                "is recomputed on an interval, so it can be a minute ahead of this."
            )
            return
    df["status"] = df["online"].map({True: "\U0001F7E2 Online", False: "\U0001F534 Offline"})
    df["last_seen"] = pd.to_datetime(df["last_seen"])
    if "department" not in df.columns:
        df["department"] = None
    df["department"] = df["department"].fillna("Unassigned")
    df["hostname_action"] = df.apply(
        lambda r: str(r["hostname"] or r["device_id"]), axis=1
    )
    df["os_icon"] = df["os_version"].apply(theme.os_icon)
    if "os_name" not in df.columns:
        df["os_name"] = ""
    df["os_name"] = df["os_name"].fillna("")
    df["os_display"] = df.apply(
        lambda r: theme.os_display_name(r["os_name"], r["os_version"]), axis=1
    )

    for column, default in [
        ("reboot_required", False),
        ("windows_updates", 0),
        ("third_party_updates", 0),
    ]:
        if column not in df.columns:
            df[column] = default
        df[column] = df[column].fillna(default)

    df["reboot"] = df["reboot_required"].map(
        {True: "\U0001F534 Restart needed", False: "\U0001F7E2 Up to date"}
    )

    df["windows_action"] = df["windows_updates"].astype(int).astype(str)
    df["third_party_action"] = df["third_party_updates"].astype(int).astype(str)

    departments = sorted(df["department"].unique())
    if len(departments) > 1:
        selected = st.multiselect(
            "Filter by department", departments, default=departments, key="fleet_department_filter"
        )
        df = df[df["department"].isin(selected)]
        if df.empty:
            st.info("No endpoints match the selected departments.")
            return

    rings = fetch_rings()
    if "deployment_ring" not in df.columns:
        df["deployment_ring"] = rings[0] if rings else "Test-Ring"
    df["deployment_ring"] = df["deployment_ring"].fillna(rings[0] if rings else "Test-Ring")

    display_columns = [
        "os_icon", "hostname_action", "department", "deployment_ring", "status", "reboot",
        "windows_action", "third_party_action", "os_display", "cpu_usage", "ram_usage",
        "software_count", "last_seen",
    ]

    visible_device_ids = df["device_id"].tolist()

    with st.container(border=True):
        edited = st.data_editor(
            df[display_columns],
            width="stretch",
            hide_index=True,
            disabled=[c for c in display_columns if c != "deployment_ring"],
            key="fleet_ring_editor",
            column_config={
                "deployment_ring": st.column_config.SelectboxColumn(
                    "Ring",
                    options=rings,
                    required=True,
                    help="Deployment ring - patch the test ring first, then production",
                ),
                "os_icon": st.column_config.ImageColumn(
                    "OS", width=48, help="Operating system, derived from the build number the agent reports"
                ),

                "hostname_action": st.column_config.ButtonColumn(
                    "Hostname",
                    type="tertiary",
                    help="Open this endpoint's telemetry, inventory and actions",
                    on_click=open_endpoint,
                    kwargs={"state_key": "fleet_host_click", "device_ids": visible_device_ids},
                    key="fleet_host_click",
                ),
                "department": "Department",
                "status": "Status",
                "reboot": st.column_config.TextColumn(
                    "Reboot", help="Whether Windows has a restart outstanding on this endpoint"
                ),
                "windows_action": st.column_config.ButtonColumn(
                    "Windows Updates",
                    type="tertiary",
                    help="Pending Windows Update patches",
                    on_click=open_endpoint,
                    kwargs={"state_key": "fleet_windows_click", "device_ids": visible_device_ids},
                    key="fleet_windows_click",
                ),
                "third_party_action": st.column_config.ButtonColumn(
                    "3rd-Party",
                    type="tertiary",
                    help="Pending winget application upgrades",
                    on_click=open_endpoint,
                    kwargs={"state_key": "fleet_third_party_click", "device_ids": visible_device_ids},
                    key="fleet_third_party_click",
                ),
                "os_display": st.column_config.TextColumn(
                    "Operating System", help="Reported by the agent; edition is read on the endpoint itself"
                ),
                "cpu_usage": st.column_config.ProgressColumn("CPU", min_value=0, max_value=100, format="%.1f%%"),
                "ram_usage": st.column_config.ProgressColumn("RAM", min_value=0, max_value=100, format="%.1f%%"),
                "software_count": "Software Items",
                "last_seen": st.column_config.DatetimeColumn("Last Seen", format="YYYY-MM-DD HH:mm:ss"),
            },
        )

    changed = edited["deployment_ring"] != df.loc[edited.index, "deployment_ring"]
    if changed.any():
        moved = 0
        for index in edited.index[changed]:
            if set_ring(df.loc[index, "device_id"], edited.loc[index, "deployment_ring"]):
                moved += 1
        if moved:
            invalidate_api_cache()
            st.toast(f"Moved {moved} endpoint(s) to a new ring.", icon="✅")


def render_endpoint_detail_page(endpoints: list[dict]):
    st.markdown(theme.section_title("boxes", "Endpoint Detail"), unsafe_allow_html=True)

    device_lookup = {e["device_id"]: e for e in endpoints}
    device_id = st.query_params.get("device_id")

    if not device_id or device_id not in device_lookup:
        st.markdown(
            '<div class="op-hint">Select an endpoint from the Overview page to view its telemetry, inventory, and actions.</div>',
            unsafe_allow_html=True,
        )
        return

    endpoint = device_lookup[device_id]
    title_col, clear_col = st.columns([5, 1])
    with title_col:
        st.subheader(endpoint["hostname"])
        st.caption(
            f"{endpoint.get('department') or 'Unassigned'} · "
            f"{endpoint.get('deployment_ring') or 'Test-Ring'} · {endpoint['device_id']}"
        )
    with clear_col:
        st.button("✕ Clear", width="stretch", on_click=_clear_selected_endpoint)

    detail_col, action_col = st.columns([3, 1])

    if endpoint.get("reboot_required"):
        reasons = endpoint.get("reboot_reasons") or "reason not reported"
        st.warning(f"**Reboot required** — {reasons}", icon="\U0001F504")

    with action_col, st.container(border=True):
        st.markdown("**Actions**")
        # on_click keeps each of these to a single rerun.
        st.button("Queue Winget Update", width="stretch", type="primary",
                  on_click=queue_task, args=(device_id, "UPDATE_WINGET"))
        st.button("Update & Verify", width="stretch",
                  on_click=queue_task, args=(device_id, "UPDATE_AND_VERIFY"))
        st.button("Update, Verify & Auto-Heal", width="stretch",
                  on_click=queue_task, args=(device_id, "UPDATE_VERIFY_HEAL"))
        st.caption("Auto-heal reverts the machine to a pre-update restore point and reboots it if verification fails.")
        st.button("Queue OS Updates", width="stretch",
                  on_click=queue_task, args=(device_id, "UPDATE_OS"))

        st.divider()
        st.markdown("**Post-patch verification**")
        st.caption(
            "Runs right after Update & Verify, on this endpoint only. Exit "
            "non-zero, or raise an error, and the workflow reports the patch "
            "broke something - Auto-Heal then rolls it back automatically."
        )

        critical_programs = st.text_input(
            "Critical programs (comma-separated process names)",
            value=endpoint.get("critical_programs") or "",
            key=f"critical_programs_{device_id}",
            placeholder="e.g. AcmeApp, MyERPClient",
            help=(
                "Fails the check if any of these are not running - the same "
                "names Task Manager shows, not the .exe path."
            ),
        )
        # verify_command = st.text_area(
        #     "Or a custom PowerShell command, for anything the list above can't express",
        #     value=endpoint.get("verify_command") or "",
        #     key=f"verify_command_{device_id}",
        #     height=68,
        #     placeholder="e.g. Get-Process AcmeApp -ErrorAction Stop",
        #     help="Runs as SYSTEM. Set, it wins outright over the critical-programs list above.",
        # )
        # st.button(
        #     "Save verification settings", width="stretch",
        #     on_click=save_verification_settings, args=(device_id, verify_command, critical_programs),
        # )
        # if not verify_command and not critical_programs:
        #     st.caption("Nothing configured - the workflow will simply pass.")

        st.divider()
        st.markdown("**Revert**")
        window = st.selectbox(
            "Restore point not older than",
            [6, 12, 24, 72],
            format_func=lambda h: f"{h} hours",
            key="revert_window",
            help="A checkpoint older than this is refused.",
        )
        revert_ok = st.checkbox(
            "I understand this restores the machine and reboots it", key="confirm_revert"
        )
        st.button("Revert Last Update", width="stretch", disabled=not revert_ok,
                  on_click=revert_endpoint, args=(device_id, window))
        st.caption(
            "Restores the checkpoint taken before the last patch. Fails safely if no "
            "checkpoint from that window exists."
        )

        st.divider()
        confirmed = st.checkbox("I understand this will restart the machine", key="confirm_restart")
        st.button("Restart Endpoint", width="stretch", disabled=not confirmed,
                  on_click=queue_task, args=(device_id, "RESTART"))
        st.caption("The user gets a 60-second Windows warning before it restarts.")

    with detail_col, st.container(border=True):
        telemetry = fetch(f"/dashboard/endpoints/{device_id}/telemetry", params={"limit": 200}) or []
        if telemetry:
            tdf = pd.DataFrame(telemetry)
            tdf["recorded_at"] = pd.to_datetime(tdf["recorded_at"])
            tdf = tdf.set_index("recorded_at").rename(columns={"cpu_usage": "CPU %", "ram_usage": "RAM %"})
            st.caption("CPU / RAM usage (recent samples)")
            st.line_chart(tdf[["CPU %", "RAM %"]])
        else:
            st.info("No telemetry history yet for this endpoint.")

    sub_updates, sub_inv, sub_tasks = st.tabs(["Pending Updates", "Software Inventory", "Task History"])

    with sub_updates:
        updates = fetch(f"/dashboard/endpoints/{device_id}/updates") or []
        if not updates:
            st.markdown(
                '<div class="op-hint">No pending updates recorded.'
                "</div>",
                unsafe_allow_html=True,
            )
        else:
            udf = pd.DataFrame(updates)
            udf["source"] = udf["source"].map(
                {"windows": "\U0001FA9F Windows Update", "winget": "\U0001F4E6 Third-party"}
            ).fillna(udf["source"])
            source_options = sorted(udf["source"].unique())
            if len(source_options) > 1:
                chosen = st.multiselect(
                    "Filter by source", source_options, default=source_options, key="updates_source_filter"
                )
                udf = udf[udf["source"].isin(chosen)]

            st.caption("Check the rows you want, then install. Leave everything unchecked to patch all pending upgrades at once.")

            selection = st.dataframe(
                udf[["source", "name", "kb", "current_version", "available_version", "severity"]],
                width="stretch",
                hide_index=True,
                on_select="rerun",
                selection_mode="multi-row",
                key=f"pending_updates_table_{rows_digest(udf, ['source', 'name', 'kb'])}",
                column_config={
                    "source": "Source",
                    "name": "Update",
                    "kb": st.column_config.TextColumn(
                        "KB / Package", help="KB article for Windows Update, package id for winget"
                    ),
                    "current_version": "Installed",
                    "available_version": "Available",
                    "severity": "Severity",
                },
            )

            picked = selected_rows(udf, selection)
            installable = picked[picked["source"].str.contains("Third-party")]
            windows_picked = picked[~picked["source"].str.contains("Third-party")]

            install_col, all_col = st.columns([1, 1])

            with install_col:
                label = (
                    f"Install {len(installable)} selected"
                    if len(installable) else "Install selected"
                )
                st.button(label, width="stretch", type="primary", disabled=installable.empty,
                          on_click=_install_selected,
                          args=(device_id, installable[["kb", "name"]].to_dict("records")))

            with all_col:
                st.button("Install all winget upgrades", width="stretch",
                          on_click=queue_task, args=(device_id, "UPDATE_WINGET"))

            if not windows_picked.empty:
                st.warning(
                    f"{len(windows_picked)} selected item(s) are part of Windows Update and cannot be "
                    "installed individually."
                    "Use the Queue OS Update action instead.",
                    icon="\U0001FA9F",
                )

    with sub_inv:
        inventory = fetch_items(f"/dashboard/endpoints/{device_id}/inventory")
        if inventory:
            st.dataframe(pd.DataFrame(inventory), width="stretch", hide_index=True)
        else:
            st.info("No software inventory recorded yet.")

    with sub_tasks:
        show_cancelled = st.toggle(
            "Show cancelled", value=False, key="device_show_cancelled",
            help="Cancelled tasks stay in the history but are kept out of this list by default.",
        )
        tasks = fetch(f"/dashboard/endpoints/{device_id}/tasks",
                      params={"include_cancelled": show_cancelled}) or []
        if tasks:
            tdf = pd.DataFrame(tasks)
            tdf["created_at"] = pd.to_datetime(tdf["created_at"])
            if "target" not in tdf.columns:
                tdf["target"] = None

            queued = int((tdf["status"] == "PENDING").sum())
            if queued:
                clear_col, note_col = st.columns([1, 3])
                with clear_col:
                    st.button(
                        f"Cancel {queued} queued task(s)", width="stretch",
                        on_click=cancel_tasks, kwargs={"device_id": device_id},
                    )
                with note_col:
                    st.caption(
                        "Dequeues work this endpoint has not started. A task already running "
                        "finishes and reports its result."
                    )

            st.dataframe(
                style_task_status(tdf[["created_at", "action", "target", "status", "output"]]),
                width="stretch",
                hide_index=True,
                column_config={"target": st.column_config.TextColumn(
                    "Target", help="Software a ring deployment was targeted at, when the action targets one"
                )},
            )
        elif show_cancelled:
            st.info("No tasks queued for this endpoint yet.")
        else:
            st.info(
                "No active tasks for this endpoint. Switch on Show cancelled if you "
                "are looking for tasks that were dequeued."
            )


def _humanize_age(value) -> str:
    """Returns a timestamp as "x units ago".
    """
    if not value:
        return "never"
    try:
        when = pd.Timestamp(value)
    except (ValueError, TypeError):
        return str(value)
    if when.tzinfo is None:
        when = when.tz_localize("UTC")
    seconds = max(0, (pd.Timestamp.now("UTC") - when).total_seconds())
    for limit, divisor, unit in (
        (90, 1, "second"), (5400, 60, "minute"), (172800, 3600, "hour"), (None, 86400, "day"),
    ):
        if limit is None or seconds < limit:
            count = int(seconds // divisor)
            return f"{count} {unit}{'' if count == 1 else 's'} ago"
    return str(value)


def render_scan_status() -> None:
    """Returns how current the scan report is.
    """
    status = fetch("/dashboard/scan-status") or {}
    if not status:
        return

    if not status.get("enabled"):
        st.warning(
            "Automatic scanning is disabled on the server (OPENPATCH_SCAN_ENABLED=0). "
            "The findings below are whatever was last scanned and nothing newer.",
            icon="⚠️",
        )
        return

    pending = status.get("products_pending", 0)
    unidentified = status.get("names_unidentified", 0)
    interval = int(status.get("interval_seconds") or 0)

    if status.get("running"):
        detail = f"Scanning now, started {_humanize_age(status.get('started_at'))}."
    elif status.get("last_scanned_at"):
        detail = f"Last scanned {_humanize_age(status.get('last_scanned_at'))}."
    else:
        st.info(
            f"No scan has completed yet. The server scans automatically every "
            f"{interval // 60} minute(s) and the first run starts shortly after it "
            "boots - the findings below will be empty until then.",
            icon="🕒",
        )
        return

    covered = (
        f"{status.get('products_scanned', 0)} of {status.get('products_resolved', 0)} "
        "identified product(s) checked for CVEs"
    )
    parts = [detail, covered]
    if pending:
        parts.append(f"{pending} due for a re-check")
    if unidentified:
        parts.append(f"{unidentified} name(s) with no NVD match, so not represented below")
    if interval:
        parts.append(f"scans run automatically every {interval // 60} minute(s)")

    st.caption(" · ".join(parts))

    if status.get("last_error"):
        st.warning(f"The last scan did not finish: {status['last_error']}", icon="⚠️")


def _severity_badge(row) -> str:
    """One glanceable status per product."""
    if not row.get("cve_scanned"):
        return "⚪ Not scanned"
    severity = row.get("max_severity")
    severity = "" if pd.isna(severity) else str(severity).upper()
    return {
        "CRITICAL": "\U0001F534 Critical",
        "HIGH": "\U0001F7E0 High",
        "MEDIUM": "\U0001F7E1 Medium",
        "LOW": "\U0001F535 Low",
    }.get(severity, "\U0001F7E2 None found")


def _eol_state(value, today):
    if value is True:
        return "\U0001F534 Past end of support"
    try:
        when = pd.Timestamp(str(value))
    except (ValueError, TypeError):
        return "\U0001F7E0 Scheduled"
    return "\U0001F534 Past end of support" if when <= today else f"\U0001F7E0 Ends {when.date()}"


def _render_ring_deployment(df):
    st.markdown(theme.section_title("boxes", "Deploy Patch to Ring"), unsafe_allow_html=True)

    with st.container(border=True):
        st.caption(
            "Staged rollout: patch the test ring, confirm nothing broke, then run the "
            "same deployment against production."
        )
        rings = fetch_rings()
        if not rings:
            st.error("Could not load deployment rings from the server.")
            return

        software_options = sorted(df["software"].dropna().unique())
        if not software_options:
            st.info("No software detected to deploy.")
            return

        col_software, col_ring, col_button = st.columns([3, 2, 1])
        with col_software:
            software_name = st.selectbox("Software", software_options, key="ring_software")
        with col_ring:
            ring_name = st.selectbox("Select Target Ring", rings, key="ring_target")
        with col_button:
            st.write("")
            st.write("")
            st.button("Deploy Patch to Ring", width="stretch", type="primary",
                      on_click=deploy_to_ring, args=(ring_name, software_name))

        st.divider()
        st.caption(
            "If a rollout goes wrong, put the ring back."
        )
        col_window, col_confirm, col_revert = st.columns([2, 3, 2])
        with col_window:
            revert_window = st.selectbox(
                "Restore point no older than",
                [6, 12, 24, 72],
                format_func=lambda h: f"{h} hours",
                key="ring_revert_window",
            )
        with col_confirm:
            st.write("")
            ring_revert_ok = st.checkbox(
                f"I understand this restarts every endpoint in {ring_name}",
                key="confirm_ring_revert",
            )
        with col_revert:
            st.write("")
            st.button("Revert Ring", width="stretch", disabled=not ring_revert_ok,
                      on_click=revert_ring, args=(ring_name, revert_window))


def render_vulnerabilities_page():
    st.markdown(theme.section_title("shield", "Vulnerabilities & EOL"), unsafe_allow_html=True)

    findings = fetch("/dashboard/findings") or []
    if not findings:
        st.info("No resolved software yet. Run an EOL scan from an endpoint to populate this view.")
        return

    grouped = pd.DataFrame(findings)
    for column, default in [
        ("is_eol", None), ("support_until", None), ("cve_scanned", False),
        ("cve_count", 0), ("max_severity", None), ("max_score", None),
        ("top_cves", ""), ("cve_match_mode", ""), ("lifecycle_cycle", None),
        ("installed", ""), ("endpoints", 0),
    ]:
        if column not in grouped.columns:
            grouped[column] = default
    grouped["cve_count"] = grouped["cve_count"].fillna(0).astype(int)
    grouped["endpoints"] = grouped["endpoints"].fillna(0).astype(int)
    grouped["cve_scanned"] = grouped["cve_scanned"].fillna(False).astype(bool)
    for column in ("max_severity", "top_cves", "cve_match_mode", "installed"):
        grouped[column] = grouped[column].where(grouped[column].notna(), "")

    grouped["status"] = grouped.apply(_severity_badge, axis=1)
    rank = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
    grouped["_rank"] = grouped["max_severity"].map(lambda v: rank.get((v or "").upper(), 0))
    grouped["confidence"] = grouped["cve_match_mode"].map(
        {"version": "Version-matched", "product": "Product-level"}
    ).fillna("-")

    vulnerable = grouped[grouped["_rank"] >= 3].sort_values(["_rank", "max_score"], ascending=False)
    unscanned = int((~grouped["cve_scanned"]).sum())

    st.markdown(
        theme.kpi_row([
            theme.kpi_card("alert-triangle", "Critical", int((grouped["_rank"] == 4).sum()),
                           accent="danger" if (grouped["_rank"] == 4).any() else "neutral"),
            theme.kpi_card("shield", "High", int((grouped["_rank"] == 3).sum()),
                           accent="warning" if (grouped["_rank"] == 3).any() else "neutral"),
            theme.kpi_card("boxes", "Products tracked", len(grouped), accent="accent"),
            theme.kpi_card("clock", "Not yet scanned", unscanned,
                           accent="warning" if unscanned else "neutral"),
        ]),
        unsafe_allow_html=True,
    )

    render_scan_status()

    st.markdown(theme.section_title("alert-triangle", "Critical & high severity"), unsafe_allow_html=True)
    if vulnerable.empty:
        st.markdown(
            '<div class="op-hint">No critical or high severity CVEs among the products scanned so far.</div>',
            unsafe_allow_html=True,
        )
    else:
        with st.container(border=True):
            st.dataframe(
                vulnerable[["status", "software", "installed", "endpoints", "cve_count", "top_cves", "confidence"]],
                width="stretch", hide_index=True,
                column_config={
                    "status": "Severity",
                    "software": "Product",
                    "installed": "Installed",
                    "endpoints": st.column_config.NumberColumn("Endpoints"),
                    "cve_count": st.column_config.NumberColumn("CVEs"),
                    "top_cves": st.column_config.TextColumn("Worst CVEs"),
                    "confidence": st.column_config.TextColumn(
                        "Match",
                        help="Version-matched applies to the installed version. Product-level means "
                             "the CPE match never confirmed a version, so the CVE affects the product "
                             "but not necessarily this build.",
                    ),
                },
            )
        if (vulnerable["confidence"] == "Product-level").any():
            st.caption(
                "Product-level rows list CVEs known for the product across all versions - treat them "
                "as leads to confirm, not as proof this build is affected."
            )

    st.markdown(theme.section_title("clock", "End of support"), unsafe_allow_html=True)
    eol = grouped[
        grouped["is_eol"].apply(lambda v: pd.notna(v) and v is not False)
    ].copy()
    if eol.empty:
        st.markdown(
            '<div class="op-hint">No tracked product has an end-of-support date recorded.</div>',
            unsafe_allow_html=True,
        )
    else:
        today = pd.Timestamp.today().normalize()
        eol["lifecycle"] = eol["is_eol"].apply(lambda v: _eol_state(v, today))
        with st.container(border=True):
            st.dataframe(
                eol[["lifecycle", "software", "installed", "lifecycle_cycle", "endpoints", "support_until"]],
                width="stretch", hide_index=True,
                column_config={
                    "lifecycle": "Status",
                    "software": "Product",
                    "installed": "Installed",
                    "lifecycle_cycle": "Release",
                    "endpoints": st.column_config.NumberColumn("Endpoints"),
                    "support_until": "Active support until",
                },
            )

    with st.expander(f"All tracked products ({len(grouped)})"):
        st.dataframe(
            grouped[["status", "software", "installed", "endpoints", "cve_count", "confidence"]],
            width="stretch", hide_index=True,
            column_config={"status": "Severity", "software": "Product", "installed": "Installed",
                           "endpoints": st.column_config.NumberColumn("Endpoints"),
                           "cve_count": st.column_config.NumberColumn("CVEs"),
                           "confidence": "Match"},
        )

    _render_ring_deployment(grouped)


FLEET_FILTERS = {
    "online": "Online endpoints",
    "offline": "Offline endpoints",
    "eol": "Endpoints nearing or past EOL",
    "critical": "Endpoints already past EOL",
}


def select_fleet(group: str | None) -> None:
    """Show the fleet, filtered to one group.
    """
    if group and st.session_state.get("fleet_filter") == group:
        group = None
    st.session_state.fleet_filter = group
    st.session_state.page = "Overview"
    st.query_params.clear()


def render_kpis(summary: dict) -> None:
    """The row of counts, four of which are buttons.
    """
    cards = [
        ("server", "Endpoints", summary["total_endpoints"], "accent", None, None),
        ("wifi", "Online", summary["online"], "success", None, "online"),
        (
            "wifi-off", "Offline", summary["offline"],
            "danger" if summary["offline"] else "neutral", None, "offline",
        ),
        (
            "boxes", "EOL Endpoints", summary["eol_endpoints"],
            "warning" if summary["eol_endpoints"] else "neutral",
            "nearing or past EOL", "eol",
        ),
        (
            "alert-triangle", "Critical Endpoints", summary["critical_endpoints"],
            "danger" if summary["critical_endpoints"] else "neutral",
            "already past EOL", "critical",
        ),
        (
            "clock", "Pending Tasks", summary["pending_tasks"],
            "warning" if summary["pending_tasks"] else "neutral",
            f"{summary['failed_tasks']} failed" if summary["failed_tasks"] else None,
            None,
        ),
    ]


    for column, (icon, label, value, accent, sub, group) in zip(
        st.columns(len(cards)), cards, strict=True
    ):
        with column, st.container(key=f"op_kpi_{group or label.lower().replace(' ', '_')}"):
            st.markdown(
                theme.kpi_card(icon, label, value, accent=accent, sub=sub),
                unsafe_allow_html=True,
            )
            if group:
                st.button(
                    f"Show {FLEET_FILTERS[group].lower()}",
                    key=f"op_fleet_pick_{group}",
                    on_click=select_fleet,
                    args=(group,),
                )


@st.cache_data(ttl=60, show_spinner=False)
def at_risk_device_ids(level: str) -> list:
    """Which endpoints are behind the EOL and Critical counts.
    """
    payload = fetch("/dashboard/at-risk", {"level": level})
    return (payload or {}).get("device_ids", [])


def apply_fleet_filter(df: pd.DataFrame, group: str | None) -> pd.DataFrame:
    """The fleet, narrowed to the group whose card was clicked."""
    if group == "online":
        return df[df["online"]]
    if group == "offline":
        return df[~df["online"]]
    if group in ("eol", "critical"):
        return df[df["device_id"].isin(at_risk_device_ids(group))]
    return df


def render_deploy_page():
    """Hand the operator the CA, and the exact commands for this server.
    """
    st.markdown(theme.section_title("shield", "Deploy agents"), unsafe_allow_html=True)

    st.markdown(
        theme.hint(
            "One zip is the whole deployment: the agent, the authority it should "
            "trust, and the commands that install it. An agent cannot learn which "
            "authority to trust from the server it is authenticating - an impostor "
            "would present its own - so the CA travels this way instead."
        ),
        unsafe_allow_html=True,
    )
    st.write("")

    agent = deploy.agent_path(paths.bundle_dir(), paths.source_root())

    left, right = st.columns([3, 2])
    with left:
        server_url = st.text_input(
            "Address your endpoints will dial",
            value=deploy.suggested_url(
                public_url=os.environ.get("OPENPATCH_PUBLIC_URL", ""),
                public_hosts=os.environ.get("OPENPATCH_PUBLIC_HOST", ""),
                server_url=SERVER_URL,
                port=os.environ.get("OPENPATCH_PORT", "8000"),
            ),
            help=(
                "This goes into the enrolment command. It must be reachable from "
                "the endpoint and covered by the server's certificate - not the "
                "dashboard's own view of the API."
            ),
        )
    with right:
        st.write("")
        st.write("")
        st.markdown(
            theme.badge(
                f"Agent · {os.path.getsize(agent) / (1024 * 1024):.0f} MB" if agent
                else "No agent in this build",
                "online" if agent else "warn",
            )
            + " "
            + theme.badge(
                "CA included" if CA_BUNDLE else "No CA to include",
                "online" if CA_BUNDLE else "warn",
            ),
            unsafe_allow_html=True,
        )

    if not agent:
        st.warning(
            "No agent executable was found, so the bundle carries the CA and "
            "the instructions only.",
            icon="⚠️",
        )
    if not CA_BUNDLE:
        st.warning(
            "This dashboard has no CA certificate, which means the API is being "
            "served over plain HTTP or with a publicly-trusted certificate. The "
            "bundle will carry no authority to trust.",
            icon="⚠️",
        )
    try:
        bundle = deploy.build_bundle(server_url, CA_BUNDLE or None, agent or None)
    except deploy.UnsafeServerUrl as refusal:
        st.error(str(refusal), icon="🚫")
        bundle = None

    st.download_button(
        "⬇  Download deployment bundle",
        data=bundle or b"",
        file_name=deploy.bundle_name(server_url),
        mime="application/zip",
        type="primary",
        disabled=bundle is None,
    )
    if agent:
        st.caption(
            f"Agent built {datetime.fromtimestamp(os.path.getmtime(agent)):%d %b %Y}"
            f" · rebuild it and rebuild the image to ship a newer one."
        )

    st.caption(
        "Contains openpatch-agent.exe, ca.crt, an elevated installer and written "
        "instructions. No secrets: the enrolment and task signing secrets never "
        "reach this process, and the bundle says where to put them."
    )


def render_tasks_page(endpoints: list[dict]):
    st.markdown(theme.section_title("clock", "Task Queue"), unsafe_allow_html=True)

    if not endpoints:
        st.info("No endpoints yet.")
        return

    with st.container(border=True):
        st.markdown("**Queue a Task**")
        options = {f"{e['hostname']} ({e['device_id']})": e["device_id"] for e in endpoints}
        col1, col2, col3 = st.columns([2, 2, 1])
        with col1:
            selected_label = st.selectbox("Endpoint", list(options.keys()), key="task_endpoint")
        with col2:
            action = st.selectbox(
                "Action",
                ["UPDATE WINGET", "UPDATE & VERIFY", "UPDATE, VERIFY & HEAL", "UPDATE OS", "RESTART"],
                key="task_action",
            )
        with col3:
            st.write("")
            st.write("")
            if st.button("Queue", width="stretch", type="primary"):
                queue_task(options[selected_label], action)

    st.markdown("<div style='height:0.6rem'></div>", unsafe_allow_html=True)
    st.markdown("**Recent Tasks Across Fleet**")

    show_cancelled = st.toggle(
        "Show cancelled", value=False, key="fleet_show_cancelled",
        help="Cancelled tasks stay in the history but are kept out of the queue by default.",
    )

    payload = fetch("/dashboard/tasks",
                    params={"limit": 500, "include_cancelled": show_cancelled}) or {}
    all_tasks = payload.get("items", [])
    hidden = payload.get("cancelled_hidden", 0)

    if not all_tasks:
        if hidden:
            st.info(f"No active tasks. {hidden} cancelled task(s) are hidden - "
                    "switch on Show cancelled to see them.")
        else:
            st.info("No tasks queued yet.")
        return

    df = pd.DataFrame(all_tasks)
    df["created_at"] = pd.to_datetime(df["created_at"])
    df = df.sort_values("created_at", ascending=False)
    if "target" not in df.columns:
        df["target"] = None

    pending_total = int((df["status"] == "PENDING").sum())

    statuses = sorted(df["status"].unique())
    status_filter = st.multiselect("Filter by status", statuses, default=statuses)
    df = df[df["status"].isin(status_filter)].reset_index(drop=True)

    caption = (
        f"{pending_total} task(s) still queued across the fleet. Tick rows to dequeue them - "
        "only tasks that have not run yet can be cancelled, and a task an agent has already "
        "started runs to completion."
    )
    if hidden:
        caption += f" {hidden} cancelled task(s) hidden."
    st.caption(caption)

    with st.container(border=True):
        selection = st.dataframe(
            style_task_status(df[["created_at", "hostname", "action", "target", "status", "output"]]),
            width="stretch",
            hide_index=True,
            on_select="rerun",
            selection_mode="multi-row",
            key=f"fleet_tasks_table_{rows_digest(df, ['id', 'status'])}",
            column_config={
                "created_at": st.column_config.DatetimeColumn("Queued At", format="YYYY-MM-DD HH:mm:ss"),
                "hostname": "Endpoint",
                "action": "Action",
                "target": "Target",
                "status": "Status",
                "output": "Output",
            },
        )

    picked = selected_rows(df, selection)
    cancellable = picked[picked["status"] == "PENDING"]
    already_run = len(picked) - len(cancellable)

    cancel_col, ring_col, all_col = st.columns([2, 2, 2])

    with cancel_col:
        label = f"Cancel {len(cancellable)} selected" if len(cancellable) else "Cancel selected"
        st.button(
            label, width="stretch", type="primary", disabled=cancellable.empty,
            on_click=cancel_tasks,
            kwargs={"task_ids": [int(i) for i in cancellable["id"]]},
        )
        if already_run:
            st.caption(f"{already_run} selected row(s) have already run and cannot be cancelled.")

    with ring_col:
        rings = fetch_rings()
        if rings:
            ring_name = st.selectbox("Ring", rings, key="cancel_ring", label_visibility="collapsed")
            st.button(
                f"Clear queue for {ring_name}", width="stretch",
                on_click=cancel_tasks, kwargs={"ring_name": ring_name},
            )
            st.caption("The undo for a ring deployment fired at the wrong ring.")

    with all_col:
        confirmed = st.checkbox("I want to clear the entire queue", key="confirm_clear_queue")
        st.button(
            "Clear all pending tasks", width="stretch", disabled=not confirmed,
            on_click=cancel_tasks, kwargs={"all_pending": True},
        )


# Sidebar navigation

NAV_PAGES = ["Overview", "Endpoint Detail", "Vulnerabilities & EOL", "Task Queue", "Deploy agents"]

if "page" not in st.session_state:
    st.session_state.page = NAV_PAGES[0]

if st.query_params.get("device_id") and st.session_state.page != "Endpoint Detail":
    st.session_state.page = "Endpoint Detail"

with st.sidebar:
    st.image(LOGO_BANNER, width=340)
    st.markdown('<div class="op-brand"><span>OpenPatch</span></div>', unsafe_allow_html=True)
    st.caption("Patch & vulnerability management")
    st.write("")

    for page_name in NAV_PAGES:
        is_active = st.session_state.page == page_name
        st.button(
            page_name,
            key=f"nav_{page_name}",
            width="stretch",
            type="primary" if is_active else "secondary",
            on_click=st.session_state.__setitem__,
            args=("page", page_name),
        )

    st.write("")
    st.divider()
    st.button("\U0001F504 Refresh", width="stretch", on_click=invalidate_api_cache)
    st.button("\U0001F513 Sign out", width="stretch", on_click=login.sign_out)
    login.render_password_change()
    st.caption(f"Signed in as {dashboard_auth.USERNAME}")
    st.caption(f"Connected to {SERVER_URL}")
    st.caption(f"Last updated {datetime.now().strftime('%H:%M:%S')}")

# Main content

render_flashes()

summary = fetch("/dashboard/summary")
if summary is None:
    st.stop()

clock_left, clock_mid, clock_right = st.columns([1, 2, 1])
with clock_mid:
    st.iframe(theme.clock_card_html(), height=130)
st.write("")

render_kpis(summary)

endpoints = fetch_items("/dashboard/endpoints")

if st.session_state.page == "Overview":
    render_overview_page(endpoints)
elif st.session_state.page == "Endpoint Detail":
    render_endpoint_detail_page(endpoints)
elif st.session_state.page == "Vulnerabilities & EOL":
    render_vulnerabilities_page()
elif st.session_state.page == "Task Queue":
    render_tasks_page(endpoints)
elif st.session_state.page == "Deploy agents":
    render_deploy_page()