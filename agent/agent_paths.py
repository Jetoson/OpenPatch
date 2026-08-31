"""Where the agent finds what it needs to run.

This module draws a distinction between two directories that
are the same when running from source and different when frozen:
    bundle_dir()    read-only files shipped inside the program: the
                    PowerShell scripts. Temporary under a one-file build.
    install_dir()   the durable location the program was launched from,
                    where config.ini lives.
"""

import os
import sys


def is_frozen() -> bool:
    """Returns whether this is a PyInstaller build or a source checkout."""
    return getattr(sys, "frozen", False)


def bundle_dir() -> str:
    """Returns the directory holding read-only resources shipped with the program.
    """
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def install_dir() -> str:
    """Returns the durable directory for state the agent must keep across restarts.
    This directory can be found beside the executable when frozen, and beside the source when not.
    """
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def scripts_dir() -> str:
    """Returns the directory where the bundled PowerShell scripts reside."""
    return os.path.join(bundle_dir(), "scripts")


def config_path() -> str:
    """Returns the agent's config.ini file directory."""
    return os.path.join(install_dir(), "config.ini")


def bundled_ca_path() -> str:
    """Returns a ca.crt built into this agent, or "" if the build carried none..
    """
    path = os.path.join(bundle_dir(), "ca.crt")
    return path if os.path.exists(path) else ""


def env_file_path() -> str:
    """Returns the optional .env file directory.
    """
    if is_frozen():
        return os.path.join(install_dir(), ".env")
    return os.path.join(os.path.dirname(install_dir()), ".env")


def program_name() -> str:
    """Returns the  name of this program which can be used in its own help text.
    """
    if is_frozen():
        return os.path.basename(sys.executable)
    return "python agent_service.py"
