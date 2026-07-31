import base64
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import cloud_automation as ca
import iris_fallback as fallback


class SecurityHardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        ca.STATE_FILE = root / "state.json"
        ca.PROCESSED_FILE = root / "processed.json"
        ca.ALERTS_FILE = root / "alerts.json"
        ca.HEARTBEAT_FILE = root / "heartbeat.json"
        ca.REPORTS_FILE = root / "reports.json"
        ca.AUTOMATION_LOCK = root / "automation.lock"
        ca.AUTOMATION_TOKEN = "secret"
        ca.SEND_POLICY = "ON_APPROVAL"

    def tearDown(self):
        self.tmp.cleanup()

    def app(self, *, send_email=lambda *a: None, create_draft=None, initial_drafts=None, send_draft=None, approve_draft=None):
        drafts = dict(initial_drafts or {})
        def create(subject, html, text, date_value):
            did = "draft-security-123456"
            drafts[did] = {"id": did, "subject": subject, "html_body": html, "text_body": text, "date": date_value, "status": "pending_approval"}
            return {"draft_id": did, "approval_url": "/approve"}
        app = FastAPI()
        app.include_router(ca.configure_router(
            get_token=lambda: "tok", send_email=send_email, create_draft=create_draft or create,
            load_drafts=lambda: drafts, save_drafts=lambda d: None,
            send_draft=send_draft or (lambda *a: (_ for _ in ()).throw(AssertionError("send forbidden"))),
            approve_draft=approve_draft or (lambda *a, **k: (_ for _ in ()).throw(AssertionError("approval forbidden"))),
            approvers=["a@example.com"], approval_senders=["a@example.com"],
            public_url="https://x", sender_email="iris@example.com", sender_name="Iris"))
        return TestClient(app)

    def test_machine_token_decision_endpoint_is_disabled(self):
        r = self.app().post("/api/lhos/automation/decision", headers={"x-lhos-automation-token": "secret"}, json={"actor": "Kristina", "text": "Approved", "message_id": "m1", "channel": "email"})
        self.assertEqual(r.status_code, 410)

    def test_machine_token_manual_send_endpoint_is_disabled(self):
        r = self.app().post("/api/lhos/automation/manual-send", headers={"x-lhos-automation-token": "secret"}, json={"date": "2030-01-02", "confirm": "SEND 2030-01-02 LATE TO ACTIVE BETA TESTERS"})
        self.assertEqual(r.status_code, 410)

    def test_arc_authentication_results_are_not_trusted(self):
        payload = {"headers": [{"name": "ARC-Authentication-Results", "value": "mx.google.com; dkim=pass header.i=@example.com; dmarc=pass"}]}
        ok, verdict = ca.sender_authenticated("a@example.com", payload)
        self.assertFalse(ok)
        self.assertEqual(verdict["basis"], "no_auth_results")

    def test_authserv_suffix_attack_is_rejected(self):
        payload = {"headers": [{"name": "Authentication-Results", "value": "mx.google.com.attacker.example; dkim=pass header.i=@example.com; dmarc=pass"}]}
        ok, verdict = ca.sender_authenticated("a@example.com", payload)
        self.assertFalse(ok)
        self.assertEqual(verdict["basis"], "untrusted_authserv")

    def test_negated_or_uncertain_confirmation_never_approves(self):
        for text in ("I haven't confirmed", "Please confirm when ready", "Not confirmed", "Maybe send it", "Do not approve", "Please don't approve this", "I can't approve this", "I won't approve this", "I haven't approved this", "I am unable to approve this"):
            self.assertNotEqual(ca.classify_instruction(text), "approve", text)

    def test_prepare_dry_run_does_not_write_heartbeat(self):
        at = ca.datetime(2030, 1, 2, 7, 0, tzinfo=ca.ET)
        with patch.object(ca, "now_et", return_value=at), patch.object(ca, "drive_source", return_value=(None, "", {"missing": True})), patch.object(ca, "gmail_subject_sent_any", return_value=False):
            r = self.app().post("/api/lhos/automation/prepare?dry_run=true", headers={"x-lhos-automation-token": "secret"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(ca.load(ca.HEARTBEAT_FILE, {}), {})

    def test_all_dry_run_endpoints_leave_every_persistent_file_unchanged(self):
        at=ca.datetime(2030,1,2,7,0,tzinfo=ca.ET);ca.atomic_json_write(ca.STATE_FILE,{});ca.atomic_json_write(ca.PROCESSED_FILE,[]);ca.atomic_json_write(ca.ALERTS_FILE,{});ca.atomic_json_write(ca.HEARTBEAT_FILE,{});ca.atomic_json_write(ca.REPORTS_FILE,{});ca.AUTOMATION_LOCK.write_text("LOCK-MARKER")
        paths=(ca.STATE_FILE,ca.PROCESSED_FILE,ca.ALERTS_FILE,ca.HEARTBEAT_FILE,ca.REPORTS_FILE,ca.AUTOMATION_LOCK);baseline={str(p):p.read_bytes() for p in paths};app=self.app()
        with patch.object(ca,"now_et",return_value=at),patch.object(ca,"drive_source",return_value=(None,"",{"missing":True})),patch.object(ca,"gmail_subject_sent_any",return_value=False),patch.object(ca,"gmail_search",return_value=[]):
            for endpoint in ("prepare","check-replies","auto-send","reconcile","close-out","watchdog"):
                r=app.post(f"/api/lhos/automation/{endpoint}?dry_run=true",headers={"x-lhos-automation-token":"secret"});self.assertEqual(r.status_code,200,endpoint);self.assertEqual({str(p):p.read_bytes() for p in paths},baseline,endpoint)

    def test_review_pending_prepare_dry_run_is_read_only(self):
        at=ca.datetime(2030,1,2,7,30,tzinfo=ca.ET);date_key="2030-01-02";draft_id="draft-security-123456";sent=[]
        state={"date":date_key,"date_display":"January 02, 2030","stage":"review_pending","content_valid":True,"draft_id":draft_id,"subject":"Daily","review_subject":"[REVIEW] Daily [draft:draft-security]","approval_url":"/approve","source":{"type":"iris_generated"},"raw_content":"valid","updated_at":at.isoformat()};ca.atomic_json_write(ca.STATE_FILE,{date_key:state});before=ca.STATE_FILE.read_bytes()
        app=self.app(send_email=lambda *a:sent.append(a),initial_drafts={draft_id:{"id":draft_id,"status":"pending_approval","html_body":"<p>x</p>","subject":"Daily","date":"January 02, 2030"}})
        with patch.object(ca,"now_et",return_value=at),patch.object(ca,"gmail_subject_sent_any",return_value=False):r=app.post("/api/lhos/automation/prepare?dry_run=true",headers={"x-lhos-automation-token":"secret"})
        self.assertEqual(r.json()["action"],"would_finish_pending_review");self.assertEqual(sent,[]);self.assertEqual(ca.STATE_FILE.read_bytes(),before);self.assertFalse(ca.AUTOMATION_LOCK.exists())

    def test_authenticated_approval_reply_dry_run_invokes_no_callbacks_or_writes(self):
        at=ca.datetime(2030,1,2,9,0,tzinfo=ca.ET);date_key="2030-01-02";draft_id="draft-security-123456";subject="[REVIEW] Daily [draft:draft-security]";state={"date":date_key,"date_display":"January 02, 2030","stage":"review_sent","content_valid":True,"draft_id":draft_id,"subject":"Daily","review_subject":subject,"source":{"type":"iris_generated"},"raw_content":"valid","updated_at":at.isoformat()};ca.atomic_json_write(ca.STATE_FILE,{date_key:state});ca.atomic_json_write(ca.PROCESSED_FILE,[]);paths=(ca.STATE_FILE,ca.PROCESSED_FILE,ca.ALERTS_FILE,ca.HEARTBEAT_FILE,ca.REPORTS_FILE,ca.AUTOMATION_LOCK);baseline={str(p):(p.exists(),p.read_bytes() if p.exists() else None) for p in paths};msg=self.gmail_message("Re: "+subject,"Approved")
        app=self.app(initial_drafts={draft_id:{"id":draft_id,"status":"pending_approval","subject":"Daily","html_body":"x","text_body":"x","date":"January 02, 2030"}})
        with patch.object(ca,"now_et",return_value=at),patch.object(ca,"gmail_search",return_value=[{"id":"mail-1"}]),patch.object(ca,"gmail_get",return_value=msg):response=app.post("/api/lhos/automation/check-replies?dry_run=true",headers={"x-lhos-automation-token":"secret"})
        self.assertEqual(response.json()["action"],"would_process_inbox");self.assertEqual(response.json()["classification"],"approve");self.assertEqual({str(p):(p.exists(),p.read_bytes() if p.exists() else None) for p in paths},baseline)

    def test_provider_error_bodies_are_never_disclosed(self):
        class Response:
            status_code=503
            text="SECRET_PROVIDER_BODY api_key=should-not-escape"
            def json(self):return {}
        with patch.object(ca.httpx,"get",return_value=Response()):
            for call in (lambda:ca.gmail_search("tok","q"),lambda:ca.drive_source("tok","2030-01-02")):
                with self.assertRaises(RuntimeError) as raised:call()
                self.assertNotIn("SECRET_PROVIDER_BODY",str(raised.exception))
            response=self.app().get("/api/lhos/automation/connectors",headers={"x-lhos-automation-token":"secret"});self.assertEqual(response.status_code,503);self.assertNotIn("SECRET_PROVIDER_BODY",response.text)
        with patch.object(ca,"GLM_API_KEY","configured"),patch.object(ca.httpx,"post",return_value=Response()):
            with self.assertRaises(RuntimeError) as raised:ca.revise_with_glm("safe original","safe feedback")
        self.assertNotIn("SECRET_PROVIDER_BODY",str(raised.exception))

    def test_unsafe_creative_provider_output_never_reaches_review(self):
        class Response:
            status_code=200
            def json(self):return {"choices":[{"message":{"content":"{\"subject\":\"LifeHouse OS Daily Briefing — Unsafe\",\"intro\":\"A sufficiently long but unsafe creative introduction for today’s briefing.\",\"sections\":[{\"title\":\"Today’s Beta Notes\",\"body\":\"Sprint 9 launches tomorrow with a new feature and a guaranteed result.\"}]}"}}]}
        with patch.object(fallback.httpx,"post",return_value=Response()):bundle=fallback.generate_bundle(date(2030,1,2),"","configured-key","https://provider.example")
        self.assertEqual(bundle["generator"],"curated-v1");self.assertNotIn("Sprint 9",bundle["raw"])

    def test_review_state_is_persisted_before_review_delivery(self):
        at = ca.datetime(2030, 1, 2, 7, 30, tzinfo=ca.ET)
        bundle = ca.generate_fallback_bundle("2030-01-02", "")
        with patch.object(ca, "now_et", return_value=at), patch.object(ca, "drive_source", return_value=(None, "", {"missing": True})), patch.object(ca, "gmail_subject_sent_any", return_value=False), patch.object(ca, "gmail_search", return_value=[]):
            with self.assertRaises(RuntimeError):
                self.app(send_email=lambda *a: (_ for _ in ()).throw(RuntimeError("ambiguous review delivery"))).post("/api/lhos/automation/prepare", headers={"x-lhos-automation-token": "secret"})
        state = ca.load(ca.STATE_FILE, {}).get("2030-01-02", {})
        self.assertEqual(state.get("stage"), "review_pending")
        self.assertEqual(state.get("draft_id"), "draft-security-123456")
        self.assertIn("draft-security", state.get("review_subject", ""))

    def test_review_pending_recovers_same_draft_after_ambiguous_delivery_failure(self):
        at=ca.datetime(2030,1,2,7,30,tzinfo=ca.ET);attempts=[]
        def unstable(*args):
            attempts.append(args)
            if len(attempts)==1:raise RuntimeError("ambiguous review delivery")
        app=self.app(send_email=unstable)
        patches=(patch.object(ca,"now_et",return_value=at),patch.object(ca,"drive_source",return_value=(None,"",{"missing":True})),patch.object(ca,"gmail_subject_sent_any",return_value=False),patch.object(ca,"gmail_search",return_value=[]))
        with patches[0],patches[1],patches[2],patches[3]:
            with self.assertRaises(RuntimeError):app.post("/api/lhos/automation/prepare",headers={"x-lhos-automation-token":"secret"})
            recovered=app.post("/api/lhos/automation/prepare",headers={"x-lhos-automation-token":"secret"})
        self.assertEqual(recovered.json()["action"],"review_sent");state=ca.load(ca.STATE_FILE,{})["2030-01-02"];self.assertEqual(state["stage"],"review_sent");self.assertEqual(state["draft_id"],"draft-security-123456");self.assertEqual(len(attempts),2)

    @staticmethod
    def gmail_message(subject, body):
        data=base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
        return {"id":"mail-1","internalDate":"1000","payload":{"mimeType":"text/plain","headers":[
            {"name":"From","value":"a@example.com"},{"name":"Subject","value":subject},
            {"name":"Authentication-Results","value":"mx.google.com; dkim=pass header.i=@example.com; dmarc=pass"}],
            "body":{"data":data}}}

    def test_unbound_approval_email_cannot_approve_current_draft(self):
        at=ca.datetime(2030,1,2,9,0,tzinfo=ca.ET);date_key="2030-01-02"
        ca.atomic_json_write(ca.STATE_FILE,{date_key:{"date":date_key,"date_display":"January 02, 2030","stage":"review_sent","content_valid":True,"draft_id":"draft-security-123456","review_subject":"[REVIEW] Daily [draft:draft-security-1]","source":{"name":"300102.docx"}}})
        msg=self.gmail_message("LifeHouse OS general note","Approved")
        with patch.object(ca,"now_et",return_value=at),patch.object(ca,"gmail_search",return_value=[{"id":"mail-1"}]),patch.object(ca,"gmail_get",return_value=msg):
            r=self.app().post("/api/lhos/automation/check-replies",headers={"x-lhos-automation-token":"secret"})
        self.assertIn("ignored_unbound_message",str(r.json()))

    def test_prepare_creates_hold_and_processes_pending_reference_without_state(self):
        at=ca.datetime(2030,1,2,7,30,tzinfo=ca.ET);date_key="2030-01-02";msg=self.gmail_message("LifeHouse OS daily briefing reference","Please use travel preparation and a calmer return-home transition as today's editorial reference.");reviews=[];app=self.app(send_email=lambda *a:reviews.append(a))
        with patch.object(ca,"now_et",return_value=at),patch.object(ca,"drive_source",return_value=(None,"",{"name":"300102.docx","missing":True})),patch.object(ca,"gmail_subject_sent_any",return_value=False),patch.object(ca,"gmail_search",return_value=[{"id":"mail-1"}]),patch.object(ca,"gmail_get",return_value=msg):
            first=app.post("/api/lhos/automation/prepare",headers={"x-lhos-automation-token":"secret"});self.assertEqual(first.json()["action"],"awaiting_authenticated_reference_processing");state=ca.load(ca.STATE_FILE,{})[date_key];self.assertEqual(state["stage"],"hold");self.assertFalse(state["content_valid"])
            second=app.post("/api/lhos/automation/check-replies",headers={"x-lhos-automation-token":"secret"});self.assertEqual(second.status_code,200);state=ca.load(ca.STATE_FILE,{})[date_key];self.assertIn("travel preparation",state["reference_content"]);self.assertIn("mail-1",ca.load(ca.PROCESSED_FILE,[]))
            third=app.post("/api/lhos/automation/prepare",headers={"x-lhos-automation-token":"secret"})
        self.assertEqual(third.json()["action"],"review_sent");self.assertEqual(ca.load(ca.STATE_FILE,{})[date_key]["stage"],"review_sent");self.assertEqual(len(reviews),1)

    def test_prepare_waits_when_authenticated_reference_is_pending_at_730(self):
        at=ca.datetime(2030,1,2,7,30,tzinfo=ca.ET);date_key="2030-01-02"
        ca.atomic_json_write(ca.STATE_FILE,{date_key:{"date":date_key,"date_display":"January 02, 2030","stage":"hold","content_valid":False}})
        msg=self.gmail_message("LifeHouse OS daily briefing reference","Please use travel preparation and a calmer return-home transition as the editorial reference for today.")
        with patch.object(ca,"now_et",return_value=at),patch.object(ca,"drive_source",return_value=(None,"",{"missing":True})),patch.object(ca,"gmail_subject_sent_any",return_value=False),patch.object(ca,"gmail_search",return_value=[{"id":"mail-1"}]),patch.object(ca,"gmail_get",return_value=msg):
            r=self.app().post("/api/lhos/automation/prepare",headers={"x-lhos-automation-token":"secret"})
        self.assertEqual(r.json()["action"],"awaiting_authenticated_reference_processing")
        self.assertEqual(ca.load(ca.STATE_FILE,{})[date_key]["stage"],"hold")


if __name__ == "__main__":
    unittest.main()
