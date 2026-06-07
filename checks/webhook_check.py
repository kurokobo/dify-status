from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from checks.base import BaseCheck, CheckResult, Status, body_snippet, logger

STATE_DIR = Path(__file__).resolve().parent.parent / "data" / ".webhook_state"

# HTTP status codes that indicate a timeout / gateway timeout (server may have accepted the request)
TIMEOUT_STATUS_CODES = frozenset({408, 504, 524})


class WebhookCheck(BaseCheck):
    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.trigger_url: str = self.params["trigger_url"]
        self.base_url: str = self.params["base_url"].rstrip("/")
        self.timeout: int = self.params.get("timeout", 30)
        self.accounts: list[dict[str, str]] = self.params["accounts"]
        self._state_file = STATE_DIR / f"{self.check_id}.json"

    def _trigger_full_url(self, account: dict[str, str]) -> str:
        token = os.environ.get(account["trigger_token_env"], "")
        return f"{self.trigger_url}/{token}"

    def _api_headers(self, account: dict[str, str]) -> dict[str, str]:
        api_key = os.environ.get(account["api_key_env"], "")
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _load_state(self) -> dict[str, Any] | None:
        if self._state_file.exists():
            return json.loads(self._state_file.read_text(encoding="utf-8"))
        return None

    def _save_state(self, state: dict[str, Any]) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )

    def _clear_state(self) -> None:
        if self._state_file.exists():
            self._state_file.unlink()

    async def run(self) -> list[CheckResult]:
        results: list[CheckResult] = []

        # Step 1: Check previous trigger if state exists
        state = self._load_state()
        if state is not None:
            # account_index missing in old state files → default to 0 (backward compat)
            account_index = state.get("account_index", 0)
            account = self.accounts[account_index % len(self.accounts)]
            result = await self._check_previous(state, account)
            results.append(result)
            self._clear_state()

        # Step 2: Trigger new webhook using next account
        if state is not None:
            next_index = (state.get("account_index", 0) + 1) % len(self.accounts)
        else:
            next_index = 0

        results.append(await self._trigger_webhook(next_index))

        return results

    async def _check_previous(
        self, state: dict[str, Any], account: dict[str, str]
    ) -> CheckResult:
        trigger_id = state["trigger_id"]
        triggered_at = state["triggered_at"]
        timed_out = state.get("trigger_timed_out", False)
        timeout_elapsed_ms = state.get("trigger_elapsed_ms", -1)
        timeout_error = state.get("trigger_error", "")
        timeout_note = f" (trigger had timed out: {timeout_elapsed_ms}ms - {timeout_error})" if timed_out else ""

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, follow_redirects=True
            ) as client:
                # Search logs by trigger_id using keyword parameter (1 API call)
                url = f"{self.base_url}/workflows/logs?keyword={trigger_id}&limit=1"
                logger.info("[%s] GET %s (account_index=%d)", self.check_id, url, state.get("account_index", 0))
                resp = await client.get(url, headers=self._api_headers(account))
                content_type = resp.headers.get("content-type", "")
                logger.info("[%s] Response: HTTP %d, body: %s", self.check_id, resp.status_code, body_snippet(resp.text, content_type))

                if resp.status_code != 200:
                    return self._result(
                        Status.DOWN, -1,
                        f"Failed to fetch workflow logs: HTTP {resp.status_code}",
                        timestamp=triggered_at,
                    )

                logs = resp.json().get("data", [])
                if not logs:
                    if timed_out:
                        msg = f"Webhook trigger had timed out ({timeout_elapsed_ms}ms) and was not processed"
                    else:
                        msg = "Webhook trigger not processed within check interval"
                    return self._result(
                        Status.DOWN, -1,
                        msg,
                        timestamp=triggered_at,
                    )

                workflow_run = logs[0].get("workflow_run", {})
                status = workflow_run.get("status", "")
                logger.info("[%s] Workflow run status: %s", self.check_id, status)

                if status == "succeeded":
                    elapsed = workflow_run.get("elapsed_time", 0)
                    elapsed_ms = int(elapsed * 1000)
                    return self._result(
                        Status.UP, elapsed_ms,
                        f"Processed{timeout_note}",
                        timestamp=triggered_at,
                    )
                elif status == "failed":
                    error = workflow_run.get("error", "unknown error")
                    return self._result(
                        Status.DOWN, -1,
                        f"Webhook processing failed: {error}{timeout_note}",
                        timestamp=triggered_at,
                    )
                else:
                    return self._result(
                        Status.DOWN, -1,
                        f"Webhook not completed (status: {status}){timeout_note}",
                        timestamp=triggered_at,
                    )

        except httpx.TimeoutException:
            logger.warning("[%s] Timeout checking workflow logs", self.check_id)
            return self._result(Status.DOWN, -1, "Timeout checking workflow logs",
                                timestamp=triggered_at)
        except Exception as exc:
            logger.error("[%s] Error checking status: %s", self.check_id, exc)
            return self._result(Status.DOWN, -1, f"Error checking status: {exc}",
                                timestamp=triggered_at)

    async def _trigger_webhook(self, account_index: int) -> CheckResult:
        """Trigger the webhook. Returns a provisional UP result on success, or DOWN on failure."""
        account = self.accounts[account_index]
        trigger_id = f"status-check-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)
        triggered_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        body = {
            "id": trigger_id,
            "timestamp": int(now.timestamp()),
        }

        try:
            trigger_url = self._trigger_full_url(account)
            logger.info("[%s] POST %s (account_index=%d, trigger_id=%s)", self.check_id, trigger_url, account_index, trigger_id)
            start = time.monotonic()
            async with httpx.AsyncClient(
                timeout=self.timeout, follow_redirects=True
            ) as client:
                resp = await client.post(
                    trigger_url,
                    json=body,
                    headers={"Content-Type": "application/json"},
                )
                elapsed_ms = int((time.monotonic() - start) * 1000)

                content_type = resp.headers.get("content-type", "")
                logger.info("[%s] Response: HTTP %d (%dms), body: %s", self.check_id, resp.status_code, elapsed_ms, body_snippet(resp.text, content_type))

                if resp.status_code in TIMEOUT_STATUS_CODES:
                    error_msg = f"HTTP {resp.status_code}"
                    logger.warning("[%s] Webhook trigger returned timeout status %d (%dms), saving state for next cycle", self.check_id, resp.status_code, elapsed_ms)
                    self._save_state({
                        "trigger_id": trigger_id,
                        "triggered_at": triggered_at,
                        "account_index": account_index,
                        "trigger_timed_out": True,
                        "trigger_elapsed_ms": elapsed_ms,
                        "trigger_error": error_msg,
                    })
                    result = self._result(
                        Status.DEGRADED, elapsed_ms,
                        f"Webhook trigger timed out ({elapsed_ms}ms, {error_msg}), may have been received. Will verify in the next cycle",
                        timestamp=triggered_at,
                    )
                    result.provisional = True
                    return result

                if resp.status_code != 200:
                    return self._result(
                        Status.DOWN, elapsed_ms,
                        f"Webhook trigger failed: HTTP {resp.status_code}",
                    )

                self._save_state({
                    "trigger_id": trigger_id,
                    "triggered_at": triggered_at,
                    "account_index": account_index,
                })
                result = self._result(
                    Status.UP, -1,
                    "Triggered, will check execution status in the next cycle",
                    timestamp=triggered_at,
                )
                result.provisional = True
                return result

        except httpx.TimeoutException as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            error_msg = str(exc)
            logger.warning("[%s] Webhook trigger timed out (%dms): %s. Saving state for next cycle", self.check_id, elapsed_ms, error_msg)
            self._save_state({
                "trigger_id": trigger_id,
                "triggered_at": triggered_at,
                "account_index": account_index,
                "trigger_timed_out": True,
                "trigger_elapsed_ms": elapsed_ms,
                "trigger_error": error_msg,
            })
            result = self._result(
                Status.DEGRADED, elapsed_ms,
                f"Webhook trigger timed out ({elapsed_ms}ms), may have been received. Will verify in the next cycle",
                timestamp=triggered_at,
            )
            result.provisional = True
            return result
        except Exception as exc:
            logger.error("[%s] Webhook trigger failed: %s", self.check_id, exc)
            return self._result(Status.DOWN, -1, f"Webhook trigger failed: {exc}")
