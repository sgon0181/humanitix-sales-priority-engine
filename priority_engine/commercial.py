"""Explicitly simulated commercial assumptions, separate from public evidence and CRM."""
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path

FIELDS = ["simulated_annual_paid_events", "simulated_paid_tickets_per_event",
          "simulated_average_ticket_price_usd", "simulated_switching_difficulty"]
SETTINGS_PATH = Path(__file__).with_name("commercial_settings.json")
SWITCHING_ORDER = {"low": 0, "medium": 1, "high": 2, "unknown": 3}
LABELS = dict(zip(FIELDS, ["annual paid events", "paid tickets per event", "average ticket price", "switching difficulty"]))


def load_settings(settings=None):
    """Read editable demonstration assumptions; never default a missing fee to zero."""
    raw = json.loads(SETTINGS_PATH.read_text()) if settings is None else settings
    if not isinstance(raw, dict) or not isinstance(raw.get("gmv_thresholds_usd"), dict):
        raise ValueError("Commercial settings require explicit GMV demonstration thresholds")
    def number(value, name, optional=False):
        if optional and (value is None or value == ""):
            return None
        try:
            parsed = Decimal(str(value))
            if not parsed.is_finite() or parsed < 0 or parsed != parsed.quantize(Decimal("0.01")):
                raise ValueError()
            return int(parsed) if parsed == parsed.to_integral_value() else float(parsed)
        except (InvalidOperation, ValueError, OverflowError):
            raise ValueError("Commercial setting must be a finite nonnegative number with at most two decimals: " + name)
    high = number(raw["gmv_thresholds_usd"].get("High"), "High")
    medium = number(raw["gmv_thresholds_usd"].get("Medium"), "Medium")
    if high <= medium:
        raise ValueError("High GMV threshold must exceed Medium")
    return {"fee_percentage": number(raw.get("fee_percentage"), "fee_percentage", True),
            "fee_per_paid_ticket_usd": number(raw.get("fee_per_paid_ticket_usd"), "fee_per_paid_ticket_usd", True),
            "gmv_thresholds_usd": {"High": high, "Medium": medium},
            "label": "Unvalidated demonstration assumptions, not verified Humanitix pricing or commercial cutoffs"}


def assess(row, settings=None):
    settings = load_settings(settings)
    thresholds = settings["gmv_thresholds_usd"]
    values, missing, invalid = {}, [], []
    for field in FIELDS[:3]:
        raw = row.get(field)
        text = "" if raw is None else str(raw).strip()
        value = None
        if not text:
            missing.append(field)
        else:
            try:
                number = Decimal(text)
                if not number.is_finite() or number < 0:
                    raise ValueError("Nonnegative finite numbers required")
                if field != FIELDS[2] and number != number.to_integral_value():
                    raise ValueError("Event and ticket counts must be whole numbers")
                if number != number.quantize(Decimal("0.01")):
                    raise ValueError("Prices may contain at most two decimal places")
                value = int(number) if number == number.to_integral_value() else float(number)
            except (InvalidOperation, ValueError, OverflowError):
                invalid.append(field)
        values[field] = value
    difficulty = str(row.get(FIELDS[3]) or "unknown").strip().lower()
    if difficulty not in SWITCHING_ORDER:
        invalid.append(FIELDS[3])
        difficulty = "unknown"
    tickets, potential = None, None
    events, per_event, price = [values[f] for f in FIELDS[:3]]
    if events is not None and per_event is not None:
        tickets = events * per_event
        if price is not None:
            try:
                amount = (Decimal(tickets) * Decimal(str(price))).quantize(Decimal("0.01"))
                potential = int(amount) if amount == amount.to_integral_value() else float(amount)
            except (InvalidOperation, OverflowError):
                invalid.append("annual_ticketing_potential_usd")
    opportunity = ("Unknown" if potential is None else "High" if potential >= thresholds["High"]
                   else "Medium" if potential >= thresholds["Medium"] else "Low")
    annual_fees = None
    percentage, fixed_fee = settings["fee_percentage"], settings["fee_per_paid_ticket_usd"]
    if potential is not None and percentage is not None and fixed_fee is not None:
        try:
            fees = (Decimal(tickets) * (Decimal(str(price)) * Decimal(str(percentage)) / 100
                                       + Decimal(str(fixed_fee)))).quantize(Decimal("0.01"))
            annual_fees = int(fees) if fees == fees.to_integral_value() else float(fees)
        except (InvalidOperation, OverflowError):
            invalid.append("illustrative_annual_gross_fee_revenue_usd")
    fee_terms = (f"Illustrative fee: {percentage}% + USD {fixed_fee} per paid ticket"
                 if percentage is not None and fixed_fee is not None else "Illustrative fee: Unknown, percentage or per-ticket assumption missing")
    formula = (f"{events} paid events/year x {per_event} paid tickets/event x USD {price} average ticket"
               if potential is not None else "Unknown: missing or invalid " + ", ".join(LABELS.get(f, f) for f in missing + invalid))
    return {"origin": "synthetic" if any(row.get(f) is not None and str(row[f]).strip() for f in FIELDS) else "not_provided",
            "label": "SIMULATED COMMERCIAL ASSUMPTIONS. Not real organiser sales or Humanitix revenue.",
            "inputs": {**values, FIELDS[3]: difficulty}, "annual_paid_tickets": tickets,
            "annual_ticketing_potential_usd": potential, "opportunity": opportunity,
            "annual_ticketing_gmv_usd": potential,
            "illustrative_annual_gross_fee_revenue_usd": annual_fees,
            "fee_assumptions": {"percentage": percentage, "per_paid_ticket_usd": fixed_fee},
            "fee_assumptions_text": fee_terms,
            "fee_formula": "Annual paid tickets x (average USD ticket price x fee percentage / 100 + USD fee per paid ticket)",
            "fee_limitations": "Illustrative gross fee revenue, not profit or verified Humanitix revenue. Processing costs are not deducted.",
            "switching_difficulty": difficulty, "assumptions": formula + "; switching difficulty: " + difficulty,
            "missing_fields": missing, "invalid_fields": invalid, "thresholds_usd": dict(thresholds),
            "threshold_basis": "annual_ticketing_gmv_usd", "thresholds_label": settings["label"],
            "meaning": "Gross annual ticket value under supplied assumptions, not fees, profit or probability of switching."}


def ordering(assessment):
    """Only break ties inside the existing action category and relevant timing."""
    assessment = assessment or {}
    value = assessment.get("illustrative_annual_gross_fee_revenue_usd")
    return (value is None, -value if value is not None else 0,
            SWITCHING_ORDER.get(assessment.get("switching_difficulty"), 3))


def attach(core, assessments):
    """Attach the separate simulation without changing the public activity assessment."""
    by_id = {a["account_id"]: a for a in assessments}
    for account in core["accounts"]:
        if account["account_id"] in by_id:
            account["simulated_commercial"] = by_id[account["account_id"]]
