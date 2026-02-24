from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from checks.base import BaseCheck, CheckResult, Status

STATE_DIR = Path(__file__).resolve().parent.parent / "data" / ".knowledge_state"


class KnowledgeCheck(BaseCheck):
    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.base_url: str = self.params["base_url"].rstrip("/")
        self.timeout: int = self.params.get("timeout", 30)
        self.accounts: list[dict[str, str]] = self.params["accounts"]
        self._state_file = STATE_DIR / f"{self.check_id}.json"

    def _headers(self, account: dict[str, str]) -> dict[str, str]:
        api_key = os.environ.get(account["api_key_env"], "")
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _dataset_id(self, account: dict[str, str]) -> str:
        return os.environ.get(account["dataset_id_env"], "")

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
            # account_index missing in old state files → default to 0 (backward compat)
            account_index = state.get("account_index", 0)
            account = self.accounts[account_index % len(self.accounts)]
            result = await self._check_previous(state, account)
            results.append(result)
            self._clear_state()

        # Step 2: Upload new document using next account
        if state is not None:
            next_index = (state.get("account_index", 0) + 1) % len(self.accounts)
        else:
            next_index = 0

        results.append(await self._upload_document(next_index))

        return results

    async def _check_previous(
        self, state: dict[str, Any], account: dict[str, str]
    ) -> CheckResult:
        document_id = state["document_id"]
        batch_id = state["batch_id"]
        dataset_id = self._dataset_id(account)
        uploaded_at = state["uploaded_at"]

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, follow_redirects=True
            ) as client:
                url = f"{self.base_url}/datasets/{dataset_id}/documents/{batch_id}/indexing-status"
                resp = await client.get(url, headers=self._headers(account))

                if resp.status_code != 200:
                    await self._delete_document(client, account, dataset_id, document_id)
                    return self._result(
                        Status.DOWN, -1,
                        f"Status check failed: HTTP {resp.status_code}",
                        timestamp=uploaded_at,
                    )

                data = resp.json().get("data", [])
                if not data:
                    await self._delete_document(client, account, dataset_id, document_id)
                    return self._result(
                        Status.DOWN, -1, "Status check returned empty data",
                        timestamp=uploaded_at,
                    )

                doc_status = data[0]
                indexing_status = doc_status.get("indexing_status", "")

                if indexing_status == "completed":
                    started_at = doc_status.get("processing_started_at", 0)
                    completed_at = doc_status.get("completed_at", 0)
                    duration_ms = int((completed_at - started_at) * 1000)
                    await self._delete_document(client, account, dataset_id, document_id)
                    duration_s = round(duration_ms / 1000)
                    return self._result(
                        Status.UP, duration_ms,
                        f"Indexing completed in {duration_s}s",
                        timestamp=uploaded_at,
                    )
                elif indexing_status == "error":
                    error_msg = doc_status.get("error", "unknown error")
                    await self._delete_document(client, account, dataset_id, document_id)
                    return self._result(
                        Status.DOWN, -1, f"Indexing failed: {error_msg}",
                        timestamp=uploaded_at,
                    )
                else:
                    # Still indexing after ~15 minutes
                    await self._delete_document(client, account, dataset_id, document_id)
                    return self._result(
                        Status.DOWN, -1,
                        f"Indexing not completed within ~15 minutes (status: {indexing_status})",
                        timestamp=uploaded_at,
                    )

        except httpx.TimeoutException:
            return self._result(Status.DOWN, -1, "Timeout checking indexing status",
                                timestamp=uploaded_at)
        except Exception as exc:
            return self._result(Status.DOWN, -1, f"Error checking status: {exc}",
                                timestamp=uploaded_at)

    async def _upload_document(self, account_index: int) -> CheckResult:
        """Upload a new document. Returns a provisional UP result on success, or DOWN on failure."""
        account = self.accounts[account_index]
        dataset_id = self._dataset_id(account)
        now = datetime.now(timezone.utc)
        uploaded_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        doc_name = f"status-check-{now.strftime('%Y%m%d-%H%M%S')}"

        random_text = str(uuid.uuid4()).replace("-", " ")

        body = {
            "name": doc_name,
            "text": random_text,
            "indexing_technique": "economy",
            "process_rule": {"mode": "automatic"},
        }

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, follow_redirects=True
            ) as client:
                url = f"{self.base_url}/datasets/{dataset_id}/document/create-by-text"
                resp = await client.post(url, headers=self._headers(account), json=body)

                if resp.status_code != 200:
                    return self._result(
                        Status.DOWN, -1,
                        f"Upload failed: HTTP {resp.status_code}",
                    )

                resp_data = resp.json()
                document = resp_data.get("document", {})
                document_id = document.get("id", "")
                batch_id = resp_data.get("batch", "")

                if not document_id or not batch_id:
                    return self._result(
                        Status.DOWN, -1,
                        "Upload failed: missing document ID or batch ID in response",
                    )

                self._save_state({
                    "document_id": document_id,
                    "batch_id": batch_id,
                    "uploaded_at": uploaded_at,
                    "account_index": account_index,
                })
                result = self._result(
                    Status.UP, -1,
                    "Document uploaded. Awaiting next cycle to verify indexing status.",
                    timestamp=uploaded_at,
                )
                result.provisional = True
                return result

        except httpx.TimeoutException:
            return self._result(Status.DOWN, -1, "Upload timeout")
        except Exception as exc:
            return self._result(Status.DOWN, -1, f"Upload failed: {exc}")

    async def _delete_document(
        self,
        client: httpx.AsyncClient,
        account: dict[str, str],
        dataset_id: str,
        document_id: str,
    ) -> None:
        """Best-effort delete of the document."""
        try:
            url = f"{self.base_url}/datasets/{dataset_id}/documents/{document_id}"
            await client.delete(url, headers=self._headers(account))
        except Exception:
            pass  # Best-effort cleanup
