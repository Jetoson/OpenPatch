# PyInstaller specification for the agent.
#
#   pyinstaller packaging/agent.spec --noconfirm
#
# The bundled PowerShell is the reason a spec file exists. The agent's
#  remediation logic lays in those scriipts.
#

import os

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
AGENT = os.path.join(ROOT, "agent")

datas = [
    (os.path.join(AGENT, "scripts"), "scripts"),
]

CA_SOURCE = os.environ.get("OPENPATCH_BUILD_CA", "").strip() or os.path.join(
    ROOT, "server", "certs", "ca.crt"
)
if os.path.exists(CA_SOURCE):
    # Destination "." is the root of the extraction directory.
    datas.append((CA_SOURCE, "."))
    print(f"[*] Bundling CA certificate: {CA_SOURCE}", flush=True)
else:
    print(
        f"[!] No CA certificate bundled ({CA_SOURCE} does not exist).\n"
        "    The agent will verify against the endpoint's own certificate "
        "store, so an\n"
        "    internal CA must reach each endpoint some other way - Group "
        "Policy, or\n"
        "    OPENPATCH_CA_BUNDLE pointing at a copy of ca.crt.",
        flush=True,
    )

hiddenimports = [
    # Reached through COM at run time and imported lazily inside functions..
    "win32com.client",
    "win32api",
    "pythoncom",
    "pywintypes",
] + collect_submodules("encodings")

a = Analysis(
    [os.path.join(AGENT, "agent_service.py")],
    pathex=[AGENT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],

    excludes=[
        "tkinter", "PIL", "matplotlib", "numpy", "pandas",
        "streamlit", "fastapi", "uvicorn", "sqlalchemy", "alembic",
        "pytest", "IPython",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="openpatch-agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # The agent is a console application and the standard output is its only
    # user interface.
    
    console=True,
    disable_windowed_traceback=False,
    icon=os.path.join(AGENT, "assets", "logo.ico"),
)
