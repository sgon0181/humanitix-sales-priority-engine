"""Transparent public activity signals and separate internal action rules."""
from collections import Counter, defaultdict
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from .validation import timestamp

SIGNALS = ["repeated_dated_paid_offers", "one_dated_paid_offer",
           "undated_or_historical_paid_evidence", "paid_activity_unestablished"]
ACTIVE_QUEUES = ["urgent_clearance_review", "callback_review", "response_review",
                 "internal_clearance", "qualification_review", "evidence_research"]
OTHER_QUEUES = ["restricted", "existing_relationship", "deferred", "contact_policy_review"]


def public_assessment(account, clock, rules):
    ev = account.get("public_evidence") or {}
    today = clock.astimezone(ZoneInfo(rules["business_timezone"])).date()
    checked = date.fromisoformat(ev["checked_on"]) if ev else None
    fresh = checked is not None and 0 <= (today - checked).days <= rules["evidence_freshness_days"]
    identity = bool(ev) and account.get("identity_status") == "primary_identity_observed"
    scope = ev.get("us_scope", "unknown")
    scope_observed = scope in {"us_events_observed", "us_venue_observed_events_unresolved"}
    online_scope = scope in {"us_operations_and_online_audience_unknown", "us_based_online_audience_unknown"}
    eligible, positive, free, excluded, bundled = [], [], [], [], {}
    for offer in ev.get("offer_observations", []):
        index = offer["input_offer_index"]
        path = f"public_evidence.offer_observations[{index}]"
        if offer["price_min"] <= 0 or offer["price_basis"] in {"scholarship", "subscription", "membership_subscription"}:
            free.append(path)
            continue
        positive.append(path)
        start = date.fromisoformat(offer["event_start_date"]) if offer.get("event_start_date") else None
        age = (today - date.fromisoformat(offer["observed_on"])).days
        reason = None
        if not fresh or not 0 <= age <= rules["evidence_freshness_days"]:
            reason = "Source stale or observed after scenario date"
        elif offer["access_basis"] != "direct_primary":
            reason = "Direct primary refresh required"
        elif not start:
            reason = "Explicit event year/date missing"
        elif abs((start - today).days) > rules["offer_window_days"]:
            reason = "Outside declared activity window"
        elif offer["availability"] in {"conflicting_tier_text", "date_unresolved", "cancelled"}:
            reason = "Date, price or availability unresolved"
        elif start > today and offer["availability"] == "past_event":
            reason = "Future date conflicts with historical event status"
        if reason:
            excluded.append({"path": path, "reason": reason})
            continue
        item = {"path": path, "start": start.isoformat(), "name": offer["name"],
                "unit": offer["unit"], "source_url": offer["source_url"],
                "availability": offer["availability"]}
        # Repeated observations of the same package/pass cannot create recurrence.
        if "package" in offer["unit"] or "pass" in offer["unit"]:
            key = (offer["source_url"], offer["name"], offer["unit"])
            old = bundled.get(key)
            if old is None or item["start"] < old["start"]:
                bundled[key] = item
        else:
            eligible.append(item)
    eligible += list(bundled.values())
    eligible.sort(key=lambda x: (x["start"], x["path"]))
    # Different tiers and attendance modes on the same start date count once.
    starts = sorted({x["start"] for x in eligible})
    future = [x for x in eligible if x["start"] >= today.isoformat() and x["availability"] != "past_event"]
    current = [x for x in future if x["availability"] != "sold_out"]
    if future and len(starts) >= 2:
        signal = SIGNALS[0]
    elif future:
        signal = SIGNALS[1]
    elif positive:
        signal = SIGNALS[2]
    else:
        signal = SIGNALS[3]
    checks = []
    def gap(rule, field, action):
        checks.append({"rule": rule, "field": field, "action": action})
    if not identity:
        gap("Q1", "identity_status", "Verify organiser identity at the canonical website")
    if not scope_observed or online_scope:
        gap("Q2", "public_evidence.us_scope", "Verify US event/sales scope, including online audience geography where relevant")
    if not current:
        gap("Q4" if positive else "Q3", "public_evidence.offer_observations",
            "Refresh a current dated paid offer, its purchase unit and availability" if positive else
            "Verify a dated paid offer and separate free activity from paid admission")
    if not fresh:
        gap("Q4", "public_evidence.checked_on", "Refresh dated primary-source evidence")
    if account.get("validation_issues"):
        gap("Q4", "validation_issues", "Resolve quarantined evidence fields before qualification")
    sources = sorted(set(ev.get("source_urls", [])))
    return {
        "qualification": {
            "verification_status": "field_reviewed" if ev else "not_reviewed",
            "identity_observed": identity, "us_scope": scope, "evidence_fresh": fresh,
            "current_paid_offer_observed": bool(current),
            "state": "needs_public_evidence" if checks else "ready_for_internal_qualification",
            "unresolved_checks": checks,
            "dependencies": ev.get("dependency_observations", []),
            "provider_observations": ev.get("provider_observations", []),
            "scope_note": "Public activity is not proof of ticketing authority or switching intent",
        },
        "commercial_potential": {
            "activity_signal": signal, "annual_paid_tickets": None,
            "annual_ticket_value_usd": None, "annual_fee_potential_usd": None,
            "distinct_paid_start_dates": starts,
            "supporting_offers": eligible, "free_or_non_admission_paths": free,
            "excluded_dated_signal_observations": excluded,
            "evidence_limits": "Activity signal only. Paid attendance, annual volume, movable share and fee contribution remain unknown.",
        },
        "public_rule_trace": [
            {"rule": "Q1", "result": identity, "field": "identity_status"},
            {"rule": "Q2", "result": scope, "field": "public_evidence.us_scope"},
            {"rule": "Q3", "positive_price_observations": len(positive), "excluded_free_or_non_admission": len(free)},
            {"rule": "Q4", "fresh": fresh, "checked_on": ev.get("checked_on")},
            {"rule": "Q5", "result": "Dependencies and provider roles retained; authority unknown"},
            {"rule": "C1", "result": signal, "basis": "Distinct dated paid offers; price amount and CRM context excluded"},
        ],
        "public_sources": sources,
    }


