"""Headless device enrolment. The result is the same config.ini the agent has always read.
"""
import os
import sys
import os_info
import argparse
import requests
import platform
import http_client
import agent_paths
import agent_config


def register(
    server_url: str,
    department: str = "",
    enrollment_secret: str = "",
    hostname: str | None = None,
) -> agent_config.AgentConfig:
    """Enrols this device and saves the issued token.
    And raises requests exceptions on failure.
    """
    payload = {
        "device_id": agent_config.DEVICE_ID,
        "hostname": hostname or platform.node(),
        "os_version": os_info.get_version(),
        "os_name": os_info.get_name(),
        "department": department or None,
        "enrollment_secret": enrollment_secret or None,
    }
    response = http_client.build_session().post(
        f"{server_url.rstrip('/')}/api/v1/agent/register", json=payload, timeout=15
    )
    response.raise_for_status()
    token = response.json()["token"]
    return agent_config.save(server_url, token, department=department)


def _describe_failure(exc: requests.RequestException) -> str:
    """Turns a request failure into something an operator can act on."""
    response = getattr(exc, "response", None)
    if response is None:
        return (
            f"Could not reach the server: {type(exc).__name__}: {exc}\n"
            "    Check the URL, that the server is running, and - for HTTPS with an "
            "internal CA - that OPENPATCH_CA_BUNDLE points at ca.crt."
        )
    if response.status_code == 401:
        return (
            "The server rejected the enrolment secret (401).\n"
        )
    return f"The server refused the enrolment: HTTP {response.status_code} {response.text[:200]}"


SECRET_ENV = "OPENPATCH_ENROLLMENT_SECRET"


def resolve_secret(args) -> str:
    """Returns the enrolment secret.
    """
    if args.enrollment_secret:
        return args.enrollment_secret

    if args.enrollment_secret_file:
        try:
            with open(args.enrollment_secret_file, encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError as exc:
            sys.exit(f"[X] Could not read {args.enrollment_secret_file}: {exc}")

    return os.environ.get(SECRET_ENV, "").strip()


def _existing_enrolment(server_url: str):
    """Consults on what to do about a device that is already enrolled.
    """
    existing = agent_config.load()
    if existing is None:
        return None

    wanted = server_url.rstrip("/")
    if existing.server_url == wanted:
        return 0, (
            f"[OK] Already enrolled against {existing.server_url} as device "
            f"{existing.device_id}; nothing to do.\n"
            f"     Configuration: {agent_config.CONFIG_PATH}"
        )

    return 2, (
        f"[X] This device is already enrolled against {existing.server_url}, "
        f"not {wanted}.\n"
        "    Re-enrol with --force if that is intended; the previously issued "
        "token stops working."
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog=f"{agent_paths.program_name()} enroll",
        description="Enrol this device with an OpenPatch server.",
    )
    parser.add_argument(
        "--server", required=True,
        help="Base URL of the OpenPatch server, e.g. https://patch.corp.local:8000",
    )
    parser.add_argument(
        "--department", default="",
        help="Grouping shown in the dashboard fleet table, e.g. Finance",
    )
    parser.add_argument(
        "--enrollment-secret", default="",
        help=(
            "Required when the server sets OPENPATCH_ENROLLMENT_SECRET. Visible "
            "in process listings and deployment logs; prefer the file or "
            "environment forms for an unattended rollout."
        ),
    )
    parser.add_argument(
        "--enrollment-secret-file", default="",
        help="Read the enrolment secret from a file instead of the command line",
    )
    parser.add_argument(
        "--hostname", default=None,
        help="Override the reported hostname (defaults to this machine's name)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-enrol even if this device already has a token. The old token stops working.",
    )
    args = parser.parse_args(argv)

    if not args.force:
        outcome = _existing_enrolment(args.server)
        if outcome is not None:
            code, message = outcome
            print(message, flush=True)
            return code

    try:
        config = register(
            server_url=args.server,
            department=args.department,
            enrollment_secret=resolve_secret(args),
            hostname=args.hostname,
        )
    except FileNotFoundError as exc:
        print(f"[X] Enrolment failed.\n    {exc}", flush=True)
        return 1
    except requests.RequestException as exc:
        print(f"[X] Enrolment failed.\n    {_describe_failure(exc)}", flush=True)
        return 1

    print(f"[OK] Enrolled as device {config.device_id} against {config.server_url}", flush=True)
    print(f"     Token saved to {agent_config.CONFIG_PATH}", flush=True)
    print("     It is shown once and never again.", flush=True)
    print("", flush=True)
    print("     Next, install the agent as a SYSTEM scheduled task so it can patch", flush=True)
    print("     without prompting the logged-in user:", flush=True)
    print(f"         {agent_paths.program_name()} install-task", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
