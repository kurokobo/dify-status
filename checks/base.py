from __future__ import annotations

import enum
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from abc import ABC, abstractmethod
from typing import Any


class Status(str, enum.Enum):
    UP = "up"
    DEGRADED = "degraded"
    DOWN = "down"


@dataclass
class CheckResult:
    check_id: str
    timestamp: str
    status: Status
    response_time_ms: int
    message: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


class BaseCheck(ABC):
    def __init__(self, config: dict[str, Any]) -> None:
        self.check_id: str = config["id"]
        self.name: str = config["name"]
        self.params: dict[str, Any] = config.get("params", {})

    @abstractmethod
    async def run(self) -> CheckResult:
        ...

    def _result(
        self, status: Status, response_time_ms: int, message: str
    ) -> CheckResult:
        return CheckResult(
            check_id=self.check_id,
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            status=status,
            response_time_ms=response_time_ms,
            message=message,
        )
