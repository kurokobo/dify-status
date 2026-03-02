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
            print(f"  [{self.check_id}] {method} {url}")
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=True
            ) as client:
                start = time.monotonic()
                async with client.stream(
                    method, url, headers=headers, json=json_body
                ) as resp:
                    status_code = resp.status_code
                    resp_headers = dict(resp.headers)
                    elapsed_headers_ms = int((time.monotonic() - start) * 1000)
                    print(f"  [{self.check_id}] Headers received: HTTP {status_code} ({elapsed_headers_ms}ms)")
                    print(f"  [{self.check_id}] Response headers: content-type={resp_headers.get('content-type', 'N/A')}, transfer-encoding={resp_headers.get('transfer-encoding', 'N/A')}, content-length={resp_headers.get('content-length', 'N/A')}")

                    try:
                        body = (await resp.aread()).decode("utf-8", errors="replace")
                    except Exception as read_exc:
                        elapsed_ms = int((time.monotonic() - start) * 1000)
                        partial = resp.stream._buffer if hasattr(resp.stream, "_buffer") else b""  # noqa: SLF001
                        partial_text = bytes(partial).decode("utf-8", errors="replace")[:200] if partial else "(no partial data)"
                        print(f"  [{self.check_id}] Body read failed after {elapsed_ms}ms: {type(read_exc).__name__}: {read_exc}")
                        print(f"  [{self.check_id}] Partial body: {partial_text}")
                        return self._result(
                            Status.DOWN, elapsed_ms,
                            f"HTTP {status_code}, body read failed: {type(read_exc).__name__}: {read_exc}",
                        )

                elapsed_ms = int((time.monotonic() - start) * 1000)

            body_snippet = body[:200] if body else "(empty)"
            print(f"  [{self.check_id}] Response: HTTP {status_code} ({elapsed_ms}ms), body: {body_snippet}")

            if status_code == expected_status:
                if expected_body:
                    if expected_body in body:
                        return self._result(Status.UP, elapsed_ms, f"HTTP {status_code}")
                    return self._result(Status.DOWN, elapsed_ms, f"HTTP {status_code}, body missing '{expected_body}'")
                return self._result(Status.UP, elapsed_ms, f"HTTP {status_code}")

            # API returns 400/401/403 but the server itself is responding
            if status_code in (400, 401, 403) and not expected_body:
                return self._result(
                    Status.UP, elapsed_ms, f"HTTP {status_code} (auth/input error, server is responding)"
                )

            return self._result(
                Status.DOWN, elapsed_ms, f"HTTP {status_code} (expected {expected_status})"
            )
        except httpx.TimeoutException:
            print(f"  [{self.check_id}] Timeout after {timeout}s")
            return self._result(Status.DOWN, -1, "Timeout")
        except httpx.ConnectError as exc:
            print(f"  [{self.check_id}] Connection error: {exc}")
            return self._result(Status.DOWN, -1, f"Connection error: {exc}")
        except Exception as exc:
            print(f"  [{self.check_id}] Error: {type(exc).__name__}: {exc}")
            return self._result(Status.DOWN, -1, f"Error: {exc}")
