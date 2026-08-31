"""Operating system related details of the machine reported to the server.
        - version - is a machine-readable format
        - name -is a human label, e.g. "Windows 11 Pro".
Build numbers are exclusively used to compose name.
"""

import winreg
import platform


# platform.win32_edition() returns the registry EditionID, which is a product
# code rather than the marketing name ("Core" is Home).
_EDITION_NAMES = {
    "core": "Home",
    "coren": "Home N",
    "coresinglelanguage": "Home Single Language",
    "corecountryspecific": "Home China",
    "professional": "Pro",
    "professionaln": "Pro N",
    "professionaleducation": "Pro Education",
    "professionalworkstation": "Pro for Workstations",
    "enterprise": "Enterprise",
    "enterprisen": "Enterprise N",
    "enterprises": "Enterprise LTSC",
    "education": "Education",
    "educationn": "Education N",
    "iotenterprise": "IoT Enterprise",
    "iotenterprises": "IoT Enterprise LTSC",
    "cloud": "SE",
    "cloudedition": "SE",
    "serverstandard": "Server Standard",
    "serverdatacenter": "Server Datacenter",
    "serverenterprise": "Server Enterprise",
}

FIRST_WINDOWS_11_BUILD = 22000
FIRST_WINDOWS_10_BUILD = 10240


def get_version() -> str:
    return platform.version()


def _build_number(version: str) -> int | None:
    parts = (version or "").strip().split(".")
    if len(parts) >= 3 and parts[0] == "10" and parts[2].isdigit():
        return int(parts[2])
    return None


def _edition() -> str:
    try:
        raw = platform.win32_edition() or ""
    except AttributeError:
        return ""  # A Python without win32_edition
    return _EDITION_NAMES.get(raw.strip().lower(), raw.strip())


def get_name() -> str:
    """Returns a human-friendly name for the operating system."""
    edition = _edition()
    build = _build_number(platform.version())

    if edition.startswith("Server"):
        # Server builds don't follow the 22000 split
        return f"Windows {edition}".strip()

    if build is None:
        family = f"Windows {platform.release()}".strip()
    elif build >= FIRST_WINDOWS_11_BUILD:
        family = "Windows 11"
    elif build >= FIRST_WINDOWS_10_BUILD:
        family = "Windows 10"
    else:
        family = f"Windows {platform.release()}".strip()

    return f"{family} {edition}".strip()


_REBOOT_KEYS = [
    (
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending",
        "Component servicing (a completed feature/update install)",
    ),
    (
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired",
        "Windows Update",
    ),
    (
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Services\Pending",
        "Windows Update (install pending)",
    ),
]


def _key_exists(path: str) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path):
            return True
    except OSError:
        return False


def _pending_file_renames() -> bool:
    """Set when an installer could not replace a file in use and queued the
    swap for next boot."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager"
        ) as key:
            value, _ = winreg.QueryValueEx(key, "PendingFileRenameOperations")
            return bool(value)
    except OSError:
        return False


def _rename_pending() -> bool:
    """A rename applied with /norestart leaves the active and configured
    computer names disagreeing until reboot."""
    base = r"SYSTEM\CurrentControlSet\Control\ComputerName"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base + r"\ActiveComputerName") as key:
            active, _ = winreg.QueryValueEx(key, "ComputerName")
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base + r"\ComputerName") as key:
            configured, _ = winreg.QueryValueEx(key, "ComputerName")
        return active != configured
    except OSError:
        return False


def get_reboot_status() -> tuple[bool, str]:
    """Check whether a reboot was required"""
    if winreg is None:
        return False, ""

    reasons = [label for path, label in _REBOOT_KEYS if _key_exists(path)]
    if _pending_file_renames():
        reasons.append("Files staged for replacement on next boot")
    if _rename_pending():
        reasons.append("Computer rename")
    return bool(reasons), "; ".join(reasons)
