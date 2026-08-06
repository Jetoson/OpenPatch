import time
import requests
import psutil
import platform
import uuid
import subprocess
import os
import sys

# Orchestrator server URLs
SERVER_URL = "http://127.0.0.1:8000/api/v1/agent/heartbeat"
TASK_RESULT_URL = "http://127.0.0.1:8000/api/v1/agent/task/result"

# End point unique identifier
DEVICE_ID = str(uuid.getnode())

def get_telemetry():
    return {
        "device_id": DEVICE_ID,
        "hostname": platform.node(),
        "os_version": platform.version(),
        "cpu_usage_percent": psutil.cpu_percent(interval=1),
        "ram_usage_percent": psutil.virtual_memory().percent
    }


def execute_task(action: str):
    """Execute PowerShell scripts based on action string"""
    print(f"\n[!] Executing task: {action}", flush=True)

    script_path = ""
    if action == "UPDATE_WINGET":
        script_path = os.path.join("scripts", "update_winget.ps1")
    elif action == "UPDATE_OS":
        script_path = os.path.join("scripts", "update_os.ps1")
    else:
        return "FAILED", "Unimplemented action."

    if not os.path.exists(script_path):
        return "FAILED", f"Script not found at path: {os.path.abspath(script_path)}"

    try:
        # Run PowerShell silently
        result = subprocess.run(
            ["powershell.exe", "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", script_path],
            capture_output=True,
            text=True,
            timeout=300
        )

        if result.returncode == 0:
            return "SUCCESS", result.stdout
        else:
            return "FAILED", result.stderr

    except subprocess.TimeoutExpired:
        return "FAILED", "Task execution timed out after 300 seconds."
    except Exception as e:
        return "FAILED", str(e)


def start_polling():
    print(f"=== OpenPatch Agent Online ===", flush=True)
    print(f"Device ID: {DEVICE_ID}", flush=True)
    print(f"Target Server: {SERVER_URL}\n", flush=True)

    while True:
        try:
            payload = get_telemetry()
            print(
                f"[>] Sending Heartbeat... (CPU: {payload['cpu_usage_percent']}% | RAM: {payload['ram_usage_percent']}%)",
                flush=True)

            response = requests.post(SERVER_URL, json=payload)

            if response.status_code == 200:
                data = response.json()
                pending_tasks = data.get('pending_tasks', [])

                if pending_tasks:
                    print(f"[*] Found {len(pending_tasks)} pending task(s).", flush=True)

                for task in pending_tasks:
                    task_id = task['task_id']
                    action = task['action']

                    status, log_output = execute_task(action)

                    # Report task status back to FastAPI
                    result_payload = {
                        "task_id": task_id,
                        "status": status,
                        "log_output": log_output
                    }
                    requests.post(TASK_RESULT_URL, json=result_payload)
                    print(f"[✓] Task {task_id} completed with status: {status}\n", flush=True)
            else:
                print(f"[!] Server returned error code: {response.status_code}", flush=True)

        except requests.exceptions.ConnectionError:
            print("[X] Connection refused. Is the FastAPI server running on http://127.0.0.1:8000?", flush=True)
        except Exception as e:
            print(f"[X] Unexpected error in polling loop: {e}", flush=True)

        # Poll interval (5 seconds for development)
        time.sleep(5)


if __name__ == "__main__":
    try:
        start_polling()
    except KeyboardInterrupt:
        print("\n[!] Agent stopped manually by user.")
        sys.exit(0)