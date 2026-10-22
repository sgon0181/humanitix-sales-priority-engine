"""New-file, one-command, provenance and approval-boundary tests."""
from copy import deepcopy
import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from priority_engine.__main__ import load
from priority_engine.engine import build_queue
from priority_engine.from_csv import COLUMNS, execute, read_csv
from priority_engine.operations import prepare_work
from priority_engine.validation import validate_crm, validate_public
from test_engine import ROOT

CAP = ROOT/"data/synthetic/capacity.json"
RULES = ROOT/"priority_engine/rules.json"
POLICY = ROOT/"priority_engine/workplan_rules.json"


def row(aid="EXTERNAL_1", **changes):
    return {"account_id":aid,"organisation_name":"Explicit software test fixture " + aid,
            "website":"https://fixture.invalid/", "as_of":"2026-10-22T14:00:00Z",
            "crm_context_origin":"synthetic", "crm_match_state":"matched", "crm_relationship":"prospect",
            "restriction_state":"no_restriction_in_scenario", **changes}


def write(path, rows, columns=COLUMNS):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=columns)
        w.writeheader()
        for r in rows: w.writerow({k:r.get(k,"") for k in columns})


def approve_current(path, rows, decision="accept"):
    """Author an explicit test review; the runtime must never do this itself."""
    write(path,rows)
    d=read_csv(path)
    accounts,_=validate_public(d["public"])
    contexts,_=validate_crm(d["crm"],{a["account_id"] for a in accounts},d["clock"])
    core=build_queue(accounts,contexts,d["clock"],load(RULES),"CSV_PREVIEW")
    snapshots={i["account_id"]:i["review_fingerprint"] for i in prepare_work(core,contexts,load(POLICY))}
    for r in rows:
        r.update(review_decision=decision,review_fingerprint=snapshots[r["account_id"]],reviewer_id="SIM_TEST_REVIEWER",
                 review_reason="Explicit fixture decision",reviewed_at="2026-10-22T13:30:00Z",review_origin="synthetic_fixture")
        if decision=="hold": r["reopen_condition"]="A human reviews again"
    write(path,rows)


