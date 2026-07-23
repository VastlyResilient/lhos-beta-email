import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
import cloud_automation as ca
class CloudTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();root=Path(self.t.name);ca.STATE_FILE=root/'state.json';ca.PROCESSED_FILE=root/'processed.json';ca.ALERTS_FILE=root/'alerts.json';ca.AUTOMATION_LOCK=root/'automation.lock';ca.AUTOMATION_TOKEN='secret';ca.END_DATE=''
 def tearDown(self):self.t.cleanup()
 def app(self,send_email=lambda *a:(_ for _ in ()).throw(AssertionError('send called')),send_draft=lambda *a:(_ for _ in ()).throw(AssertionError('send draft called')),approve_draft=lambda *a:{'status':'approved'},initial_drafts=None):
  app=FastAPI(); drafts=dict(initial_drafts or {}); self._drafts=drafts
  def create(s,h,t,d):drafts['id']={'id':'id','subject':s,'html_body':h,'text_body':t,'date':d,'status':'pending_approval'};return {'draft_id':'id'}
  app.include_router(ca.configure_router(get_token=lambda:'tok',send_email=send_email,create_draft=create,load_drafts=lambda:drafts,save_drafts=lambda d:None,send_draft=send_draft,approve_draft=approve_draft,approvers=['a@example.com'],public_url='https://x',sender_email='iris@example.com',sender_name='Iris'));return TestClient(app)
 def test_deterministic_preserves_named_sections(self):
  raw=('Good day Beta Team\nSprint 2 Continues\nWe fixed the dashboard issue and added a new feature for testing. '*4+'\nYour One-Time Survey Opens Today\nPlease complete the survey and send feedback.')
  s=ca.deterministic_sections(raw);blob=' '.join(s.values());self.assertIn('Sprint 2 Continues',blob);self.assertIn('Survey Opens Today',blob)
 def test_prepare_dry_run_never_sends(self):
  raw=('Today we fixed the mobile dashboard issue and added a new feature for testing. Please send feedback. '*5)
  with patch.object(ca,'gmail_subject_sent_any',return_value=False),patch.object(ca,'drive_source',return_value=({'name':'x'},raw,{'name':'x'})):
   r=self.app().post('/api/lhos/automation/prepare?dry_run=true',headers={'x-lhos-automation-token':'secret'});self.assertEqual(r.status_code,200);self.assertEqual(r.json()['action'],'would_send_review')
 def test_unapproved_never_sends_at_deadline(self):
  at9=ca.now_et().replace(hour=9,minute=0,second=0,microsecond=0);date=at9.strftime('%Y-%m-%d');ca.atomic_json_write(ca.STATE_FILE,{date:{'stage':'review_sent','content_valid':True}})
  with patch.object(ca,'now_et',return_value=at9):
   r=self.app().post('/api/lhos/automation/auto-send?dry_run=true',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.json()['action'],'would_notify_not_sent')
 def test_deadline_refuses_before_nine(self):
  before=ca.now_et().replace(hour=8,minute=59,second=0,microsecond=0)
  with patch.object(ca,'now_et',return_value=before):
   r=self.app().post('/api/lhos/automation/auto-send',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.json()['action'],'too_early')
 def test_instruction_classifier(self):
  for text in ['Approved','Good, send it','Confirmed','Looks good — ship it']:
   self.assertEqual(ca.classify_instruction(text),'approve')
  self.assertEqual(ca.classify_instruction("Don't send it yet"),'hold')
  self.assertEqual(ca.classify_instruction('Looks good but change the headline'),'revise')
  self.assertEqual(ca.classify_instruction('Thanks'),'ambiguous')
 def test_unauthorized(self):
  self.assertEqual(self.app().get('/api/lhos/automation/status').status_code,401)
 def test_watchdog_dry_run_never_sends(self):
  with patch.object(ca,'now_et',return_value=ca.datetime(2030,1,1,10,0,tzinfo=ca.ET)):
   r=self.app().post('/api/lhos/automation/watchdog?dry_run=true',headers={'x-lhos-automation-token':'secret'})
   self.assertEqual(r.json()['action'],'would_alert_bobby')
 def test_revision_persists_new_draft_and_supersedes_old(self):
  at8=ca.now_et().replace(hour=8,minute=0,second=0,microsecond=0);date=at8.strftime('%Y-%m-%d');raw='Daily Beta Notes\nA concrete beta update describes testing and feedback from users.\nWhat Changed\nThe dashboard has a revised navigation flow with clearer labels.\nHelpful Reminder\nPlease continue testing and report any specific issue.\nThank You\nThank you for the detailed feedback and continued beta participation.'
  ca.atomic_json_write(ca.STATE_FILE,{date:{'date':date,'date_display':'July 23, 2026','stage':'review_sent','content_valid':True,'draft_id':'old','subject':'S','review_subject':'[REVIEW] S','raw_content':raw}})
  sent=[];app=self.app(send_email=lambda *a:sent.append(a),initial_drafts={'old':{'id':'old','status':'pending_approval','text_body':raw}})
  with patch.object(ca,'now_et',return_value=at8),patch.object(ca,'revise_with_glm',return_value=raw.replace('clearer labels','clearer labels and revised colors')):
   r=app.post('/api/lhos/automation/decision',headers={'x-lhos-automation-token':'secret'},json={'actor':'Kristina','text':'change the labels','message_id':'m-revise','channel':'imessage'})
  self.assertEqual(r.status_code,200);self.assertEqual(r.json()['action'],'revised_review_sent');newid=r.json()['draft_id'];self.assertIn(newid,self._drafts);self.assertEqual(self._drafts['old']['status'],'revised');self.assertEqual(len(sent),1)
if __name__=='__main__':unittest.main()
