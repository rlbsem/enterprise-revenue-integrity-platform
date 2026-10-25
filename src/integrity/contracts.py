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
    operation: Literal["activate_and_invoice", "post_invoice_and_cash"]
    currency: Literal["USD"] = "USD"
    facts: dict


class Fault(Closed):
    effect_id: str
    modes: list[Literal["ok", "lost_ack", "unavailable", "reject"]] = Field(max_length=8)


class Ingest(Closed):
    event: Commercial
