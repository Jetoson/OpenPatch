"""The one Windows-specific module, and the reason it is the only one."""

import sys

import telemetry


def test_importing_the_agent_does_not_open_a_com_connection():
    telemetry.reset_wmi()
    import importlib

    for name in ("script_runner", "tasks", "server_api", "agent_service"):
        importlib.import_module(name)

    assert telemetry._wmi is None, "nothing should have opened WMI on import"


def test_no_agent_module_imports_pywin32_at_module_scope():
    import ast
    import os

    agent_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "agent"
    )
    offenders = {}
    for filename in os.listdir(agent_dir):
        if not filename.endswith(".py"):
            continue
        tree = ast.parse(open(os.path.join(agent_dir, filename), encoding="utf-8").read())
        for node in tree.body:
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            hits = [n for n in names if n.split(".")[0] in ("win32api", "win32com", "psutil")]
            if hits:
                offenders.setdefault(filename, []).extend(hits)

    assert offenders == {}, f"Windows imports at module scope: {offenders}"


class TestElevationCheck:
    def test_a_failing_check_is_treated_as_not_elevated(self, monkeypatch):
        import ctypes

        class Boom:
            def __getattr__(self, name):
                raise OSError("no shell32 here")

        monkeypatch.setattr(ctypes, "windll", Boom(), raising=False)
        assert telemetry.is_elevated() is False

    def test_it_returns_a_real_bool(self):
        assert isinstance(telemetry.is_elevated(), bool)


class TestWmiCaching:
    def test_the_connection_is_opened_once_and_reused(self, monkeypatch):
        """The heartbeat needs it every poll, and opening it is not free."""
        telemetry.reset_wmi()
        opened = []
        fake_module = type("M", (), {"GetObject": staticmethod(
            lambda moniker: opened.append(moniker) or "connection"
        )})
        monkeypatch.setitem(sys.modules, "win32com", type("P", (), {"client": fake_module}))
        monkeypatch.setitem(sys.modules, "win32com.client", fake_module)

        assert telemetry.wmi() == "connection"
        assert telemetry.wmi() == "connection"
        assert opened == ["winmgmts:"], "opened exactly once"

        telemetry.reset_wmi()


class TestCollect:
    def test_the_payload_has_everything_the_server_stores(self, monkeypatch):
        telemetry.reset_wmi()

        class FakeOS:
            Version = "10.0.26100"

        monkeypatch.setattr(telemetry, "wmi", lambda: type("W", (), {
            "InstancesOf": staticmethod(lambda _cls: [FakeOS()])
        }))
        monkeypatch.setattr(telemetry, "hostname", lambda: "WS-01")
        monkeypatch.setattr(telemetry.os_info, "get_reboot_status", lambda: (True, "Windows Update"))
        monkeypatch.setattr(telemetry.os_info, "get_name", lambda: "Windows 11 Pro")
        fake_psutil = type("P", (), {
            "cpu_percent": staticmethod(lambda interval=None: 12.5),
            "virtual_memory": staticmethod(lambda: type("M", (), {"percent": 40.0})),
        })
        monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

        payload = telemetry.collect("dev-1")

        assert payload == {
            "device_id": "dev-1",
            "hostname": "WS-01",
            "os_version": "10.0.26100",
            "os_name": "Windows 11 Pro",
            "reboot_required": True,
            "reboot_reasons": "Windows Update",
            "cpu_usage": 12.5,
            "ram_usage": 40.0,
        }
        telemetry.reset_wmi()

    def test_the_os_label_comes_from_os_info_not_wmi(self, monkeypatch):
        """So the agent and the enrolment command - which has no pywin32 -
        always report the OS the same way."""
        telemetry.reset_wmi()

        class FakeOS:
            Version = "10.0.26100"
            Caption = "Microsoft Windows 11 Professional"

        monkeypatch.setattr(telemetry, "wmi", lambda: type("W", (), {
            "InstancesOf": staticmethod(lambda _cls: [FakeOS()])
        }))
        monkeypatch.setattr(telemetry, "hostname", lambda: "WS-01")
        monkeypatch.setattr(telemetry.os_info, "get_reboot_status", lambda: (False, None))
        monkeypatch.setattr(telemetry.os_info, "get_name", lambda: "Windows 11 Pro")
        monkeypatch.setitem(sys.modules, "psutil", type("P", (), {
            "cpu_percent": staticmethod(lambda interval=None: 1.0),
            "virtual_memory": staticmethod(lambda: type("M", (), {"percent": 1.0})),
        }))

        assert telemetry.collect("dev-1")["os_name"] == "Windows 11 Pro"
        telemetry.reset_wmi()


def test_the_agent_imports_no_gui_toolkit():
    import ast
    import os

    agent_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "agent"
    )
    gui = {"tkinter", "PIL", "win32gui"}
    offenders = {}
    for filename in os.listdir(agent_dir):
        if not filename.endswith(".py"):
            continue
        tree = ast.parse(open(os.path.join(agent_dir, filename), encoding="utf-8").read())
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module.split(".")[0])
        if found & gui:
            offenders[filename] = sorted(found & gui)

    assert offenders == {}
