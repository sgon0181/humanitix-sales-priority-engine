"""Exercise the real local HTTP upload/download path above the unchanged engine."""
import csv
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zipfile

from priority_engine.__main__ import ROOT
from priority_engine.demo_server import MAX_UPLOAD, make_server, results_csv


class DemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(dir=ROOT / "outputs")
        cls.runs = Path(cls.temp.name)
        cls.server = make_server(0, cls.runs)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        cls.example = cls.post("/api/example", b"")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    @classmethod
    def post(cls, route, body, headers=None):
        request = Request(cls.base + route, data=body, headers=headers or {"Content-Type": "text/csv"})
        with urlopen(request, timeout=20) as response:
            return json.load(response)

    def test_static_page_has_one_table_and_required_controls(self):
        with urlopen(self.base) as response:
            html = response.read().decode()
        self.assertEqual(html.count("<table>"), 1)
        self.assertNotIn("Try example data", html)
        self.assertNotIn('id="example"', html)
        for phrase in ["Upload CSV", "Download blank CSV template", "Download results CSV", "HubSpot draft", "revenue forecast"]:
            self.assertIn(phrase, html)
        self.assertNotIn("Download example CSV", html)
        self.assertNotIn("CSV format and data boundaries", html)
        self.assertIn("How prioritisation works", html)
        self.assertIn('class="logic-steps"', html)
        self.assertIn("Perplexity and Apollo", html)
        self.assertIn("Commercial and CRM inputs are simulated", html)

    def test_presentation_slides_are_served_before_the_demo(self):
        with urlopen(self.base) as response:
            html = response.read().decode()
        self.assertLess(html.index('id="deck"'), html.index('id="csv-drop-zone"'))
        self.assertEqual(html.count('class="slide"'), 3)
        for route in ["/slides.js", "/slides.css"]:
            with urlopen(self.base + route) as response:
                self.assertEqual(response.status, 200)

    def test_example_uses_existing_rank_and_no_capacity_in_view(self):
        payload = self.example
        plan = json.loads((self.runs / payload["run_id"] / "results/workload_proposal.json").read_text())
        self.assertEqual([r["account_id"] for r in payload["rows"]], [r["account_id"] for r in plan["work"]])
        self.assertEqual(len(payload["rows"]), 9)
        self.assertEqual(payload["rows"][0]["account_id"], "BATCH_02")
        for row in payload["rows"]:
            self.assertNotIn("estimated_minutes", row)
            self.assertNotIn("review_fingerprint", row)
            self.assertNotIn("proposed_rep_id", row)
            self.assertEqual(row["crm_label"], "Simulated CRM")
            self.assertEqual(row["annual_volume"], "Unknown")
        self.assertIn("not expected revenue", payload["limitation"])

    def test_restrictions_and_missing_evidence_are_explained(self):
        rows = {r["account_id"]: r for r in self.example["rows"]}
        self.assertEqual(rows["BATCH_03"]["priority"], "Contact restricted")
        self.assertIn("do not contact", rows["BATCH_03"]["next_action"])
        self.assertIn("global do not contact", rows["BATCH_03"]["crm_context"])
        self.assertFalse(rows["BATCH_NEW_09"]["reviewed"])
        self.assertIn("Verify organiser identity", rows["BATCH_NEW_09"]["next_action"])
        self.assertIn("Evidence needed", rows["BATCH_NEW_09"]["explanation"])

    def test_uploaded_csv_recomputes_changed_crm_without_code_edits(self):
        before = self.post("/api/analyse", (ROOT / "data/csv_examples/before_new_organisers.csv").read_bytes())
        after = self.post("/api/analyse", (ROOT / "data/csv_examples/new_organisers.csv").read_bytes())
        old = {r["account_id"]: r for r in before["rows"]}
        new = {r["account_id"]: r for r in after["rows"]}
        self.assertEqual(len(old), 8)
        self.assertEqual(len(new), 9)
        self.assertEqual(old["BATCH_02"]["rank"], 4)
        self.assertEqual(new["BATCH_02"]["rank"], 1)
        self.assertNotEqual(old["BATCH_03"]["next_action"], new["BATCH_03"]["next_action"])

    def test_example_download_is_the_working_input_byte_for_byte(self):
        with urlopen(self.base + "/example.csv") as response:
            self.assertIn("attachment", response.headers["Content-Disposition"])
            raw = response.read()
        self.assertEqual(raw, (ROOT / "data/csv_examples/new_organisers.csv").read_bytes())
        result = self.post("/api/analyse", raw)
        self.assertEqual(result["rows"], self.example["rows"])

    def test_15_field_template_round_trip_preserves_potential_cooldown_and_unknowns(self):
        with urlopen(self.base + "/template.csv") as response:
            columns = next(csv.reader(io.StringIO(response.read().decode("utf-8-sig"))))
        self.assertEqual(len(columns), 15)
        self.assertNotIn("public_evidence_json", columns)
        self.assertNotIn("review_fingerprint", columns)
        first = {"account_id": "SIMPLE_A", "organisation_name": "Test fixture A", "website": "https://fixture-a.invalid/",
                 "as_of": "2026-10-22T14:00:00Z", "crm_context_origin": "synthetic", "crm_match_state": "matched",
                 "crm_relationship": "prospect", "restriction_state": "no_restriction_in_scenario",
                 "simulated_annual_paid_events": "10", "simulated_paid_tickets_per_event": "100",
                 "simulated_average_ticket_price_usd": "300", "simulated_switching_difficulty": "medium",
                 "last_interaction_at": "2026-10-21T14:00:00Z", "last_interaction_outcome": "no_answer",
                 "last_interaction_channel": "phone"}
        second = {**first, "account_id": "SIMPLE_B", "organisation_name": "Test fixture B", "website": "https://fixture-b.invalid/",
                  "simulated_paid_tickets_per_event": "", "last_interaction_at": "", "last_interaction_outcome": "", "last_interaction_channel": ""}
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows([first, second])
        payload = self.post("/api/analyse", stream.getvalue().encode())
        rows = {r["account_id"]: r for r in payload["rows"]}
        self.assertEqual(rows["SIMPLE_A"]["simulated_commercial"]["opportunity"], "High")
        self.assertEqual(rows["SIMPLE_A"]["classification"], "deferred")
        self.assertEqual(rows["SIMPLE_B"]["simulated_commercial"]["opportunity"], "Unknown")
        self.assertEqual(rows["SIMPLE_B"]["classification"], "evidence_research")
        self.assertEqual(payload["issues"], [])

    def test_result_download_contains_all_accounts_in_display_order(self):
        with urlopen(self.base + self.example["download_results"]) as response:
            rows = list(csv.DictReader(io.StringIO(response.read().decode("utf-8-sig"))))
        self.assertEqual([r["Account"] for r in rows], [r["account"] for r in self.example["rows"]])
        self.assertTrue(all(r["CRM origin"] == "Simulated CRM" for r in rows))
        self.assertTrue(all(r["Annual paid-ticket volume"] == "Unknown" for r in rows))

    def test_handoff_download_is_a_review_draft_from_existing_engine(self):
        with urlopen(self.base + self.example["download_handoff"]) as response:
            archive = zipfile.ZipFile(io.BytesIO(response.read()))
        self.assertEqual(set(archive.namelist()), {"companies_PROPOSED.csv", "notes_PROPOSED.csv", "tasks_PROPOSED.csv", "HANDOFF_MAPPING.md", "READ_FIRST.txt"})
        tasks = list(csv.DictReader(io.StringIO(archive.read("tasks_PROPOSED.csv").decode("utf-8-sig"))))
        self.assertEqual(len(tasks), self.example["task_drafts"])
        self.assertTrue(all(r["Pending human review"] == "True" for r in tasks))
        self.assertTrue(all(r["Eligible for live export"] == "False" for r in tasks))
        self.assertIn("REVIEW DRAFT ONLY", archive.read("READ_FIRST.txt").decode())

    def test_bad_input_has_a_useful_error_and_server_remains_usable(self):
        with self.assertRaises(HTTPError) as caught:
            self.post("/api/analyse", b"account,priority\nMade up,100\n")
        self.assertEqual(caught.exception.code, 400)
        self.assertIn("Unsupported columns", json.load(caught.exception)["error"])
        caught.exception.close()
        self.assertEqual(len(self.post("/api/example", b"")["rows"]), 9)

    def test_external_origin_and_oversized_upload_rejected(self):
        with self.assertRaises(HTTPError) as caught:
            self.post("/api/example", b"", {"Origin": "https://unrelated.example"})
        self.assertEqual(caught.exception.code, 403)
        caught.exception.close()
        request = Request(self.base + "/api/analyse", data=b"", headers={"Content-Length": str(MAX_UPLOAD + 1)})
        with self.assertRaises(HTTPError) as caught:
            urlopen(request)
        self.assertEqual(caught.exception.code, 413)
        caught.exception.close()

    def test_download_routes_do_not_expose_project_files(self):
        for route in ["/AGENTS.md", "/download/../input.csv", "/download/" + "a" * 32 + "/results.csv"]:
            with self.assertRaises(HTTPError) as caught:
                urlopen(self.base + route)
            self.assertEqual(caught.exception.code, 404)
            caught.exception.close()

    def test_spreadsheet_formula_content_is_exported_as_text(self):
        payload = {"rows": [{**self.example["rows"][0], "account": "=1+1", "explanation": "+SUM(1,2)"}]}
        row = next(csv.DictReader(io.StringIO(results_csv(payload).decode("utf-8-sig"))))
        self.assertEqual(row["Account"], "'=1+1")
        self.assertEqual(row["Explanation"], "'+SUM(1,2)")

    def upload_changed_row(self, changes):
        with (ROOT / "data/csv_examples/new_organisers.csv").open(newline="") as source:
            rows = list(csv.DictReader(source))
        target = next(r for r in rows if r["account_id"] == "BATCH_02")
        target.update(changes)
        columns = list(dict.fromkeys([key for row in rows for key in row]))
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
        return self.post("/api/analyse", stream.getvalue().encode())

    def test_provided_crm_is_not_mislabelled_as_simulated(self):
        result = self.upload_changed_row({"crm_context_origin": "provided_unverified"})
        row = next(r for r in result["rows"] if r["account_id"] == "BATCH_02")
        self.assertEqual(row["crm_label"], "Provided CRM, unverified")
        self.assertIn("Callback due", row["crm_context"])

    def test_existing_hold_remains_visible_without_exposing_ledger(self):
        result = self.upload_changed_row({"review_decision": "hold", "review_fingerprint": "old_snapshot",
                                         "reviewer_id": "SIM_REVIEWER", "review_reason": "Contract question unresolved",
                                         "reviewed_at": "2026-10-22T13:00:00Z", "review_origin": "synthetic_fixture",
                                         "reopen_condition": "Contract status reviewed"})
        row = next(r for r in result["rows"] if r["account_id"] == "BATCH_02")
        self.assertEqual(row["priority"], "On hold")
        self.assertIn("Keep on hold", row["next_action"])
        self.assertIn("Contract question unresolved", row["explanation"])
        self.assertNotIn("review_fingerprint", row)
        with urlopen(self.base + result["download_handoff"]) as response:
            archive = zipfile.ZipFile(io.BytesIO(response.read()))
        self.assertNotIn("henceforthcomedy.com", archive.read("tasks_PROPOSED.csv").decode())


if __name__ == "__main__":
    unittest.main()
