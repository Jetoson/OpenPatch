# PyInstaller specification for the OpenPatch API server.
#
#   pyinstaller packaging/server.spec --noconfirm
#
# Docker is the primary way to run the server (see the Dockerfile), and this
# exists for the deployments that cannot use docker for any reason.
#

import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
SERVER = os.path.join(ROOT, "server")

datas = [
    # schema. Destination matches paths.alembic_dir().
    (os.path.join(SERVER, "alembic"), "alembic"),
    (os.path.join(SERVER, "alembic.ini"), "."),
]

# Alembic loads env.py and each migration by file path at run time.
hiddenimports = [
    "main",
    "api.models",
    "api.routers.agent",
    "api.routers.dashboard",
    "api.routers.tasks",
    "alembic.runtime.migration",
    "alembic.autogenerate",
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.dialects.postgresql",
] + collect_submodules("uvicorn")

a = Analysis(
    [os.path.join(SERVER, "run.py")],
    pathex=[SERVER],
    binaries=[],
    datas=datas + collect_data_files("alembic"),
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # The dashboard's stack, for the reason given above.
        "streamlit", "pandas", "numpy", "matplotlib", "PIL",
        # Agent-only.
        "win32com", "win32api", "psutil",
        "tkinter", "pytest", "IPython",
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
    name="openpatch-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)
