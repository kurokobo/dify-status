from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from checks.base import BaseCheck, CheckResult, Status

STATE_DIR = Path(__file__).resolve().parent.parent / "data" / ".knowledge_state"


class KnowledgeCheck(BaseCheck):
    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.base_url: str = self.params["base_url"].rstrip("/")
        self.dataset_id_env: str = self.params["dataset_id_env"]
        self.api_key_env: str = self.params["api_key_env"]
        self.timeout: int = self.params.get("timeout", 30)
        self.interval_minutes: int = self.params.get("interval_minutes", 0)
        self._state_file = STATE_DIR / f"{self.check_id}.json"

    def _headers(self) -> dict[str, str]:
        api_key = os.environ.get(self.api_key_env, "")
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _dataset_id(self) -> str:
        return os.environ.get(self.dataset_id_env, "")

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

        # Step 1: Check previous document if state exists
        state = self._load_state()
        if state is not None:
            # Skip if not enough time has passed since upload
            if self.interval_minutes > 0:
                uploaded_at = datetime.fromisoformat(state["uploaded_at"])
                min_wait = timedelta(minutes=self.interval_minutes)
                if datetime.now(timezone.utc) - uploaded_at < min_wait:
                    return []  # Too early, skip this cycle

            result = await self._check_previous(state)
            results.append(result)
            # Always clear state after checking
            self._clear_state()

        # Step 2: Upload new document
        upload_result = await self._upload_document()
        if upload_result is not None:
            # Upload failed — record the failure
            results.append(upload_result)

        return results

    async def _check_previous(self, state: dict[str, Any]) -> CheckResult:
        document_id = state["document_id"]
        batch_id = state["batch_id"]
        dataset_id = self._dataset_id()

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, follow_redirects=True
            ) as client:
                url = f"{self.base_url}/datasets/{dataset_id}/documents/{batch_id}/indexing-status"
                resp = await client.get(url, headers=self._headers())

                if resp.status_code != 200:
                    await self._delete_document(client, dataset_id, document_id)
                    return self._result(
                        Status.DOWN, -1,
                        f"Status check failed: HTTP {resp.status_code}",
                    )

                data = resp.json().get("data", [])
                if not data:
                    await self._delete_document(client, dataset_id, document_id)
                    return self._result(
                        Status.DOWN, -1, "Status check returned empty data"
                    )

                doc_status = data[0]
                indexing_status = doc_status.get("indexing_status", "")

                if indexing_status == "completed":
                    started_at = doc_status.get("processing_started_at", 0)
                    completed_at = doc_status.get("completed_at", 0)
                    duration_ms = int((completed_at - started_at) * 1000)
                    await self._delete_document(client, dataset_id, document_id)
                    duration_s = duration_ms / 1000
                    return self._result(
                        Status.UP, duration_ms,
                        f"Indexing completed in {duration_s:.1f}s",
                    )
                elif indexing_status == "error":
                    error_msg = doc_status.get("error", "unknown error")
                    await self._delete_document(client, dataset_id, document_id)
                    return self._result(
                        Status.DOWN, -1, f"Indexing failed: {error_msg}"
                    )
                else:
                    # Still indexing after ~15 minutes
                    await self._delete_document(client, dataset_id, document_id)
                    return self._result(
                        Status.DOWN, -1,
                        f"Indexing not completed within ~15 minutes (status: {indexing_status})",
                    )

        except httpx.TimeoutException:
            return self._result(Status.DOWN, -1, "Timeout checking indexing status")
        except Exception as exc:
            return self._result(Status.DOWN, -1, f"Error checking status: {exc}")

    async def _upload_document(self) -> CheckResult | None:
        """Upload a new document. Returns a CheckResult only on failure (None = success)."""
        dataset_id = self._dataset_id()
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        doc_name = f"status-check-{timestamp}"

        body = {
            "name": doc_name,
            "text": "ping",
            "indexing_technique": "economy",
            "process_rule": {"mode": "automatic"},
        }

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, follow_redirects=True
            ) as client:
                start = time.monotonic()
                url = f"{self.base_url}/datasets/{dataset_id}/document/create-by-text"
                resp = await client.post(url, headers=self._headers(), json=body)
                elapsed_ms = int((time.monotonic() - start) * 1000)

                if resp.status_code != 200:
                    return self._result(
                        Status.DOWN, elapsed_ms,
                        f"Upload failed: HTTP {resp.status_code}",
                    )

                resp_data = resp.json()
                document = resp_data.get("document", {})
                document_id = document.get("id", "")
                batch_id = resp_data.get("batch", "")

                if not document_id or not batch_id:
                    return self._result(
                        Status.DOWN, elapsed_ms,
                        "Upload failed: missing document ID or batch ID in response",
                    )

                self._save_state({
                    "document_id": document_id,
                    "batch_id": batch_id,
                    "uploaded_at": datetime.now(timezone.utc).isoformat(),
                })
                return None  # Success — no result to report yet

        except httpx.TimeoutException:
            return self._result(Status.DOWN, -1, "Upload timeout")
        except Exception as exc:
            return self._result(Status.DOWN, -1, f"Upload failed: {exc}")

    async def _delete_document(
        self, client: httpx.AsyncClient, dataset_id: str, document_id: str
    ) -> None:
        """Best-effort delete of the document."""
        try:
            url = f"{self.base_url}/datasets/{dataset_id}/documents/{document_id}"
            await client.delete(url, headers=self._headers())
        except Exception:
            pass  # Best-effort cleanup
