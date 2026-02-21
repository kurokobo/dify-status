from __future__ import annotations

import os
import time

import httpx

from checks.base import BaseCheck, CheckResult, Status


class HttpCheck(BaseCheck):
    async def run(self) -> CheckResult:
        url: str = self.params["url"]
        method: str = self.params.get("method", "GET").upper()
        expected_status: int = self.params.get("expected_status", 200)
        expected_body: str | None = self.params.get("expected_body")
        timeout: int = self.params.get("timeout", 30)
        api_key_env: str | None = self.params.get("api_key_env")

        headers: dict[str, str] = {}
        if api_key_env:
            api_key = os.environ.get(api_key_env, "")
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

        json_body = None
        if method == "POST" and api_key_env:
            headers["Content-Type"] = "application/json"
            json_body = {
                "inputs": {},
                "query": "ping",
                "response_mode": "blocking",
                "user": "status-checker",
                "auto_generate_name": False,
            }

        try:
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=True
            ) as client:
                start = time.monotonic()
                resp = await client.request(
                    method, url, headers=headers, json=json_body
                )
                elapsed_ms = int((time.monotonic() - start) * 1000)

            if resp.status_code == expected_status:
                if expected_body:
                    body = resp.text
                    if expected_body in body:
                        return self._result(Status.UP, elapsed_ms, f"HTTP {resp.status_code}, body contains '{expected_body}'")
                    return self._result(Status.DOWN, elapsed_ms, f"HTTP {resp.status_code}, body missing '{expected_body}'")
                return self._result(Status.UP, elapsed_ms, f"HTTP {resp.status_code}")

            # API returns 400/401/403 but the server itself is responding
            if resp.status_code in (400, 401, 403) and not expected_body:
                return self._result(
                    Status.UP, elapsed_ms, f"HTTP {resp.status_code} (auth/input error, server is responding)"
                )

            return self._result(
                Status.DOWN, elapsed_ms, f"HTTP {resp.status_code} (expected {expected_status})"
            )
        except httpx.TimeoutException:
            return self._result(Status.DOWN, -1, "Timeout")
        except httpx.ConnectError as exc:
            return self._result(Status.DOWN, -1, f"Connection error: {exc}")
        except Exception as exc:
            return self._result(Status.DOWN, -1, f"Error: {exc}")
