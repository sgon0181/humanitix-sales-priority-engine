"""Local presentation adapter. All decisions come from the existing CSV pipeline."""
import argparse
import csv
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
import uuid
import zipfile

from .__main__ import ROOT
from .from_csv import clean, execute
from .commercial import assess as assess_commercial
from .validation import timestamp

MAX_UPLOAD = 10 * 1024 * 1024
LIMITATION = ("This ranks work, not expected revenue. GMV and illustrative gross fee revenue use simulated inputs and unvalidated, "
              "configurable demonstration assumptions. Fees are not profit or verified Humanitix revenue; processing costs are not deducted. Missing numerical inputs produce Unknown. "
              "Actual annual paid volume remains unverified, and high potential never overrides contact restrictions or cooldowns.")
BANDS = {"due_commitment": "Due follow-up", "recent_reply": "Recent reply", "qualification": "Qualification",
         "evidence_research": "Research needed", "clearance": "CRM check", "policy_review": "Policy review",
         "waiting": "Deferred", "existing_relationship": "Existing relationship"}
SIGNALS = {"repeated_dated_paid_offers": "Repeated dated paid offers",
           "one_dated_paid_offer": "One dated paid offer",
           "undated_or_historical_paid_evidence": "Historical or undated paid evidence",
           "paid_activity_unestablished": "Paid activity not established"}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def display_date(value):
    """Render the engine's recorded instant, with an explicit timezone."""
    if not value:
        return "an unrecorded time"
    date = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    return date.strftime("%d %b %Y, %H:%M UTC").lstrip("0")


def explain_action(item, context, action):
    """Translate the existing decision into one task and its actual trigger."""
    queue = item["underlying_queue"]
    events = sorted(context.get("interactions", []), key=lambda event: (timestamp(event["occurred_at"]), event["interaction_id"]))
    callback = context.get("callback") or {}
    if queue == "restricted":
        restriction = ("A global do-not-contact restriction is recorded."
                       if context.get("restriction_state") == "global_do_not_contact" else
                       "The requested callback channel (" + str(callback.get("channel", "unknown")) + ") is restricted.")
        if action.get("callback_due_at"):
            return ("Review the callback/restriction conflict internally; do not contact",
                    restriction + " A callback is also recorded for " + display_date(action["callback_due_at"]) + ".")
        return "Keep contact blocked; do not contact", restriction
    if queue == "existing_relationship":
        task = ("Route to the existing relationship owner" if item.get("existing_owner_id") else
                "Identify the existing relationship owner")
        relationship = "customer" if context.get("simulated_relationship") == "customer" else "open opportunity"
        return task, "CRM records an existing " + relationship + "; this is outside new-host acquisition."
    if queue in {"internal_clearance", "urgent_clearance_review"}:
        checks = []
        if context.get("simulated_match_state", "unknown") not in {"matched", "no_existing_match"}:
            checks.append("identity match")
        if context.get("simulated_relationship", "unknown") == "unknown":
            checks.append("relationship status")
        if context.get("restriction_state", "unknown") == "unknown":
            checks.append("contact restrictions")
        reason = "CRM clearance is incomplete: " + ", ".join(checks) + "."
        if queue == "urgent_clearance_review":
            reason += " A callback was due " + display_date(action.get("callback_due_at")) + "."
        return "Resolve CRM clearance before preparing contact", reason
    if queue == "deferred":
        until = display_date(action.get("reopen_at"))
        if action["matched_rule"] == "A4":
            conditions = []
            if action.get("callback_due_at"):
                conditions.append("Callback scheduled for " + display_date(action["callback_due_at"]))
            if context.get("defer_until"):
                conditions.append("CRM defer date: " + display_date(context["defer_until"]))
            reason = "; ".join(conditions) + "."
        else:
            attempts = [e for e in events if e["outcome"] in {"no_answer", "message_left"} and e["channel"] in {"phone", "email"}]
            reason = "The contact cooldown/attempt policy applies until " + until + "."
            if attempts:
                reason = "Last outreach: " + display_date(attempts[-1]["occurred_at"]) + ". " + reason
        return "Wait until " + until + ", then review", reason
    if queue == "callback_review":
        task = "Prepare the requested " + str(callback.get("channel", "")) + " callback; do not contact"
        reason = "A requested callback was due " + display_date(action.get("callback_due_at")) + "."
        if not item["qualification"]["identity_observed"]:
            task = "Verify organiser identity for the requested " + str(callback.get("channel", "")) + " callback; do not contact"
            reason += " Organiser identity still needs verification."
        return task, reason
    if queue == "response_review":
        task = ("Review the positive reply before drafting a response; do not send" if item["qualification"]["identity_observed"] else
                "Verify organiser identity before drafting a response; do not send")
        reason = "An outstanding positive reply was recorded " + display_date(action.get("oldest_recent_reply_at")) + "."
        if not item["qualification"]["identity_observed"]:
            reason += " Organiser identity still needs verification."
        return task, reason
    if queue == "contact_policy_review":
        declined = next((event for event in reversed(events) if event["outcome"] == "not_interested"), None)
        return ("Keep acquisition on hold for internal review",
                "The latest recorded interest decision was not interested" +
                (" on " + display_date(declined["occurred_at"]) if declined else "") + ".")
    if queue == "evidence_research":
        check = item["qualification"]["unresolved_checks"][0]
        tasks = {
            "identity_status": ("Verify organiser identity on its primary website", "organiser identity has not been source-verified"),
            "public_evidence.us_scope": ("Confirm the organiser runs US events", "US event or audience scope is unconfirmed"),
            "public_evidence.offer_observations": ("Verify a current dated paid event", "a current paid event has not been established"),
            "public_evidence.checked_on": ("Refresh the dated primary-source evidence", "the public evidence needs a fresh source check"),
            "validation_issues": ("Resolve conflicting public evidence", "some public fields failed validation"),
        }
        task, reason = tasks.get(check["field"], (check["action"], "a required qualification check is unresolved"))
        return task, "Evidence needed: " + reason + ". Simulated commercial values do not verify public facts."
    return ("Confirm annual paid-ticket volume and the ticketing decision-maker",
            "Current US paid-event evidence is supported; actual annual sales, ticketing control and switching intent remain unknown.")


