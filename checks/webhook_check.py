from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from checks.base import BaseCheck, CheckResult, Status

STATE_DIR = Path(__file__).resolve().parent.parent / "data" / ".webhook_state"


class WebhookCheck(BaseCheck):
    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.trigger_url: str = self.params["trigger_url"]
        self.trigger_token_env: str = self.params["trigger_token_env"]
        self.base_url: str = self.params["base_url"].rstrip("/")
        self.api_key_env: str = self.params["api_key_env"]
        self.timeout: int = self.params.get("timeout", 30)
        self.interval_minutes: int = self.params.get("interval_minutes", 0)
        self._state_file = STATE_DIR / f"{self.check_id}.json"

    def _trigger_full_url(self) -> str:
        token = os.environ.get(self.trigger_token_env, "")
        return f"{self.trigger_url}/{token}"

    def _api_headers(self) -> dict[str, str]:
        api_key = os.environ.get(self.api_key_env, "")
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
            if self.interval_minutes > 0:
                triggered_at = datetime.fromisoformat(state["triggered_at"])
                min_wait = timedelta(minutes=self.interval_minutes)
                if datetime.now(timezone.utc) - triggered_at < min_wait:
                    return []

            result = await self._check_previous(state)
            results.append(result)
            self._clear_state()

        # Step 2: Trigger new webhook
        trigger_result = await self._trigger_webhook()
        if trigger_result is not None:
            results.append(trigger_result)

        return results

    async def _check_previous(self, state: dict[str, Any]) -> CheckResult:
        trigger_id = state["trigger_id"]
        triggered_at = state["triggered_at"]

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, follow_redirects=True
            ) as client:
                # Search logs by trigger_id using keyword parameter (1 API call)
                url = f"{self.base_url}/workflows/logs?keyword={trigger_id}&limit=1"
                resp = await client.get(url, headers=self._api_headers())

                if resp.status_code != 200:
                    return self._result(
                        Status.DOWN, -1,
                        f"Failed to fetch workflow logs: HTTP {resp.status_code}",
                        timestamp=triggered_at,
                    )

                logs = resp.json().get("data", [])
                if not logs:
                    return self._result(
                        Status.DOWN, -1,
                        "Webhook trigger not processed within check interval",
                        timestamp=triggered_at,
                    )

                workflow_run = logs[0].get("workflow_run", {})
                status = workflow_run.get("status", "")

                if status == "succeeded":
                    elapsed = workflow_run.get("elapsed_time", 0)
                    elapsed_ms = int(elapsed * 1000)
                    return self._result(
                        Status.UP, elapsed_ms,
                        f"Webhook processed in {elapsed:.1f}s",
                        timestamp=triggered_at,
                    )
                elif status == "failed":
                    error = workflow_run.get("error", "unknown error")
                    return self._result(
                        Status.DOWN, -1,
                        f"Webhook processing failed: {error}",
                        timestamp=triggered_at,
                    )
                else:
                    return self._result(
                        Status.DOWN, -1,
                        f"Webhook not completed (status: {status})",
                        timestamp=triggered_at,
                    )

        except httpx.TimeoutException:
            return self._result(Status.DOWN, -1, "Timeout checking workflow logs",
                                timestamp=triggered_at)
        except Exception as exc:
            return self._result(Status.DOWN, -1, f"Error checking status: {exc}",
                                timestamp=triggered_at)

    async def _trigger_webhook(self) -> CheckResult | None:
        """Trigger the webhook. Returns a CheckResult only on failure."""
        trigger_id = f"status-check-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)

        body = {
            "id": trigger_id,
            "timestamp": int(now.timestamp()),
        }

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, follow_redirects=True
            ) as client:
                start = time.monotonic()
                resp = await client.post(
                    self._trigger_full_url(),
                    json=body,
                    headers={"Content-Type": "application/json"},
                )
                elapsed_ms = int((time.monotonic() - start) * 1000)

                if resp.status_code != 200:
                    return self._result(
                        Status.DOWN, elapsed_ms,
                        f"Webhook trigger failed: HTTP {resp.status_code}",
                    )

                self._save_state({
                    "trigger_id": trigger_id,
                    "triggered_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                })
                return None  # Success — result will be checked next cycle

        except httpx.TimeoutException:
            return self._result(Status.DOWN, -1, "Webhook trigger timeout")
        except Exception as exc:
            return self._result(Status.DOWN, -1, f"Webhook trigger failed: {exc}")
