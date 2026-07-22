import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
import cloud_automation as ca
class CloudTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();root=Path(self.t.name);ca.STATE_FILE=root/'state.json';ca.PROCESSED_FILE=root/'processed.json';ca.AUTOMATION_TOKEN='secret';ca.END_DATE=''
 def tearDown(self):self.t.cleanup()
 def app(self,send_email=lambda *a:(_ for _ in ()).throw(AssertionError('send called')),send_draft=lambda *a:(_ for _ in ()).throw(AssertionError('send draft called'))):
  app=FastAPI(); drafts={}
  def create(s,h,t,d):drafts['id']={'id':'id','subject':s,'html_body':h,'text_body':t,'date':d,'status':'pending_approval'};return {'draft_id':'id'}
  app.include_router(ca.configure_router(get_token=lambda:'tok',send_email=send_email,create_draft=create,load_drafts=lambda:drafts,save_drafts=lambda d:None,send_draft=send_draft,approvers=['a@example.com'],public_url='https://x',sender_email='iris@example.com',sender_name='Iris'));return TestClient(app)
 def test_deterministic_preserves_named_sections(self):
  raw=('Good day Beta Team\nSprint 2 Continues\nWe fixed the dashboard issue and added a new feature for testing. '*4+'\nYour One-Time Survey Opens Today\nPlease complete the survey and send feedback.')
  s=ca.deterministic_sections(raw);blob=' '.join(s.values());self.assertIn('Sprint 2 Continues',blob);self.assertIn('Survey Opens Today',blob)
 def test_prepare_dry_run_never_sends(self):
  raw=('Today we fixed the mobile dashboard issue and added a new feature for testing. Please send feedback. '*5)
  with patch.object(ca,'gmail_subject_sent_any',return_value=False),patch.object(ca,'drive_source',return_value=({'name':'x'},raw,{'name':'x'})):
   r=self.app().post('/api/lhos/automation/prepare?dry_run=true',headers={'x-lhos-automation-token':'secret'});self.assertEqual(r.status_code,200);self.assertEqual(r.json()['action'],'would_send_review')
 def test_hold_blocks_auto_send(self):
  date,_=ca.now_et().strftime('%Y-%m-%d'),None;ca.atomic_json_write(ca.STATE_FILE,{date:{'stage':'hold','content_valid':False}})
  r=self.app().post('/api/lhos/automation/auto-send',headers={'x-lhos-automation-token':'secret'});self.assertEqual(r.json()['action'],'blocked')
 def test_unauthorized(self):
  self.assertEqual(self.app().get('/api/lhos/automation/status').status_code,401)
if __name__=='__main__':unittest.main()