def present(results, run_id):
    """Project existing ranked rows into a smaller view, without reordering them."""
    plan = read_json(results / "workload_proposal.json")
    contexts = {c["account_id"]: c for c in read_json(results / "imported_crm.json")["accounts"]}
    public = {a["account_id"]: a for a in read_json(results / "imported_public.json")["organisers"]}
    actions = {a["account_id"]: a["action"] for a in read_json(results / "core_queue.json")["accounts"]}
    summary = read_json(results / "import_summary.json")
    validation = read_json(results / "validation_report.json")
    settings = read_json(results / "simulated_commercial.json").get("metadata", {}).get("settings")
    rows = []
    for item in plan["work"]:
        context = contexts.get(item["account_id"], {})
        potential = item["commercial_potential"]
        qualification = item["qualification"]
        record = public.get(item["account_id"], {})
        origin = context.get("source_origin", "unavailable")
        crm_label = {"synthetic": "Simulated CRM", "provided_unverified": "Provided CRM, unverified"}.get(origin, "CRM unavailable")
        crm = []
        if context.get("callback"):
            crm.append("Callback due " + display_date(context["callback"]["due_at"]))
        if context.get("restriction_state") not in {None, "no_restriction_in_scenario"}:
            crm.append("Restriction: " + context["restriction_state"].replace("_", " "))
        if context.get("defer_until"):
            crm.append("Deferred until " + display_date(context["defer_until"]))
        if context.get("simulated_relationship") in {"customer", "existing_customer"}:
            crm.append("Existing customer")
        for event in context.get("interactions", [])[-1:]:
            if event.get("outcome") != "callback_requested":
                crm.append(event["outcome"].replace("_", " ") + " on " + display_date(event["occurred_at"]))
        band = item["priority_band"]
        next_action, explanation = explain_action(item, context, actions[item["account_id"]])
        priority = BANDS.get(band, band.replace("_", " "))
        if item["underlying_queue"] == "restricted":
            priority = "Contact restricted"
        elif item["underlying_queue"] == "urgent_clearance_review":
            priority = "Due CRM check"
        review = item.get("review") or {}
        if review.get("decision") == "hold":
            priority = "On hold"
            next_action = "Keep on hold until the recorded condition is reviewed"
            explanation = "Hold reason: " + review["reason"] + ". Reopen condition: " + review["reopen_condition"]
        elif review.get("decision") == "evidence_needed" and item.get("review_state") == "evidence_needed":
            next_action = "Research: " + review["requested_evidence"]
            explanation = "Evidence requested by the reviewer: " + review["reason"] + "."
        evidence = SIGNALS[potential["activity_signal"]]
        commercial = item.get("simulated_commercial") or assess_commercial({}, settings)
        rows.append({
            "rank": item["unified_rank"], "account_id": item["account_id"],
            "account": item["organisation_name"], "website": record.get("website", ""),
            "priority": priority, "priority_band": band, "classification": item["underlying_queue"],
            "evidence": evidence, "reviewed": qualification["verification_status"] == "field_reviewed",
            "paid_dates": potential["distinct_paid_start_dates"],
            "offers": [{"name": o["name"], "date": o["start"], "source": o["source_url"]}
                       for o in potential["supporting_offers"]],
            "sources": item["public_sources"], "next_action": next_action,
            "explanation": explanation, "crm_label": crm_label,
            "crm_context": "; ".join(crm) or "No actionable history recorded; clearance is still required.",
            "annual_volume": "Unknown", "simulated_commercial": commercial,
            "work_type": "Sales preparation" if item["work_kind"] == "sales_preparation" else "Internal review / research",
        })
    return clean({"run_id": run_id, "as_of": read_json(results / "imported_crm.json")["metadata"]["scenario_clock"],
                  "rows": rows, "input_rows": summary["input_rows"], "reviewed_count": sum(r["reviewed"] for r in rows),
                  "task_drafts": summary["proposed_handoff_bundles"], "limitation": LIMITATION,
                  "issues": validation["input_issues"],
                  "download_results": f"/download/{run_id}/results.csv",
                  "download_handoff": f"/download/{run_id}/handoff.zip"})


