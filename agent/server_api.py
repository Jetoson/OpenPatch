"""All calls the agent makes to the OpenPatch server.
"""
class ServerAPI:
    def __init__(self, session, server_url: str, device_id: str, token: str):
        self.session = session
        self.server_url = server_url.rstrip("/")
        self.device_id = device_id
        self.token = token

    def _url(self, path: str) -> str:
        return f"{self.server_url}/api/v1/agent{path}"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def set_credentials(self, server_url: str, token: str) -> None:
        """Adopts a freshly issued token after re-enrolment."""
        self.server_url = server_url.rstrip("/")
        self.token = token

    def heartbeat(self, payload: dict):
        """Reports essential telemetry and collect any pending tasks."""
        return self.session.post(self._url("/heartbeat"), json=payload, headers=self._headers())

    def report_task_result(self, task_id: int, status: str, output: str):
        return self.session.post(
            self._url("/task/result"),
            json={
                "device_id": self.device_id,
                "task_id": task_id,
                "status": status,
                "output": output,
            },
            headers=self._headers(),
        )

    def task_status(self, task_id: int, timeout: int = 10):
        return self.session.get(
            self._url(f"/task/{task_id}/status"), headers=self._headers(), timeout=timeout
        )

    def send_inventory(self, software_list: list):
        return self.session.post(
            self._url("/inventory"),
            json={"device_id": self.device_id, "software_list": software_list},
            headers=self._headers(),
        )

    def send_pending_updates(self, updates: list, timeout: int = 30):
        return self.session.post(
            self._url("/updates"),
            json={"device_id": self.device_id, "updates": updates},
            headers=self._headers(),
            timeout=timeout,
        )
