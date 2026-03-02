from __future__ import annotations

import os
import time

import httpx

from checks.base import BaseCheck, CheckResult, Status, body_snippet, logger


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
            logger.info("[%s] %s %s", self.check_id, method, url)
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
                    logger.info("[%s] Headers received: HTTP %d (%dms)", self.check_id, status_code, elapsed_headers_ms)
                    logger.info("[%s] Response headers: content-type=%s, transfer-encoding=%s, content-length=%s", self.check_id, resp_headers.get('content-type', 'N/A'), resp_headers.get('transfer-encoding', 'N/A'), resp_headers.get('content-length', 'N/A'))

                    try:
                        body = (await resp.aread()).decode("utf-8", errors="replace")
                    except Exception as read_exc:
                        elapsed_ms = int((time.monotonic() - start) * 1000)
                        partial = resp.stream._buffer if hasattr(resp.stream, "_buffer") else b""  # noqa: SLF001
                        partial_text = bytes(partial).decode("utf-8", errors="replace")[:200] if partial else "(no partial data)"
                        logger.error("[%s] Body read failed after %dms: %s: %s", self.check_id, elapsed_ms, type(read_exc).__name__, read_exc)
                        logger.error("[%s] Partial body: %s", self.check_id, partial_text)
                        return self._result(
                            Status.DOWN, elapsed_ms,
                            f"HTTP {status_code}, body read failed: {type(read_exc).__name__}: {read_exc}",
                        )

                elapsed_ms = int((time.monotonic() - start) * 1000)

            content_type = resp_headers.get("content-type", "")
            logger.info("[%s] Response: HTTP %d (%dms), body: %s", self.check_id, status_code, elapsed_ms, body_snippet(body, content_type))

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
            logger.warning("[%s] Timeout after %ds", self.check_id, timeout)
            return self._result(Status.DOWN, -1, "Timeout")
        except httpx.ConnectError as exc:
            logger.error("[%s] Connection error: %s", self.check_id, exc)
            return self._result(Status.DOWN, -1, f"Connection error: {exc}")
        except Exception as exc:
            logger.error("[%s] Error: %s: %s", self.check_id, type(exc).__name__, exc)
            return self._result(Status.DOWN, -1, f"Error: {exc}")