def safe_cell(value):
    """Keep a supplied name or explanation from becoming a spreadsheet formula."""
    value = str(value)
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r")) else value


def results_csv(payload):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["Work order", "Account ID", "Account", "Priority", "Commercial evidence", "Public evidence status",
                     "Annual paid-ticket volume", "Next action", "Explanation", "CRM origin", "CRM context", "Source URLs",
                     "Commercial origin", "Commercial opportunity", "Simulated annual paid tickets", "Simulated annual ticketing potential USD",
                     "Simulated switching difficulty", "Commercial assumptions", "Illustrative annual gross fee revenue USD",
                     "Illustrative fee assumptions", "Fee limitations", "GMV threshold assumptions"])
    for r in payload["rows"]:
        writer.writerow([safe_cell(v) for v in [r["rank"], r["account_id"], r["account"], r["priority"], r["evidence"],
                         "Supplied field review" if r["reviewed"] else "Unreviewed claims", r["annual_volume"],
                         r["next_action"], r["explanation"], r["crm_label"], r["crm_context"], "; ".join(r["sources"]),
                         r["simulated_commercial"]["origin"], r["simulated_commercial"]["opportunity"],
                         r["simulated_commercial"]["annual_paid_tickets"] if r["simulated_commercial"]["annual_paid_tickets"] is not None else "Unknown",
                         r["simulated_commercial"]["annual_ticketing_potential_usd"] if r["simulated_commercial"]["annual_ticketing_potential_usd"] is not None else "Unknown",
                         r["simulated_commercial"]["switching_difficulty"], r["simulated_commercial"]["assumptions"],
                         r["simulated_commercial"]["illustrative_annual_gross_fee_revenue_usd"] if r["simulated_commercial"]["illustrative_annual_gross_fee_revenue_usd"] is not None else "Unknown",
                         r["simulated_commercial"]["fee_assumptions_text"], r["simulated_commercial"]["fee_limitations"],
                         json.dumps({"basis": "GMV", "thresholds_usd": r["simulated_commercial"]["thresholds_usd"], "label": r["simulated_commercial"]["thresholds_label"]})]])
    return stream.getvalue().encode("utf-8-sig")