class CsvInputTests(unittest.TestCase):
    def run_rows(self, rows, approve=False):
        folder=tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        source=Path(folder.name)/"incoming.csv"
        output=Path(folder.name)/"results"
        if approve: approve_current(source,rows)
        else: write(source,rows)
        plan,handoff=execute(source,output,CAP,RULES,POLICY)
        return source,output,plan,handoff

    def test_new_csv_ids_and_size_drive_runtime_not_the_95_account_dataset(self):
        source,out,plan,_=self.run_rows([row("EXTERNAL_A"),row("NEVER_SEEN_B"),row("NEW_C")])
        self.assertEqual({i["account_id"] for i in plan["work"]},{"EXTERNAL_A","NEVER_SEEN_B","NEW_C"})
        self.assertEqual(len(plan["work"]),3)
        self.assertEqual(load(out/"import_summary.json")["proposed_tasks"],3)

    def test_no_approvals_still_generates_proposed_workload_and_handoff(self):
        _,out,plan,handoff=self.run_rows([row()])
        self.assertEqual(plan["metadata"]["assigned_tasks"],0)
        self.assertEqual(handoff["tasks"],[])
        proposal=load(out/"workload_proposal.json")
        self.assertEqual(proposal["metadata"]["proposed_tasks"],1)
        self.assertEqual(proposal["metadata"]["assigned_tasks"],0)
        self.assertIsNone(proposal["work"][0]["review"])
        self.assertIsNone(proposal["work"][0]["assigned_rep_id"])
        self.assertTrue(proposal["work"][0]["proposed_rep_id"])
        draft=load(out/"handoff_PROPOSED.json")
        self.assertTrue(draft["metadata"]["pending_human_review"])
        self.assertFalse(draft["tasks"][0]["eligible_for_live_export"])

    def test_claimed_price_and_frequency_do_not_self_verify(self):
        _,_,plan,_=self.run_rows([row(ticket_price_usd="9999",event_frequency="daily",event_start_date="2026-11-01")])
        self.assertEqual(plan["work"][0]["commercial_potential"]["activity_signal"],"paid_activity_unestablished")
        self.assertEqual(plan["work"][0]["qualification"]["verification_status"],"not_reviewed")

    def test_explicit_flat_observation_needs_no_json_or_code_changes(self):
        r=row(evidence_reviewed_by="SIM_TEST_RESEARCHER",evidence_checked_on="2026-10-22",evidence_access_basis="direct_primary",
              identity_observed="true",us_scope="us_events_observed",public_source_url="https://fixture.invalid/event",
              event_name="Synthetic test offer",event_start_date="2026-11-01",ticket_price_usd="10",purchase_unit="one_admission")
        _,out,plan,_=self.run_rows([r])
        self.assertEqual(plan["work"][0]["commercial_potential"]["activity_signal"],"one_dated_paid_offer")
        self.assertIn("not verified by the importer",load(out/"imported_public.json")["organisers"][0]["evidence_origin"])

    def test_public_evidence_and_input_bytes_are_preserved_with_provenance(self):
        source,out,_,_=self.run_rows([row(source_record_ids_json='["source:original"]',source_urls_json='["https://fixture.invalid/source"]')])
        self.assertEqual(source.read_bytes(),(out/"input.csv").read_bytes())
        record=load(out/"imported_public.json")["organisers"][0]
        self.assertIn("source:original",record["source_record_ids"])
        self.assertTrue(any(x.startswith("csv:") and x.endswith(":row:2") for x in record["source_record_ids"]))
        self.assertEqual(load(out/"source_claims.json")["rows"][0]["original_values"]["account_id"],"EXTERNAL_1")

    def test_quoted_names_bom_and_utf8_are_supported(self):
        _,_,plan,_=self.run_rows([row(organisation_name='Fixture, "Quoted" Café')])
        self.assertEqual(plan["work"][0]["organisation_name"],'Fixture, "Quoted" Café')

    def test_supplied_current_approval_can_allocate_without_a_second_command(self):
        _,_,plan,handoff=self.run_rows([row()],approve=True)
        self.assertEqual(plan["metadata"]["assigned_tasks"],1)
        self.assertEqual(len(handoff["tasks"]),1)

    def test_changed_crm_never_silently_refreshes_old_approval(self):
        source,out,_,_=self.run_rows([row()],approve=True)
        with source.open(encoding="utf-8-sig") as stream:
            rows=list(csv.DictReader(stream))
        old=rows[0]["review_fingerprint"]
        rows[0].update(callback_requested_at="2026-10-20T14:00:00Z",callback_due_at="2026-10-22T13:00:00Z",callback_channel="phone")
        write(source,rows)
        plan,_=execute(source,out,CAP,RULES,POLICY)
        self.assertEqual(plan["work"][0]["underlying_queue"],"callback_review")
        self.assertEqual(plan["work"][0]["review_state"],"stale_decision")
        self.assertEqual(plan["metadata"]["assigned_tasks"],0)
        self.assertEqual(load(out/"imported_reviews.json")["decisions"][0]["fingerprint"],old)

    def test_fingerprint_is_not_manufactured_from_an_accept_cell(self):
        _,out,plan,_=self.run_rows([row(review_decision="accept",reviewer_id="SIM_TEST",review_reason="Accept",reviewed_at="2026-10-22T13:00:00Z",review_origin="synthetic_fixture")])
        self.assertEqual(plan["metadata"]["assigned_tasks"],0)
        self.assertGreater(load(out/"import_summary.json")["review_issues"],0)
        self.assertEqual(load(out/"workload_proposal.json")["metadata"]["proposed_tasks"],0)

    def test_hold_blocks_proposals_and_actual_assignments(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        source=Path(folder.name)/"input.csv";out=Path(folder.name)/"out"
        rows=[row()];approve_current(source,rows,"hold")
        plan,_=execute(source,out,CAP,RULES,POLICY)
        self.assertEqual(plan["metadata"]["assigned_tasks"],0)
        self.assertEqual(load(out/"workload_proposal.json")["metadata"]["proposed_tasks"],0)

    def test_do_not_contact_and_future_wait_cannot_be_overridden_by_proposals(self):
        for changes in [{"restriction_state":"global_do_not_contact"},
                        {"callback_requested_at":"2026-10-20T14:00:00Z","callback_due_at":"2026-10-24T14:00:00Z","callback_channel":"phone"}]:
            _,out,_,_=self.run_rows([row(**changes)])
            self.assertEqual(load(out/"workload_proposal.json")["metadata"]["proposed_tasks"],0)

    def test_unknown_owner_mapping_blocks_instead_of_reassigning(self):
        _,out,plan,_=self.run_rows([row(owner_id="UNMAPPED_OWNER")])
        self.assertEqual(plan["metadata"]["assigned_tasks"],0)
        self.assertEqual(load(out/"workload_proposal.json")["metadata"]["proposed_tasks"],0)
        self.assertIn("validation",plan["work"][0]["allocation_reason"])

    def test_bad_crm_keeps_public_identity_and_reports_the_bad_section(self):
        _,out,plan,_=self.run_rows([row(callback_due_at="2026-10-22T13:00:00Z")])
        self.assertEqual(len(plan["work"]),1)
        self.assertGreater(load(out/"import_summary.json")["import_issues"],0)
        self.assertEqual(plan["work"][0]["underlying_queue"],"internal_clearance")

    def test_provided_crm_claims_are_not_mislabelled_as_invented_history(self):
        _,out,_,_=self.run_rows([row(crm_context_origin="provided_unverified")])
        note=load(out/"handoff_PROPOSED.json")["notes"][0]["standard_properties"]["Note body"]
        self.assertIn("PROVIDED CRM INFORMATION",note)
        self.assertNotIn("SIMULATED CRM HISTORY",note)
        self.assertEqual(load(out/"imported_crm.json")["accounts"][0]["source_origin"],"provided_unverified")

    def test_ambiguous_crm_origin_is_not_guessed(self):
        _,out,_,_=self.run_rows([row(crm_context_origin="")])
        self.assertEqual(load(out/"workload_proposal.json")["metadata"]["proposed_tasks"],0)
        self.assertGreater(load(out/"import_summary.json")["import_issues"],0)

    def test_duplicate_ids_are_reported_without_overwriting_or_hidden_fallback(self):
        _,out,plan,_=self.run_rows([row(),row(),row("ONLY_VALID")])
        self.assertEqual([i["account_id"] for i in plan["work"]],["ONLY_VALID"])
        self.assertEqual(len(load(out/"source_claims.json")["rows"]),3)

    def test_priority_columns_and_unknown_headers_are_rejected(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        source=Path(folder.name)/"input.csv"
        write(source,[{**row(),"priority":"high"}],COLUMNS+["priority"])
        with self.assertRaisesRegex(ValueError,"Unsupported columns"):read_csv(source)

    def test_explicit_clock_required_and_conflicting_clocks_rejected(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        source=Path(folder.name)/"input.csv"
        write(source,[row(as_of="")])
        with self.assertRaisesRegex(ValueError,"no hidden demo clock"):read_csv(source)
        self.assertEqual(read_csv(source,"2026-10-22T14:00:00Z")["clock"].year,2026)
        write(source,[row(),row("SECOND",as_of="2026-10-23T14:00:00Z")])
        with self.assertRaisesRegex(ValueError,"conflicting"):read_csv(source)

    def test_one_cli_command_generates_every_output_for_a_fresh_csv(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        source=Path(folder.name)/"incoming.csv";out=Path(folder.name)/"out"
        write(source,[row("BRAND_NEW")])
        command=[sys.executable,str(ROOT/"run_csv.py"),str(source),"--output",str(out)]
        result=subprocess.run(command,cwd=source.parent,capture_output=True,text=True,check=True)
        self.assertEqual(json.loads(result.stdout)["accounts"],1)
        for filename in ["workload_proposal.md","workload_proposal.json","handoff_PROPOSED.json","companies_PROPOSED.csv","notes_PROPOSED.csv","tasks_PROPOSED.csv","workplan.md","unassigned.csv","representatives.csv","source_claims.json","validation_report.json"]:
            self.assertTrue((out/filename).is_file(),filename)

    def test_same_csv_and_clock_have_byte_identical_outputs(self):
        source,first,_,_=self.run_rows([row()])
        second=first.parent/"second"
        execute(source,second,CAP,RULES,POLICY)
        self.assertEqual({p.name for p in first.iterdir()},{p.name for p in second.iterdir()})
        for path in first.iterdir():self.assertEqual(path.read_bytes(),(second/path.name).read_bytes(),path.name)

    def test_output_directory_cannot_overwrite_the_input_csv(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        source=Path(folder.name)/"input.csv"
        write(source,[row()]);before=source.read_bytes()
        with self.assertRaisesRegex(ValueError,"separate directory"):execute(source,source.parent,CAP,RULES,POLICY)
        self.assertEqual(source.read_bytes(),before)


if __name__=="__main__":unittest.main()
