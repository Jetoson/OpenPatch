"""Where the server finds what it needs to run.

    bundle_dir() -  read-only files shipped with the program.
    install_dir() - the permanent directory the program was launched from.
    data_dir()  -   writable state: the SQLite database and the generated
                   admin key.
"""

import os
import sys


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def bundle_dir() -> str:
    """Returns the directory holding read-only resources shipped with the program."""
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def install_dir() -> str:
    """Returns the permanent directory the program was launched from."""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def data_dir() -> str:
    """Writable state: database, generated admin key.
    """
    override = os.environ.get("OPENPATCH_DATA_DIR", "").strip()
    return override or os.path.join(install_dir(), "data")


def ensure_data_dir() -> str:
    """Creates data_dir(), if not already present."""
    directory = data_dir()
    os.makedirs(directory, exist_ok=True)
    return directory


def default_database_url() -> str:
    """Returns SQLite URL inside the data directory.
    """
    return "sqlite:///" + os.path.join(data_dir(), "patchguard.db").replace("\\", "/")


def alembic_dir() -> str:
    """The migration scripts, which ship with the program."""
    return os.path.join(bundle_dir(), "alembic")


def alembic_ini() -> str:
    return os.path.join(bundle_dir(), "alembic.ini")


def dashboard_assets_dir() -> str:
    return os.path.join(bundle_dir(), "dashboard", "assets")


def env_file_path() -> str:
    """ The optional .env file which can be found beside the executable when frozen,
     at the repository root from a source checkout."""
    if is_frozen():
        return os.path.join(install_dir(), ".env")
    return os.path.join(os.path.dirname(install_dir()), ".env")


def program_name() -> str:
    """Returns the name of this program in its own help text.
    """
    if is_frozen():
        return os.path.basename(sys.executable)
    return "python run.py"


def source_root() -> str:
    """Returns the repository root when running from a checkout"".
    """
    if is_frozen():
        return ""
    root = os.path.dirname(install_dir())
    return root if os.path.isdir(os.path.join(root, "packaging")) else ""


def has_source_tree() -> bool:
    """Verifies whether the repository's helper scripts are reachable.
    """
    return not is_frozen() and os.path.isdir(
        os.path.join(os.path.dirname(install_dir()), "scripts")
    )
