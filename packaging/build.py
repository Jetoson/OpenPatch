"""Build the OpenPatch executables.

    python packaging/build.py            both
    python packaging/build.py agent      only the agent
    python packaging/build.py server     only the server

"""

import os
import sys
import shutil
import argparse
import subprocess


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGING = os.path.join(ROOT, "packaging")
DIST = os.path.join(ROOT, "dist")
WORK = os.path.join(ROOT, "build")

TARGETS = {
    "agent": ("agent.spec", "openpatch-agent.exe"),
    "server": ("server.spec", "openpatch-server.exe"),
}


def _require(module: str, package: str, target: str) -> None:
    try:
        __import__(module)
    except ImportError:
        sys.exit(
            f"[X] Cannot build the {target}: {module} is not installed.\n"
            f"    pip install {package}\n"
        )


def build(target: str, clean: bool) -> str:
    spec, artefact = TARGETS[target]

    if target == "agent" and sys.platform == "win32":
        # The agent reads WMI and the computer name through pywin32
        _require("win32api", "pywin32", "agent")

    if clean:
        shutil.rmtree(os.path.join(WORK, target), ignore_errors=True)

    print(f"[*] Building {artefact} from {spec}", flush=True)
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", os.path.join(PACKAGING, spec),
         "--noconfirm", "--distpath", DIST, "--workpath", WORK],
        check=True,
    )

    produced = os.path.join(DIST, artefact)
    if not os.path.exists(produced):
        sys.exit(f"[X] Build reported success but {produced} does not exist.")

    size = os.path.getsize(produced) / (1024 * 1024)
    print(f"[OK] {produced}  ({size:.1f} MB)", flush=True)
    return produced


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(prog="build.py", description=__doc__)
    parser.add_argument("target", nargs="?", default="all", choices=["all", "agent", "server"])
    parser.add_argument("--clean", action="store_true", help="discard cached build state first")
    args = parser.parse_args(argv)

    targets = list(TARGETS) if args.target == "all" else [args.target]
    for target in targets:
        build(target, clean=args.clean)

    print("", flush=True)
    print("Artefacts in " + DIST, flush=True)
    print("  openpatch-agent.exe   copy to an endpoint, then: "
          "openpatch-agent.exe enroll --server https://...", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
