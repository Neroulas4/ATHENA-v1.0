"""Audited, idempotent email delivery downstream of persisted workflows."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from time import sleep
from typing import Protocol
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .email_render import EmailMessage, render_daily_brief, render_post_mortem
from .models import AnalysisResult, now_utc
from .store import Store

logger = logging.getLogger(__name__)


class DeliveryAttempt(BaseModel):
    model_config = ConfigDict(frozen=True)
    delivery_id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    delivery_type: str
    channel: str = "EMAIL"
    provider: str = "RESEND"
    recipients: tuple[str, ...] = ()
    attempted_at: datetime = Field(default_factory=now_utc)
    status: str
    provider_message_id: str | None = None
    error_class: str | None = None
    safe_error_detail: str | None = None
    retry_count: int = 0
    idempotency_key: str
    retryable: bool = False


@dataclass(frozen=True)
class SendReceipt:
    message_id: str


class DeliveryError(Exception):
    def __init__(self, error_class: str, detail: str, *, retryable: bool) -> None:
        super().__init__(detail)
        self.error_class, self.detail, self.retryable = error_class, detail, retryable


class EmailDeliveryProvider(Protocol):
    name: str
    def send(self, sender: str, recipients: tuple[str, ...], message: EmailMessage, idempotency_key: str) -> SendReceipt: ...


class ResendEmailProvider:
    name = "RESEND"
    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=15)

    def send(self, sender: str, recipients: tuple[str, ...], message: EmailMessage, idempotency_key: str) -> SendReceipt:
        try:
            response = self.client.post("https://api.resend.com/emails", headers={"Authorization": f"Bearer {self.api_key}", "Idempotency-Key": idempotency_key}, json={"from": sender, "to": list(recipients), "subject": message.subject, "html": message.html, "text": message.text})
        except httpx.RequestError as exc:
            raise DeliveryError(type(exc).__name__, "Resend transport failure", retryable=True) from exc
        if response.status_code >= 400:
            raise DeliveryError("ResendHTTPError", f"Resend HTTP {response.status_code}", retryable=response.status_code == 429 or response.status_code >= 500)
        try: message_id = response.json()["id"]
        except (ValueError, KeyError, TypeError) as exc:
            raise DeliveryError("ResendResponseError", "Resend accepted request without a message ID", retryable=True) from exc
        return SendReceipt(str(message_id))


class FakeEmailProvider:
    name = "FAKE"
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.sent: list[tuple[str, tuple[str, ...], EmailMessage, str]] = []

    def send(self, sender: str, recipients: tuple[str, ...], message: EmailMessage, idempotency_key: str) -> SendReceipt:
        if self.failures:
            self.failures -= 1
            raise DeliveryError("FakeTransientError", "Temporary fake failure", retryable=True)
        self.sent.append((sender, recipients, message, idempotency_key))
        return SendReceipt(f"fake-{len(self.sent)}")


class DeliveryService:
    def __init__(self, store: Store, provider: EmailDeliveryProvider | None = None, *, sender: str | None = None, recipients: tuple[str, ...] | None = None, api_key: str | None = None) -> None:
        self.store = store
        self.sender = sender if sender is not None else os.getenv("ATHENA_EMAIL_FROM", "").strip()
        self.recipients = recipients if recipients is not None else tuple(x.strip() for x in os.getenv("ATHENA_EMAIL_TO", "").split(",") if x.strip())
        key = api_key if api_key is not None else os.getenv("RESEND_API_KEY", "")
        self.provider = provider or (ResendEmailProvider(key) if key else None)

    def deliver(self, result: AnalysisResult, kind: str) -> DeliveryAttempt | None:
        if kind not in {"DAILY_BRIEF", "POST_MORTEM"}: raise ValueError("Only scheduled report kinds may be emailed")
        if self.store.get_run_result(result.run_id) is None: raise ValueError("Report must be persisted before email delivery")
        key = f"athena-email/{kind}/{result.run_id}"
        retry_count = self.store.claim_delivery(key, result.run_id)
        if retry_count is None:
            existing = self.store.deliveries_for_run(result.run_id)
            return existing[-1] if existing else None
        while True:
            status = "PENDING"
            message_id = error_class = detail = None
            retryable = False
            if not self.sender or not self.recipients or self.provider is None:
                status = "NOT_CONFIGURED"
            else:
                message = render_daily_brief(result) if kind == "DAILY_BRIEF" else render_post_mortem(result, self.store)
                try:
                    receipt = self.provider.send(self.sender, self.recipients, message, key)
                    message_id, status = receipt.message_id, "SENT"
                except DeliveryError as exc:
                    status, error_class, detail, retryable = "FAILED", exc.error_class, exc.detail, exc.retryable
                except Exception as exc:
                    status, error_class, detail, retryable = "FAILED", type(exc).__name__, "Unexpected email provider failure", False
            attempt = DeliveryAttempt(run_id=result.run_id, delivery_type=kind, provider=self.provider.name if self.provider else "RESEND", recipients=self.recipients, status=status, provider_message_id=message_id, error_class=error_class, safe_error_detail=detail, retry_count=retry_count, idempotency_key=key, retryable=retryable)
            self.store.record_delivery(attempt)
            logger.info("delivery_attempt", extra={"run_id": result.run_id, "delivery_id": attempt.delivery_id, "provider_message_id": message_id, "status": status, "retry_count": retry_count})
            if not retryable or retry_count >= 2: return attempt
            claimed = self.store.claim_delivery(key, result.run_id)
            if claimed is None: return attempt
            retry_count = claimed
            sleep(min(0.25 * 2 ** (retry_count - 1), 1.0))
