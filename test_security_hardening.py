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

    def app(self, *, send_email=lambda *a: None, create_draft=None):
        drafts = {}
        def create(subject, html, text, date_value):
            did = "draft-security-123456"
            drafts[did] = {"id": did, "subject": subject, "html_body": html, "text_body": text, "date": date_value, "status": "pending_approval"}
            return {"draft_id": did, "approval_url": "/approve"}
        app = FastAPI()
        app.include_router(ca.configure_router(
            get_token=lambda: "tok", send_email=send_email, create_draft=create_draft or create,
            load_drafts=lambda: drafts, save_drafts=lambda d: None,
            send_draft=lambda *a: (_ for _ in ()).throw(AssertionError("send forbidden")),
            approve_draft=lambda *a, **k: (_ for _ in ()).throw(AssertionError("approval forbidden")),
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
        for text in ("I haven't confirmed", "Please confirm when ready", "Not confirmed", "Maybe send it"):
            self.assertNotEqual(ca.classify_instruction(text), "approve", text)

    def test_prepare_dry_run_does_not_write_heartbeat(self):
        at = ca.datetime(2030, 1, 2, 7, 0, tzinfo=ca.ET)
        with patch.object(ca, "now_et", return_value=at), patch.object(ca, "drive_source", return_value=(None, "", {"missing": True})), patch.object(ca, "gmail_subject_sent_any", return_value=False):
            r = self.app().post("/api/lhos/automation/prepare?dry_run=true", headers={"x-lhos-automation-token": "secret"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(ca.load(ca.HEARTBEAT_FILE, {}), {})

    def test_fallback_publication_never_calls_model_provider(self):
        with patch.object(fallback.httpx, "post") as post:
            bundle = fallback.generate_bundle(date(2030, 1, 2), "travel packing reference", "configured-key", "https://provider.example")
        post.assert_not_called()
        self.assertEqual(bundle["generator"], "curated-v1")

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
