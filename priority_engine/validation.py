"""Small boundary checks. Bad evidence is reported, never converted into facts."""
from copy import deepcopy
from datetime import date, datetime
from math import isfinite
from urllib.parse import urlsplit

DECISION_INPUTS = {"priority", "score", "commercial_potential", "recommended_action",
                   "predetermined_rank", "queue_rank", "activity_signal"}


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include a UTC offset")
    return parsed


def has_decision_inputs(value):
    if isinstance(value, dict):
        return bool(DECISION_INPUTS.intersection(value)) or any(
            has_decision_inputs(v) for v in value.values())
    if isinstance(value, list):
        return any(has_decision_inputs(v) for v in value)
    return False


def url_ok(value):
    try:
        p = urlsplit(value)
        return p.scheme in {"http", "https"} and bool(p.hostname)
    except (ValueError, TypeError, AttributeError):
        return False


def offer_error(offer):
    if not isinstance(offer, dict):
        return "Offer must be an object"
    try:
        for field in ("price_min", "price_max"):
            value = offer[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
                return "Price must be finite and nonnegative"
        if offer["price_max"] < offer["price_min"]:
            return "Price range is reversed"
        if offer["currency"] != "USD":
            return "Only USD price observations are supported in version 1"
        date.fromisoformat(offer["observed_on"])
        if offer.get("event_start_date"):
            date.fromisoformat(offer["event_start_date"])
        if not url_ok(offer.get("source_url")):
            return "Missing or invalid source URL"
        if not all(isinstance(offer.get(k), str) and offer[k] for k in
                   ("name", "unit", "price_basis", "availability", "access_basis")):
            return "Missing typed offer context"
    except (KeyError, ValueError, TypeError):
        return "Missing or invalid price/date context"
    return None


def validate_public(document):
    """Keep valid accounts; quarantine bad observations with their original paths."""
    rows = document.get("organisers") if isinstance(document, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Public input needs an organisers array")
    ids = [r.get("account_id") for r in rows if isinstance(r, dict) and isinstance(r.get("account_id"), str)]
    duplicates = {v for v in ids if ids.count(v) > 1}
    accepted, issues = [], []
    for index, raw in enumerate(rows):
        aid = raw.get("account_id") if isinstance(raw, dict) else None
        base = {"account_id": aid, "path": f"organisers[{index}]"}
        error = None
        if not isinstance(raw, dict) or not isinstance(aid, str) or not aid:
            error = "Missing stable identity"
        elif aid in duplicates:
            error = "Duplicate account ID; every conflicting row rejected"
        elif has_decision_inputs(raw):
            error = "Predetermined decision fields are forbidden"
        elif not isinstance(raw.get("organisation_name"), str) or not raw["organisation_name"].strip():
            error = "Missing organisation name"
        elif not url_ok(raw.get("website")) or not raw.get("canonical_domain"):
            error = "Missing valid canonical website/domain"
        elif urlsplit(raw["website"]).hostname.lower().removeprefix("www.") != raw["canonical_domain"]:
            error = "Website and canonical domain disagree"
        elif (not isinstance(raw.get("source_record_ids"), list) or not raw["source_record_ids"]
              or not all(isinstance(v, str) and v for v in raw["source_record_ids"])):
            error = "Missing source provenance"
        if error:
            issues.append({**base, "severity": "rejected_account", "reason": error})
            continue
        row = deepcopy(raw)
        row["validation_issues"] = []
        ev = row.get("public_evidence")
        if ev is not None:
            try:
                valid = (isinstance(ev, dict) and ev.get("account_id") == aid
                         and row.get("verification_status") == "field_reviewed"
                         and ev.get("verification_status") == "field_reviewed"
                         and isinstance(ev.get("offer_observations"), list)
                         and isinstance(ev.get("source_urls"), list)
                         and bool(ev["source_urls"])
                         and all(url_ok(u) for u in ev["source_urls"]))
                date.fromisoformat(ev["checked_on"])
            except (KeyError, TypeError, ValueError):
                valid = False
            if not valid:
                issue = {**base, "severity": "quarantined_evidence", "reason": "Invalid review structure; identity retained for research"}
                issues.append(issue)
                row["validation_issues"].append(issue)
                row["public_evidence"] = None
                row["verification_status"] = "not_reviewed"
            else:
                valid_offers = []
                for n, offer in enumerate(ev["offer_observations"]):
                    reason = offer_error(offer)
                    if reason:
                        issue = {**base, "path": f"{base['path']}.public_evidence.offer_observations[{n}]",
                                 "severity": "quarantined_offer", "reason": reason}
                        issues.append(issue)
                        row["validation_issues"].append(issue)
                    else:
                        valid_offers.append({**offer, "input_offer_index": n})
                ev["offer_observations"] = valid_offers
        accepted.append(row)
    return accepted, issues


def validate_crm(document, valid_ids, clock):
    if (not isinstance(document, dict) or not isinstance(document.get("metadata"), dict)
            or document["metadata"].get("synthetic") is not True):
        raise ValueError("CRM fixtures must declare synthetic=true")
    rows = document.get("accounts")
    if not isinstance(rows, list):
        raise ValueError("CRM fixtures need an accounts array")
    ids = [r.get("account_id") for r in rows if isinstance(r, dict) and isinstance(r.get("account_id"), str)]
    duplicates = {v for v in ids if ids.count(v) > 1}
    accepted, issues = {}, []
    enums = {
        "simulated_match_state": {"unknown", "matched", "no_existing_match", "ambiguous"},
        "simulated_relationship": {"unknown", "prospect", "customer", "open_opportunity"},
        "restriction_state": {"unknown", "no_restriction_in_scenario", "global_do_not_contact", "channel_restricted"},
    }
    outcomes = {"no_answer", "message_left", "positive_reply", "not_interested", "callback_requested", "restriction_recorded", "internal_review"}
    for n, row in enumerate(rows):
        aid = row.get("account_id") if isinstance(row, dict) else None
        error = None
        try:
            if not isinstance(row, dict) or aid not in valid_ids or aid in duplicates:
                raise ValueError("Unknown or duplicate account join")
            if row.get("synthetic") is not True or not row.get("scenario_id") or has_decision_inputs(row):
                raise ValueError("Missing synthetic label/scenario or forbidden decision input")
            for field, allowed in enums.items():
                if row.get(field) not in allowed:
                    raise ValueError("Missing or invalid CRM state: " + field)
            if row.get("owner_id") is not None and row["owner_id"] not in {"SIM_REP_" + c for c in "ABCDE"}:
                raise ValueError("Owner must be null or a fictional rep ID")
            if not isinstance(row.get("restricted_channels"), list) or not set(row["restricted_channels"]) <= {"phone", "email"}:
                raise ValueError("Invalid restricted channels")
            if row["restriction_state"] == "channel_restricted" and not row["restricted_channels"]:
                raise ValueError("Channel restriction needs a channel")
            interactions = row["interactions"]
            if not isinstance(interactions, list):
                raise ValueError("Interactions must be an array")
            interaction_ids = [x["interaction_id"] for x in interactions]
            if len(interaction_ids) != len(set(interaction_ids)):
                raise ValueError("Duplicate interaction ID")
            for event in interactions:
                if (event.get("synthetic") is not True or event.get("channel") not in {"phone", "email", "internal"}
                        or event.get("outcome") not in outcomes or timestamp(event["occurred_at"]) > clock):
                    raise ValueError("Invalid or future interaction")
            callback = row.get("callback")
            if callback:
                source = next((x for x in interactions if x["interaction_id"] == callback["source_interaction_id"]), None)
                if (callback.get("synthetic") is not True or callback.get("channel") not in {"phone", "email"}
                        or callback.get("status") not in {"open", "completed", "cancelled"}
                        or not source or source["outcome"] != "callback_requested"
                        or timestamp(callback["requested_at"]) != timestamp(source["occurred_at"])
                        or timestamp(callback["due_at"]) < timestamp(callback["requested_at"])):
                    raise ValueError("Callback lacks a valid linked request")
            if row.get("defer_until"):
                timestamp(row["defer_until"])
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            error = str(exc)
        if error:
            issues.append({"account_id": aid, "path": f"accounts[{n}]", "severity": "rejected_crm_context",
                           "reason": error + "; use unknown CRM state"})
        else:
            accepted[aid] = deepcopy(row)
    return accepted, issues
