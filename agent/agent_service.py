
import os
import sys
import json
import time
import tasks
import random
import requests
import threading
import telemetry
import contextlib
import server_api
import enrollment
import agent_paths
import http_client
import agent_config
import task_signing
import script_runner

ALLOW_UNELEVATED_ENV = "OPENPATCH_ALLOW_UNELEVATED"

NOT_ELEVATED_MESSAGE = rf"""
[X] The OpenPatch agent must run elevated. Install it as a SYSTEM scheduled task as outlined \
 in the Read the README.txt file. """

NOT_ENROLLED_MESSAGE = f"""
[X] This device is not enrolled with an OpenPatch server.
    Enrol and start the agent again following the instructions outlined in the README.txt file.
"""


def require_elevation() -> None:
    """Makes the agent to refuse to start without elevation.
    """
    if telemetry.is_elevated():
        return
    if os.environ.get(ALLOW_UNELEVATED_ENV, "").strip() in ("1", "true", "True"):
        print(
            "[!] Running WITHOUT elevation because "
            f"{ALLOW_UNELEVATED_ENV} is set. Patching, restore points, rollback "
            "and remote restart will not work on this machine.",
            flush=True,
        )
        return
    sys.exit(NOT_ELEVATED_MESSAGE)


def load_config() -> agent_config.AgentConfig:
    """Fetches gent config.
    """
    config = agent_config.load()
    if config is None:
        sys.exit(NOT_ENROLLED_MESSAGE)
    return config


def reregister(api: server_api.ServerAPI) -> bool:
    """Re-enrol after the server stops recognising our token.
    """
    secret = agent_config.get_option("enrollment_secret")
    if not secret and agent_config.ENROLLMENT_SECRET_REQUIRED:
        print(
            "[X] The server rejected the token and no enrolment secret is provisioned "
            "on this endpoint, so it cannot re-enrol itself. Re-enrol it with: "
            f"{agent_paths.program_name()} enroll --server <url> --force",
            flush=True,
        )
        return False

    print("[!] Server rejected the token - re-enrolling...", flush=True)
    existing = agent_config.load()
    try:
        config = enrollment.register(
            server_url=api.server_url,
            department=existing.department if existing else "",
            enrollment_secret=secret,
        )
    except requests.RequestException as exc:
        print(f"[X] Automatic re-enrolment failed: {type(exc).__name__}: {exc}", flush=True)
        return False

    api.set_credentials(config.server_url, config.token)
    print(f"[OK] Re-enrolled as device {config.device_id}", flush=True)
    return True


# polling
# Fallback cadence until the server states its own.
DEFAULT_POLL_INTERVAL = 30

# Bounds on what the server may ask for, so a bad value can neither hammer
# the server nor effectively silence this agent.
MIN_POLL_INTERVAL = 5
MAX_POLL_INTERVAL = 900


def next_sleep(interval: int) -> float:
    """Returns the poll interval with up to 10% jitter added.
    """
    return interval + random.uniform(0, interval * 0.1)


def apply_poll_interval(data: dict, current: int) -> int:
    """Returns the cadence the server asked for, clamped, and only when it sent one."""
    requested = data.get("poll_interval")
    if not isinstance(requested, (int, float)) or isinstance(requested, bool):
        return current
    return int(max(MIN_POLL_INTERVAL, min(int(requested), MAX_POLL_INTERVAL)))


def task_list_is_trusted(device_id: str, tasks_received, headers) -> bool:
    """Returns whether a received task list may be executed.
    """
    secret = agent_config.task_signing_secret()
    if not secret:
        return True

    accepted, reason = task_signing.verify(device_id, tasks_received, headers, secret)
    if accepted:
        return True

    print(
        "\n[!!] WARNING: Invalid signature detected!\n"
        f"     Reason: {reason}\n"
        f"     Dropped {len(tasks_received)} unverified task(s) without executing them.\n",
        flush=True,
    )
    return False


def task_was_cancelled(api: server_api.ServerAPI, task_id: int) -> bool:
    """Returns whether the server has since dequeued a task we were already handed.
    """
    try:
        response = api.task_status(task_id)
        if response.status_code != 200:
            return False
        return response.json().get("status") == "CANCELLED"
    except requests.RequestException:
        return False


