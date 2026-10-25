"""Versioned closed contracts, integer money, and canonical exact-content identities."""

import hashlib
import json
from datetime import date, datetime
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator


def canonical(body):
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(body):
    return hashlib.sha256(canonical(body).encode()).hexdigest()


def key(*parts):
    return str(uuid5(NAMESPACE_URL, canonical(parts)))


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Commercial(Closed):
    schema_version: Literal["commercial.v1"] = "commercial.v1"
    source: Literal["salesforce"] = "salesforce"
    tenant: str = Field(min_length=1, max_length=80)
    entity: str = Field(min_length=1, max_length=80)
    event_id: str = Field(min_length=1, max_length=100)
    opportunity_id: str = Field(min_length=1, max_length=100)
    account_id: str = Field(min_length=1, max_length=100)
    contract_id: str = Field(min_length=1, max_length=100)
    owner_id: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=1)
    stage: Literal["Closed Won", "Negotiation"]
    currency: Literal["USD"] = "USD"
    unit_monthly_cents: int = Field(gt=0, le=10**10)
    quantity: int = Field(gt=0, le=10000)
    term_months: Literal[12] = 12
    billing_months: Literal[3] = 3
    effective_start: str
    effective_end: str
    event_at: str
    correction_of: str | None = None

    @model_validator(mode="after")
    def times(self):
        start, end = (
            date.fromisoformat(self.effective_start),
            date.fromisoformat(self.effective_end),
        )
        if end <= start:
            raise ValueError("invalid_effective_interval")
        if start.day != 1 or (end.year, end.month, end.day) != (start.year + 1, start.month, 1):
            raise ValueError("first_day_twelve_month_contract_required")
        if datetime.fromisoformat(self.event_at).utcoffset() is None:
            raise ValueError("aware_event_timestamp_required")
        return self


class Effect(Closed):
    schema_version: Literal["effect.v1"] = "effect.v1"
    effect_id: str
    tenant: str
    entity: str
    canonical_account: str
    destination_customer: str
    source_event: str
    source_revision: int = Field(ge=1)
    operation: Literal[
        "activate_and_invoice",
        "post_invoice_and_cash",
        "apply_contract_change",
        "post_contract_change",
    ]
    currency: Literal["USD"] = "USD"
    facts: dict


class Fault(Closed):
    effect_id: str
    modes: list[Literal["ok", "lost_ack", "unavailable", "reject"]] = Field(max_length=8)


class Ingest(Closed):
    event: Commercial


class Adjustment(Closed):
    schema_version: Literal["financial-adjustment.v1"] = "financial-adjustment.v1"
    event_id: str = Field(min_length=1, max_length=100)
    logical_id: str = Field(min_length=1, max_length=100)
    tenant: str
    entity: str
    kind: Literal["credit", "proration", "credit_posting", "proration_posting"]
    invoice_id: str
    amount_cents: int = Field(gt=0, le=10**11)
    currency: Literal["USD"] = "USD"
    revision: int = Field(ge=1)
    corrects_event: str | None = None
    effective_date: str
    observed_source_at: str
    reason: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def temporal(self):
        date.fromisoformat(self.effective_date)
        if datetime.fromisoformat(self.observed_source_at).utcoffset() is None:
            raise ValueError("aware_observed_timestamp_required")
        if (self.revision == 1) != (self.corrects_event is None):
            raise ValueError("explicit_revision_chain_required")
        return self


class ContractChange(Closed):
    schema_version: Literal["contract-change.v1"] = "contract-change.v1"
    source: Literal["salesforce"] = "salesforce"
    tenant: str = Field(min_length=1, max_length=80)
    entity: str = Field(min_length=1, max_length=80)
    event_id: str = Field(min_length=1, max_length=100)
    opportunity_id: str
    account_id: str
    contract_id: str
    owner_id: str
    revision: int = Field(ge=2)
    parent_event_id: str
    change_id: str
    action: Literal["expansion", "contraction", "cancellation", "renewal", "correction"]
    correction_of: str | None = None
    currency: Literal["USD"] = "USD"
    unit_monthly_cents: int = Field(ge=0, le=10**10)
    quantity: int = Field(ge=0, le=10000)
    effective_start: str
    effective_end: str
    event_at: str
    observed_source_at: str

    @model_validator(mode="after")
    def valid_change(self):
        start, end = (
            date.fromisoformat(self.effective_start),
            date.fromisoformat(self.effective_end),
        )
        if start.day != 1 or end.day != 1 or end <= start:
            raise ValueError("whole_month_effective_interval_required")
        if (self.action == "cancellation") != (self.quantity == 0 and self.unit_monthly_cents == 0):
            raise ValueError("only_cancellation_can_zero_subscription")
        if self.action != "cancellation" and (self.quantity == 0 or self.unit_monthly_cents == 0):
            raise ValueError("positive_subscription_required")
        if (self.action == "correction") != (self.correction_of is not None):
            raise ValueError("explicit_correction_required")
        for stamp in (self.event_at, self.observed_source_at):
            if datetime.fromisoformat(stamp).utcoffset() is None:
                raise ValueError("aware_timestamp_required")
        return self
