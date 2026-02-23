from __future__ import annotations

from typing import TYPE_CHECKING

from checks.http_check import HttpCheck
from checks.knowledge_check import KnowledgeCheck
from checks.webhook_check import WebhookCheck

if TYPE_CHECKING:
    from checks.base import BaseCheck

CHECK_TYPES: dict[str, type[BaseCheck]] = {
    "http": HttpCheck,
    "knowledge": KnowledgeCheck,
    "webhook": WebhookCheck,
}