def handoff_zip(results):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ["companies_PROPOSED.csv", "notes_PROPOSED.csv", "tasks_PROPOSED.csv"]:
            # Preserve the existing handoff fields while making CSV cells safe to open.
            rows = csv.reader(io.StringIO((results / name).read_text(encoding="utf-8-sig")))
            output = io.StringIO(newline="")
            writer = csv.writer(output)
            writer.writerows([[safe_cell(cell) for cell in row] for row in rows])
            archive.writestr(name, output.getvalue().encode("utf-8-sig"))
        archive.write(results / "HANDOFF_MAPPING.md", "HANDOFF_MAPPING.md")
        archive.writestr("READ_FIRST.txt", "REVIEW DRAFT ONLY. Do not import directly.\n"
                         "Company, note and task suggestions from the existing workflow. Not every ranked account has a task draft.\n"
                         "CRM origins are labelled in notes. No live HubSpot connection or outreach.\n"
                         "Resolve real owner/association IDs, human review and field mapping before use.\n")
    return stream.getvalue()


def make_server(port=8765, runs=None):
    run_root = Path(runs) if runs else ROOT / "outputs/demo_runs"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, code, body, kind="application/json; charset=utf-8", filename=None):
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            if filename:
                self.send_header("Content-Disposition", 'attachment; filename="' + filename + '"')
            self.end_headers()
            self.wfile.write(body)

        def error(self, message, code=400):
            self.send(code, json.dumps({"error": clean(message)}).encode())

        def do_GET(self):
            path = urlsplit(self.path).path
            static = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                      "/style.css": ("style.css", "text/css; charset=utf-8"),
                      "/slides.js": ("slides.js", "text/javascript; charset=utf-8"),
                      "/slides.css": ("slides.css", "text/css; charset=utf-8"),
                      "/fonts/montserrat-latin.woff2": ("fonts/montserrat-latin.woff2", "font/woff2")}
            if path in static:
                name, kind = static[path]
                return self.send(200, (ROOT / "demo" / name).read_bytes(), kind)
            if path in {"/example.csv", "/template.csv"}:
                example = "new_organisers.csv" if path == "/example.csv" else "organisers_template.csv"
                return self.send(200, (ROOT / "data/csv_examples" / example).read_bytes(), "text/csv; charset=utf-8", example)
            match = re.fullmatch(r"/download/([a-f0-9]{32})/(results\.csv|handoff\.zip)", path)
            if match:
                run_id, name = match.groups()
                results = run_root / run_id / "results"
                if (results / "view.json").is_file():
                    if name == "results.csv":
                        return self.send(200, results_csv(read_json(results / "view.json")), "text/csv; charset=utf-8", "humanitix_ranked_accounts.csv")
                    return self.send(200, handoff_zip(results), "application/zip", "humanitix_handoff_REVIEW_DRAFT.zip")
            self.error("File not found", 404)

        def do_POST(self):
            if self.path not in {"/api/analyse", "/api/example"}:
                return self.error("Route not found", 404)
            # The interface needs no cross-origin access to this local process.
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + self.headers.get("Host", ""):
                return self.error("Use the local demo page to upload a CSV", 403)
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 0 or size > MAX_UPLOAD:
                    return self.error("Choose a CSV smaller than 10 MB", 413)
                body = self.rfile.read(size)
                if self.path == "/api/example":
                    body = (ROOT / "data/csv_examples/new_organisers.csv").read_bytes()
                if not body:
                    return self.error("Choose a CSV file first")
                run_id = uuid.uuid4().hex
                folder = run_root / run_id
                (folder / "input").mkdir(parents=True)
                path = folder / "input/upload.csv"
                path.write_bytes(body)
                results = folder / "results"
                execute(path, results, ROOT / "data/synthetic/capacity.json", ROOT / "priority_engine/rules.json",
                        ROOT / "priority_engine/workplan_rules.json")
                payload = present(results, run_id)
                (results / "view.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
                self.send(200, json.dumps(payload).encode())
            except (ValueError, TypeError, KeyError, OSError, AttributeError) as exc:
                self.error("Could not process CSV: " + str(exc))

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser(description="Local Humanitix CSV demo, no packages or credentials required")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = make_server(args.port)
    print(f"Humanitix demo: http://127.0.0.1:{server.server_port}", flush=True)
    print("Local only. Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
