"""Machine telemetry, and the privilege check that gates the whole agent."""

import ctypes
import importlib
import os_info


_wmi = None


def _psutil():
    return importlib.import_module("psutil")


def _win32api():
    return importlib.import_module("win32api")


def _win32com_client():
    return importlib.import_module("win32com.client")

def wmi():
    """The WMI namespace, opened on first use."""
    global _wmi
    if _wmi is None:
        _wmi = _win32com_client().GetObject("winmgmts:")
    return _wmi


def reset_wmi() -> None:
    """Drops the cached connection. Used for tests, and for the rare case of
    wanting to re-open it after a WMI failure."""
    global _wmi
    _wmi = None


def hostname() -> str:
    return _win32api().GetComputerName()


def is_elevated() -> bool:
    """Checks whether this process has administrator rights."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def collect(device_id: str) -> dict:
    psutil = _psutil()
    wmi_os = wmi().InstancesOf("Win32_OperatingSystem")[0]
    reboot_required, reboot_reasons = os_info.get_reboot_status()

    return {
        "device_id": device_id,
        "hostname": hostname(),
        "os_version": wmi_os.Version,
        "os_name": os_info.get_name(),
        "reboot_required": reboot_required,
        "reboot_reasons": reboot_reasons,
        "cpu_usage": psutil.cpu_percent(interval=1),
        "ram_usage": psutil.virtual_memory().percent,
    }