"""Governed explanation categories. Categories describe different bases; never sum the bridge blindly."""

CATEGORIES = {
    "activation_lag": "Approved commitment has no activated subscription; operations review required",
    "contract_mismatch": "Invoice economics differ from known commercial service-period obligations",
    "proration": "Explicit service-period billing adjustment",
    "credit": "Latest known credit; original and corrections retained",
    "partial_settlement": "Net invoice cohort not fully covered by verified allocations",
    "posting_lag": "A recorded posting exists outside the compared accounting period",
    "unmapped_legal_entity": "No approved canonical mapping in the contract's legal entity",
    "backdated_correction": "Later observation revises an earlier business-effective fact",
    "duplicate_source": "Repeated delivery acknowledged without another source fact",
}


def complete_bridge(snapshot, bridge):
    entries = bridge["entries"]
    extra = []
    for entry in entries:
        if entry["category"] == "unsettled_invoice_balance":
            entry["category"] = "partial_settlement"
        entry["basis"] = (
            "invoice_composition"
            if entry["category"] in ("credit", "proration")
            else "comparison_difference"
        )
        if entry["category"] == "unposted_adjustment_requires_review":
            outside = [
                r
                for x in snapshot["snapshots"]
                if x["service"] == "netsuite"
                for r in x["contents"]["records"]
                if r.get("invoice_id") == entry["invoice_id"]
                and r["kind"] in ("posting", "change_posting")
                and r["posting_date"] >= snapshot["period"]["end_exclusive"]
            ]
            if outside:
                known = -sum(r["amount_cents"] for r in outside)
                extra.append(
                    {
                        **entry,
                        "category": "posting_lag",
                        "amount_cents": known,
                        "explanation": "Independent ERP record posted after the reporting period",
                        "source_record_ids": [r["id"] for r in outside],
                    }
                )
                entry["amount_cents"] -= known
    entries[:] = [e for e in entries if e.get("amount_cents") != 0] + extra
    for missing in bridge["unbilled_commitments"]:
        category = (
            "activation_lag"
            if missing["reason"] == "activation_lag_requires_review"
            else "unmapped_legal_entity"
        )
        entries.append(
            {
                **missing,
                "category": category,
                "amount_cents": missing["scheduled_quarter_cents"],
                "basis": "unbilled_commitment_not_in_invoice_totals",
                "owner": "commercial_operations",
                "explanation": "Mapping ambiguity or missing scoped mapping blocks activation"
                if category == "unmapped_legal_entity"
                else CATEGORIES[category],
            }
        )
    for correction in bridge["backdated_correction_lineage"]:
        entries.append(
            {
                **correction,
                "category": "backdated_correction",
                "basis": "source_revision_lineage",
                "owner": "billing" if correction["source"] == "stripe" else "finance",
                "amount_cents": None,
                "explanation": "Correction retained with separate effective and observed dates",
            }
        )
    duplicates = []
    for export in snapshot["snapshots"]:
        for delivery in export["contents"].get("source_deliveries", []):
            if delivery["duplicate"]:
                duplicates.append(
                    {
                        "source": export["service"],
                        "tenant": export["tenant"],
                        "entity": export["entity"],
                        **delivery,
                    }
                )
    if duplicates:
        entries.append(
            {
                "category": "duplicate_source",
                "basis": "delivery_observation_not_money",
                "count": len(duplicates),
                "deliveries": duplicates,
                "amount_cents": None,
                "owner": "integration_operations",
                "explanation": "Actual duplicate source requests acknowledged; facts and money not duplicated",
            }
        )
    bridge.update(category_definitions=CATEGORIES, non_additive_bases=True)
    return bridge
