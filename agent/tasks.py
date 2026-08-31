"""Task definitions and status reporting.
"""
import agent_config
import script_runner

# Actions that are a single script run.
TASK_SCRIPTS = {
    "UPDATE_WINGET": "update_winget.ps1",
    "UPDATE_OS": "update_os.ps1",
    "RESTART": "restart_system.ps1",
    "ROLLBACK": "rollback.ps1",
}

# Actions which need a restore point taken first.
PATCHING_ACTIONS = ("UPDATE_WINGET", "UPDATE_OS")

# Which script parameter carries a task's target, per action.
TARGET_FLAGS = {
    "UPDATE_WINGET": "-PackageId",
    "UPDATE_OS": "-KB",
    "ROLLBACK": "-MaxAgeHours",
}


def checkpoint_args() -> list:
    """ Returns checkpoint arguments."""
    configured = agent_config.checkpoint_throttle_minutes()
    if not configured:
        return []
    try:
        return ["-CheckpointThrottleMinutes", str(int(configured))]
    except ValueError:
        print(
            "[!] Using the default (always take a restore point).",
            flush=True,
        )
        return []


def script_args(action: str, target: str | None) -> list | None:
    """Returns full argument list for a single-script action."""
    args = checkpoint_args() if action in PATCHING_ACTIONS else []
    flag = TARGET_FLAGS.get(action)
    if target and flag:
        args = args + [flag, target]
    return args or None


def update_and_verify():
    """Applies winget updates, then confirm the machine still works."""
    update_code, update_output = script_runner.run("update_winget.ps1", args=checkpoint_args())
    log = [f"[update_winget.ps1 exit={update_code}]", update_output]

    if update_code != 0:
        log.append("Verification skipped: the update did not succeed.")
        return "FAILED", "\n".join(log)

    verify_code, verify_output = script_runner.run("verify_workflow.ps1")
    log += [f"\n[verify_workflow.ps1 exit={verify_code}]", verify_output]

    if verify_code == 0:
        return "SUCCESS_VERIFIED", "\n".join(log)
    return "SUCCESS_WORKFLOW_FAILED", "\n".join(log)


def update_verify_heal():
    """Updates, verifies, and rolls the machine back if verification fails."""
    update_code, update_output = script_runner.run("update_winget.ps1", args=checkpoint_args())
    log = [f"[update_winget.ps1 exit={update_code}]", update_output]

    if update_code != 0:
        log.append("Verification and rollback skipped: the update did not succeed.")
        return "FAILED", "\n".join(log)

    verify_code, verify_output = script_runner.run("verify_workflow.ps1")
    log += [f"\n[verify_workflow.ps1 exit={verify_code}]", verify_output]

    if verify_code == 0:
        return "SUCCESS_VERIFIED", "\n".join(log)

    # Verification failed, so the patch broke something - heal automatically.
    print("[!] Verification failed - rolling back automatically.", flush=True)
    rollback_code, rollback_output = script_runner.run(
        "rollback.ps1", timeout=script_runner.timeout_for("ROLLBACK")
    )
    log += [f"\n[rollback.ps1 exit={rollback_code}]", rollback_output]

    if rollback_code == 0:
        return "FAILED_AUTO_ROLLED_BACK", "\n".join(log)
    return "FAILED_ROLLBACK_FAILED", "\n".join(log)


def execute(action: str, target: str | None = None):
    """Returns (status, output) tuple after executing the action."""
    print(
        f"\n[!] Executing task: {action}" + (f" (target: {target})" if target else ""),
        flush=True,
    )

    if action == "UPDATE_AND_VERIFY":
        return update_and_verify()

    if action == "UPDATE_VERIFY_HEAL":
        return update_verify_heal()

    script_name = TASK_SCRIPTS.get(action)
    if not script_name:
        return "FAILED", "Unknown action"

    returncode, output = script_runner.run(
        script_name, timeout=script_runner.timeout_for(action), args=script_args(action, target)
    )
    return ("SUCCESS", output) if returncode == 0 else ("FAILED", output)
