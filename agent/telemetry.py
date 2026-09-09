"""Machine telemetry, and the privilege check that gates the whole agent."""

import ctypes

import os_info

_wmi = None


def wmi():
    """The WMI namespace, opened on first use."""
    global _wmi
    if _wmi is None:
        import win32com.client
        _wmi = win32com.client.GetObject("winmgmts:")
    return _wmi


def reset_wmi() -> None:
    global _wmi
    _wmi = None


def hostname() -> str:
    import win32api
    return win32api.GetComputerName()


def is_elevated() -> bool:
    """Checks whether this process has administrator rights."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def collect(device_id: str) -> dict:
    """One heartbeat payload."""
    import psutil

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