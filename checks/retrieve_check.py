from __future__ import annotations

import os
import time

import httpx

from checks.base import BaseCheck, CheckResult, Status


class RetrieveCheck(BaseCheck):
    async def run(self) -> CheckResult:
        base_url: str = self.params["base_url"].rstrip("/")
        dataset_id_env: str = self.params["dataset_id_env"]
        api_key_env: str = self.params["api_key_env"]
        query: str = self.params.get("query", "test")
        timeout: int = self.params.get("timeout", 30)

        dataset_id = os.environ.get(dataset_id_env, "")
        api_key = os.environ.get(api_key_env, "")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        body = {
            "query": query,
            "retrieval_model": {
                "search_method": "semantic_search",
                "reranking_enable": False,
                "reranking_mode": None,
                "reranking_model": {
                    "reranking_provider_name": "",
                    "reranking_model_name": "",
                },
                "weights": None,
                "top_k": 1,
                "score_threshold_enabled": False,
                "score_threshold": None,
            },
        }

        url = f"{base_url}/datasets/{dataset_id}/retrieve"

        try:
            print(f"  [{self.check_id}] POST {url}")
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=True
            ) as client:
                start = time.monotonic()
                resp = await client.post(url, headers=headers, json=body)
                elapsed_ms = int((time.monotonic() - start) * 1000)

            body_snippet = resp.text[:200] if resp.text else "(empty)"
            print(f"  [{self.check_id}] Response: HTTP {resp.status_code} ({elapsed_ms}ms), body: {body_snippet}")

            if resp.status_code != 200:
                return self._result(
                    Status.DOWN, elapsed_ms,
                    f"HTTP {resp.status_code} (expected 200)",
                )

            data = resp.json()
            if "records" not in data:
                return self._result(
                    Status.DOWN, elapsed_ms,
                    "Response missing 'records' field",
                )

            return self._result(
                Status.UP, elapsed_ms,
                f"HTTP {resp.status_code}",
            )

        except httpx.TimeoutException:
            print(f"  [{self.check_id}] Timeout after {timeout}s")
            return self._result(Status.DOWN, -1, "Timeout")
        except httpx.ConnectError as exc:
            print(f"  [{self.check_id}] Connection error: {exc}")
            return self._result(Status.DOWN, -1, f"Connection error: {exc}")
        except Exception as exc:
            print(f"  [{self.check_id}] Error: {exc}")
            return self._result(Status.DOWN, -1, f"Error: {exc}")
