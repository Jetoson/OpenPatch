"""The two secrets shared between the server and its agents.

    enrolment secret     what a device must present to join the fleet
    task signing secret  what proves a task list came from this server
"""

import os

import generated_secrets
import paths
from config import ENROLLMENT_OPEN, ENROLLMENT_SECRET, TASK_SIGNING_SECRET


def enrollment_secret_path() -> str:
    return os.environ.get("OPENPATCH_ENROLLMENT_SECRET_FILE") or os.path.join(
        paths.data_dir(), "enrollment_secret"
    )


def task_signing_secret_path() -> str:
    return os.environ.get("OPENPATCH_TASK_SIGNING_SECRET_FILE") or os.path.join(
        paths.data_dir(), "task_signing_secret"
    )


def enrollment_secret() -> str:
    """Returns the secret required by POST /agent/register.
    """
    if ENROLLMENT_OPEN:
        return ""
    return generated_secrets.resolve(ENROLLMENT_SECRET, enrollment_secret_path())


def task_signing_secret() -> str:
    return generated_secrets.resolve(TASK_SIGNING_SECRET, task_signing_secret_path())


def describe_enrollment_secret() -> str:
    if ENROLLMENT_OPEN:
        return (
            "[!] Enrolment is OPEN (OPENPATCH_ENROLLMENT_OPEN=1) - anything "
            "that can reach\n"
            "    this server can enrol a device and be issued a token for it."
        )
    return generated_secrets.describe(
        "Enrolment secret",
        ENROLLMENT_SECRET,
        enrollment_secret(),
        enrollment_secret_path(),
        "OPENPATCH_ENROLLMENT_SECRET",
    )


def describe_task_signing_secret() -> str:
    line = generated_secrets.describe(
        "Task signing secret",
        TASK_SIGNING_SECRET,
        task_signing_secret(),
        task_signing_secret_path(),
        "OPENPATCH_TASK_SIGNING_SECRET",
    )
    return (
        f"{line}\n"
        "    Every agent needs this same value to verify the tasks it is "
        "given;\n"
        "    one that does not have it executes them unverified and says so."
    )