def action_assessment(public, crm, clock, rules):
    crm = crm or {}
    events = sorted(crm.get("interactions", []), key=lambda e: (timestamp(e["occurred_at"]), e["interaction_id"]))
    callback = crm.get("callback") or {}
    open_callback = callback.get("status") == "open"
    due = timestamp(callback["due_at"]) if open_callback else None
    request_channel = callback.get("channel") if open_callback else None
    channel_block = crm.get("restriction_state") == "channel_restricted" and request_channel in crm.get("restricted_channels", [])
    # A later reply/callback request can reopen a simple not-interested disposition,
    # but never remove a recorded global or channel restriction.
    latest_interest = next((e for e in reversed(events) if e["outcome"] in
                            {"not_interested", "positive_reply", "callback_requested"}), None)
    declined = bool(latest_interest and latest_interest["outcome"] == "not_interested")
    if declined:
        due = None
    replies = [e for e in events if e["outcome"] == "positive_reply" and
               clock - timestamp(e["occurred_at"]) <= timedelta(days=rules["reply_window_days"])]
    attempts = [e for e in events if e["channel"] in {"phone", "email"} and
                e["outcome"] in {"no_answer", "message_left"}]
    last = timestamp(attempts[-1]["occurred_at"]) if attempts else None
    recent = [timestamp(e["occurred_at"]) for e in attempts if
              clock - timestamp(e["occurred_at"]) < timedelta(days=rules["attempt_window_days"])]
    cooldown = last is not None and clock < last + timedelta(days=rules["cooldown_days"])
    limit = len(recent) >= rules["attempt_limit"]
    defer = timestamp(crm["defer_until"]) if crm.get("defer_until") else None
    unknown = (crm.get("simulated_match_state", "unknown") not in {"matched", "no_existing_match"}
               or crm.get("simulated_relationship", "unknown") == "unknown"
               or crm.get("restriction_state", "unknown") == "unknown")
    conditions = [
        ("A1", crm.get("restriction_state") == "global_do_not_contact" or channel_block,
         "restricted", "blocked", "Resolve recorded restriction internally; do not contact or substitute another channel", "restriction_state;restricted_channels;callback.channel"),
        ("A2", crm.get("simulated_relationship") in {"customer", "open_opportunity"},
         "existing_relationship", "owner_review", "Route internally to the existing relationship owner; exclude from net-new work", "simulated_relationship;owner_id"),
        ("A3", unknown,
         "urgent_clearance_review" if due and due <= clock else "internal_clearance",
         "urgent_internal_check" if due and due <= clock else "research",
         "Resolve identity, relationship and contact restrictions internally before any follow-up decision", "simulated_match_state;simulated_relationship;restriction_state"),
        ("A4", bool((due and due > clock) or (defer and defer > clock)),
         "deferred", "wait", "Wait until the recorded callback/defer date, then review", "callback.due_at;defer_until"),
        ("A5", bool(due and due <= clock),
         "callback_review", "urgent_internal_review", "Review requested callback internally, preserve requested channel/time and refresh missing evidence", "callback;owner_id"),
        ("A6", bool(replies) and not declined,
         "response_review", "internal_response_review", "Review outstanding positive reply internally and verify the request before drafting a response", "interactions"),
        ("A7", declined or cooldown or limit,
         "contact_policy_review" if declined else "deferred", "hold" if declined else "wait",
         "Hold for internal review of the recorded lack of interest" if declined else "Wait for the demo cooldown/attempt window to expire; review before further contact", "interactions"),
        ("A8", bool(public["qualification"]["unresolved_checks"]),
         "evidence_research", "research", public["qualification"]["unresolved_checks"][0]["action"] if public["qualification"]["unresolved_checks"] else "",
         "qualification.unresolved_checks"),
        ("A9", True, "qualification_review", "qualification", "Review annual paid-ticket volume first, then ticketing control, dependency fit and switching reason", "commercial_potential;qualification.dependencies"),
    ]
    trace = []
    for rule, triggered, queue, urgency, action, fields in conditions:
        trace.append({"rule": rule, "triggered": bool(triggered), "fields": fields.split(";")})
        if triggered:
            break
    reopen = None
    if rule == "A4":
        reopen = max(t for t in (due, defer) if t is not None).isoformat()
    elif rule == "A7" and not declined:
        waits = []
        if cooldown:
            waits.append(last + timedelta(days=rules["cooldown_days"]))
        if limit:
            waits.append(recent[-rules["attempt_limit"]] + timedelta(days=rules["attempt_window_days"]))
        reopen = max(waits).isoformat()
    return {
        "queue": queue, "urgency": urgency, "matched_rule": rule,
        "recommended_action": action, "rule_trace": trace,
        "callback_due_at": due.isoformat() if due else None,
        "oldest_recent_reply_at": timestamp(replies[0]["occurred_at"]).isoformat() if replies else None,
        "reopen_at": reopen, "existing_owner_id": crm.get("owner_id"),
        "needs_human_review": True, "eligible_for_live_export": False,
        "outreach_authorized": False, "real_crm_clearance": "not_available",
    }


