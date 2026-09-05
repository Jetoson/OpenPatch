"""Loads the repository's .env file into the environment.
"""

import os
import paths


ENV_PATH = paths.env_file_path()


def load() -> bool:
    """Loads ROOT/.env if present and returns whether anything was loaded."""
    if not os.path.exists(ENV_PATH):
        return False

    try:
        from dotenv import load_dotenv
    except ImportError:
        print(
            f"[!] {ENV_PATH} exists but python-dotenv is not installed, so it is "
            "being ignored. Install it (pip install -r requirements.txt) or set "
            "these as real environment variables.",
            flush=True,
        )
        return False

    # Anything already in the environment gets a priority.
    load_dotenv(ENV_PATH, override=False)
    return True
