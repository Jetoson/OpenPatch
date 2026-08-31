"""Local agent configuration: server URL, device identity, and enrollment
token, persisted to config.ini.

DEVICE_ID is derived from the machine's MAC address
"""

import os
import uuid
import tempfile
import contextlib
import agent_paths
import configparser
from dotenv import load_dotenv


def load_env_file() -> None:
    """Loads a .env file into the environment, if one is there to load.
    Real environment variables have a higher priority (override=False)
    """
    path = agent_paths.env_file_path()
    if not os.path.exists(path):
        return
    load_dotenv(path, override=False)

load_env_file()

# Beside the executable when frozen
CONFIG_PATH = agent_paths.config_path()
DEVICE_ID = str(uuid.getnode())

READ_ENCODING = "utf-8-sig"
WRITE_ENCODING = "utf-8"

# Settings an administrator places on the endpoint
MANUAL_OPTIONS = (
    "task_signing_secret", "ca_bundle", "enrollment_secret", "checkpoint_throttle_minutes",
)

# Whether an endpoint must hold the enrolment secret to re-enrol itself after
# the server stops recognising its token.
ENROLLMENT_SECRET_REQUIRED = True


def get_option(name: str) -> str:
    """ Returns a manually-provisioned setting: environment first, then config.ini."""
    from_env = os.environ.get(f"OPENPATCH_{name.upper()}", "").strip()
    if from_env:
        return from_env
    if not os.path.exists(CONFIG_PATH):
        return ""
    parser = configparser.ConfigParser()
    parser.read(CONFIG_PATH, encoding=READ_ENCODING)
    return parser.get("agent", name, fallback="").strip()


def task_signing_secret() -> str:
    """Returns a shared secret used to verify task lists from the server."""
    return get_option("task_signing_secret")


class AgentConfig:
    def __init__(self, server_url: str, token: str, department: str = ""):
        self.server_url = server_url.rstrip("/")
        self.device_id = DEVICE_ID
        self.token = token
        self.department = department


def load() -> AgentConfig | None:
    """Returns the saved config, or None if the agent hasn't registered yet (first run)."""
    if not os.path.exists(CONFIG_PATH):
        return None

    parser = configparser.ConfigParser()
    parser.read(CONFIG_PATH, encoding=READ_ENCODING)

    if not parser.has_section("agent"):
        return None

    server_url = parser.get("agent", "server_url", fallback="").strip()
    token = parser.get("agent", "token", fallback="").strip()
    if not server_url or not token:
        return None

    department = parser.get("agent", "department", fallback="").strip()

    # A config.ini carrying another machine's device id is a cloned golden
    # image
    recorded = parser.get("agent", "device_id", fallback="").strip()
    if recorded and recorded != DEVICE_ID:
        print(
            f"[X] {CONFIG_PATH} was issued to a different device "
            f"({recorded}; this machine is {DEVICE_ID}), so its token will be "
            "rejected.",
            "    Either a golden image was enrolled before being cloned, or "
            "this machine's network adapter changed - the device id is derived "
            "from the MAC address.",
            "    Delete the file and enrol this machine on its own.",
            sep=chr(10),
            flush=True,
        )
        return None

    return AgentConfig(server_url=server_url, token=token, department=department)


def save(server_url: str, token: str, department: str = "") -> AgentConfig:
    preserved = dict.fromkeys(MANUAL_OPTIONS, "")
    if os.path.exists(CONFIG_PATH):
        previous = configparser.ConfigParser()
        previous.read(CONFIG_PATH, encoding=READ_ENCODING)
        for name in MANUAL_OPTIONS:
            preserved[name] = previous.get("agent", name, fallback="")

    parser = configparser.ConfigParser()
    parser["agent"] = {
        "server_url": server_url.rstrip("/"),
        "device_id": DEVICE_ID,
        "token": token,
        "department": department or "",
        **preserved,
    }
    _write_atomically(parser)
    return AgentConfig(server_url=server_url, token=token, department=department)


def _write_atomically(parser: configparser.ConfigParser) -> None:
    """Replaces config.ini in one step, so an interrupted write cannot destroy
    an enrolment.
    """
    directory = os.path.dirname(CONFIG_PATH) or "."
    descriptor, temporary = tempfile.mkstemp(
        dir=directory, prefix=".config-", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding=WRITE_ENCODING) as handle:
            parser.write(handle)
            handle.flush()
            # The rename only publishes what has actually reached the disk
            os.fsync(handle.fileno())
        os.replace(temporary, CONFIG_PATH)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(temporary)
        raise


def checkpoint_throttle_minutes() -> str:
    """Returns how recently a System Restore point may have been created and still
    let a patch run skip taking its own."""
    return get_option("checkpoint_throttle_minutes")