def run_pending_tasks(api: server_api.ServerAPI, pending) -> None:
    """Executes a verified task list, reporting each result as it finishes."""
    print(f"[*] Found {len(pending)} pending task(s).", flush=True)

    for position, task in enumerate(pending):
        task_id = task["task_id"]
        action = task["action"]

        if position > 0 and task_was_cancelled(api, task_id):
            print(f"[-] Task {task_id} ({action}) was cancelled - skipping.", flush=True)
            continue

        status, log_output = tasks.execute(action, task.get("target"))

        # UPDATE-VERIFY-HEAL and ROLLBACK restart the machine, which
        # can kill this process before the result below is reported.
        api.report_task_result(task_id, status, log_output)
        print(f"[OK] Task {task_id} completed with status: {status}\n", flush=True)


def start_polling(api: server_api.ServerAPI) -> None:
    """Sends Heartbeat, collect tasks, run them in a loop."""
    poll_interval = DEFAULT_POLL_INTERVAL

    while True:
        try:
            payload = telemetry.collect(api.device_id)
            print(
                f"[>] Sending Heartbeat... (CPU: {payload['cpu_usage']}% | "
                f"RAM: {payload['ram_usage']}%)",
                flush=True,
            )

            response = api.heartbeat(payload)

            if response.status_code == 200:
                data = response.json()

                previous = poll_interval
                poll_interval = apply_poll_interval(data, poll_interval)
                if poll_interval != previous:
                    print(f"[*] Poll interval set by server: {poll_interval}s", flush=True)

                pending = data.get("pending_tasks", [])

                if pending and not task_list_is_trusted(api.device_id, pending, response.headers):
                    pending = []

                if pending:
                    run_pending_tasks(api, pending)

            elif response.status_code == 401:
                reregister(api)
            else:
                print(f"[!] Server returned error code: {response.status_code}", flush=True)

        except requests.exceptions.ConnectionError:
            print(
                f"[X] Connection refused. Is the Central server running on {api.server_url}?",
                flush=True,
            )
        except Exception as exc:
            print(f"[X] Unexpected error in polling loop: {type(exc).__name__}: {exc}", flush=True)

        time.sleep(next_sleep(poll_interval))


# reporting
UPDATE_SCAN_INTERVAL_SECONDS = 3600


def _parse_script_json(output: str, what: str):
    """Parses JSON a PowerShell script wrote to stdout.
    Since PowerShell may prefix its output with a UTF-8 BOM, which is not
    whitespace and so survives an ordinary strip.
    """
    try:
        return json.loads(output.lstrip("\ufeff"))
    except ValueError as exc:
        print(f"[!] {what} returned output that is not JSON: {exc}", flush=True)
        return None


def send_software_inventory(api: server_api.ServerAPI) -> None:
    """Scans installed software and report it.
    """
    print("[*] Scanning installed software...", flush=True)
    code, output = script_runner.run("get_software.ps1")
    if code != 0:
        print(f"[!] Software scan failed: {output[:300]}", flush=True)
        return

    software_list = _parse_script_json(output, "get_software.ps1")
    if software_list is None:
        return

    try:
        response = api.send_inventory(software_list)
    except requests.RequestException as exc:
        print(f"[!] Could not send inventory: {type(exc).__name__}: {exc}", flush=True)
        return

    if response.status_code == 200:
        print(f"[OK] Successfully sent {len(software_list)} apps to the server!", flush=True)
    elif response.status_code == 401:
        reregister(api)
    else:
        print(f"[!] Server rejected inventory: {response.text[:200]}", flush=True)


def send_pending_updates(api: server_api.ServerAPI) -> None:
    """Scans for pending Windows and winget(third party) updates, and report them.
    """
    print("[*] Scanning for pending updates...", flush=True)
    code, output = script_runner.run("get_updates.ps1", timeout=600)
    if code != 0:
        print(f"[!] Update scan failed: {output[:300]}", flush=True)
        return

    found = _parse_script_json(output, "get_updates.ps1")
    if found is None:
        return

    updates = [
        {**item, "source": source}
        for source in ("windows", "winget")
        for item in found.get(source) or []
    ]

    try:
        response = api.send_pending_updates(updates)
    except requests.RequestException as exc:
        print(f"[!] Could not send updates: {type(exc).__name__}: {exc}", flush=True)
        return

    if response.status_code == 200:
        windows_count = sum(1 for u in updates if u["source"] == "windows")
        print(
            f"[OK] Reported {windows_count} Windows and "
            f"{len(updates) - windows_count} third-party updates.",
            flush=True,
        )
    elif response.status_code == 401:
        reregister(api)
    else:
        print(f"[!] Server rejected updates: {response.text[:200]}", flush=True)