def sort_key(row):
    action = row["action"]
    timing = ""
    if action["queue"] in {"callback_review", "urgent_clearance_review"}:
        timing = action["callback_due_at"] or ""
    elif action["queue"] == "response_review":
        timing = action["oldest_recent_reply_at"] or ""
    # Epoch values handle equivalent timestamps with different UTC offsets.
    time_order = timestamp(timing).timestamp() if timing else 0
    return (time_order, SIGNALS.index(row["commercial_potential"]["activity_signal"]),
            len(row["qualification"]["unresolved_checks"]), row["account_id"])


def build_queue(accounts, crm_by_id, clock, rules, scenario_id):
    results = []
    for account in sorted(accounts, key=lambda a: a["account_id"]):
        public = public_assessment(account, clock, rules)
        action = action_assessment(public, crm_by_id.get(account["account_id"]), clock, rules)
        results.append({
            "account_id": account["account_id"], "organisation_name": account["organisation_name"],
            "canonical_domain": account["canonical_domain"],
            "synthetic": True, "scenario_id": scenario_id,
            "context_status": "simulated" if account["account_id"] in crm_by_id else "missing_or_invalid_treated_unknown",
            **public, "action": action, "queue_rank": None,
            "recommendation_key": account["account_id"] + ":" + action["queue"],
            "provenance": {"input_row": account.get("input_row"), "source_record_ids": account["source_record_ids"],
                           "public_field_prefix": "organisers[account_id=" + account["account_id"] + "]",
                           "original_research_urls_unverified": account.get("source_urls", [])},
        })
    groups = defaultdict(list)
    for row in results:
        groups[row["action"]["queue"]].append(row)
    ordered = []
    for queue in ACTIVE_QUEUES + OTHER_QUEUES:
        items = sorted(groups[queue], key=sort_key)
        for rank, row in enumerate(items, 1):
            row["queue_rank"] = rank if queue in ACTIVE_QUEUES else None
            row["sort_basis"] = {
                "queue": queue, "timing": (row["action"]["callback_due_at"] if queue in {"callback_review", "urgent_clearance_review"}
                                            else row["action"]["oldest_recent_reply_at"] if queue == "response_review" else None),
                "activity_signal": row["commercial_potential"]["activity_signal"],
                "unresolved_public_check_count": len(row["qualification"]["unresolved_checks"]),
                "stable_tiebreaker": row["account_id"],
            }
            ordered.append(row)
    return {
        "metadata": {
            "rules_version": rules["rules_version"], "as_of": clock.isoformat(),
            "scenario_id": scenario_id, "synthetic": True,
            "label": "SIMULATED CRM CONTEXT. REAL PUBLIC IDENTITIES. INTERNAL REVIEW ONLY.",
            "account_count": len(ordered),
            "queue_counts": dict(sorted(Counter(r["action"]["queue"] for r in ordered).items())),
            "activity_signal_counts": dict(sorted(Counter(r["commercial_potential"]["activity_signal"] for r in ordered).items())),
            "unverified_count": sum(r["qualification"]["verification_status"] == "not_reviewed" for r in ordered),
            "sorting": "Separate queues. Timing first for callbacks/replies, then activity signal, fewer unresolved checks, stable ID. No global call rank.",
            "limitations": "Public activity signals are not annual revenue estimates. Simulated history does not authorize contact or live export. No allocation or handoff is implemented.",
            "editable_assumptions": rules,
        },
        "accounts": ordered,
    }