def update_scan_loop(api: server_api.ServerAPI) -> None:
    while True:
        send_pending_updates(api)
        time.sleep(UPDATE_SCAN_INTERVAL_SECONDS)


# entry points
def build_api(config: agent_config.AgentConfig) -> server_api.ServerAPI:
    """Builds and returns a server API session for every call."""
    return server_api.ServerAPI(
        session=http_client.build_session(),
        server_url=config.server_url,
        device_id=config.device_id,
        token=config.token,
    )


def run_agent() -> None:
    require_elevation()
    config = load_config()
    try:
        api = build_api(config)
    except FileNotFoundError as exc:
        sys.exit(f"[X] {exc}")

    print(f"[*] Agent started with Device ID: {config.device_id}", flush=True)
    print(http_client.describe_tls(config.server_url), flush=True)

    if agent_config.task_signing_secret():
        print("[*] Task signature verification: ENABLED", flush=True)
    else:
        print(
            "[!] Task signature verification: DISABLED - no OPENPATCH_TASK_SIGNING_SECRET "
            "set and none in config.ini. Tasks from any server that can answer this "
            "agent will be executed.",
            flush=True,
        )

    send_software_inventory(api)
    threading.Thread(target=update_scan_loop, args=(api,), daemon=True).start()

    start_polling(api)


USAGE = f"""
OpenPatch agent.
    {agent_paths.program_name()}                        run the agent (requires elevation)
    {agent_paths.program_name()} enroll --help          enrol this device with a server
    {agent_paths.program_name()} install-task           run at boot as LOCAL SYSTEM
    {agent_paths.program_name()} uninstall-task         remove that scheduled task
"""


def install_task(argv: list) -> int:
    """Registers (or removes) the scheduled task that runs this agent as SYSTEM.
    """
    import argparse
    parser = argparse.ArgumentParser(
        prog=f"{agent_paths.program_name()} install-task",
        description="Install this agent as a scheduled task running as LOCAL SYSTEM at boot.",
    )
    parser.add_argument("--task-name", default="OpenPatch Agent")
    parser.add_argument(
        "--uninstall", action="store_true", help="remove the task instead of installing it"
    )
    args = parser.parse_args(argv)

    if not agent_paths.is_frozen():
        print(
            "[!] Running from source, where the executable does not exist yet.\n"
            "    Use the script directly: "
            "powershell -ExecutionPolicy Bypass -File scripts/install_agent_task.ps1",
            flush=True,
        )

    if not args.uninstall and not telemetry.is_elevated():
        print(
            "[X] Installing the scheduled task requires an elevated console.",
            "    Re-run this from an elevated PowerShell or Command Prompt console. ",
            sep=chr(10),
            flush=True,
        )
        return 1

    script_args = ["-TaskName", args.task_name]
    if args.uninstall:
        script_args.append("-Uninstall")
    elif agent_paths.is_frozen():
        script_args += ["-AgentExe", sys.executable]

    code, output = script_runner.run("install_agent_task.ps1", args=script_args)
    print(output, flush=True)
    # None means the script could not be run at all, which is a failure the
    # caller must not read as success - see script_runner.
    return 0 if code == 0 else 1


def use_utf8_output() -> None:
    """Makes stdout survive the characters this agent prints.
    """
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, OSError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list) -> int:
    use_utf8_output()

    if argv and argv[0] == "enroll":
        return enrollment.main(argv[1:])

    if argv and argv[0] in ("install-task", "uninstall-task"):
        extra = ["--uninstall"] if argv[0] == "uninstall-task" else []
        return install_task(argv[1:] + extra)

    if argv and argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0

    if argv:
        print(f"Unknown argument {argv[0]!r}.")
        print(USAGE)
        return 2

    run_agent()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
