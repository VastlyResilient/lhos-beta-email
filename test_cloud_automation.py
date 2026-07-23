import tempfile,unittest,base64,zipfile,io
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
import cloud_automation as ca
class CloudTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();root=Path(self.t.name);ca.STATE_FILE=root/'state.json';ca.PROCESSED_FILE=root/'processed.json';ca.ALERTS_FILE=root/'alerts.json';ca.AUTOMATION_LOCK=root/'automation.lock';ca.AUTOMATION_TOKEN='secret';ca.END_DATE=''
 def tearDown(self):self.t.cleanup()
 def app(self,send_email=lambda *a:(_ for _ in ()).throw(AssertionError('send called')),send_draft=lambda *a:(_ for _ in ()).throw(AssertionError('send draft called')),approve_draft=lambda *a,**k:{'status':'approved'},initial_drafts=None):
  app=FastAPI(); drafts=dict(initial_drafts or {}); self._drafts=drafts
  def create(s,h,t,d):drafts['id']={'id':'id','subject':s,'html_body':h,'text_body':t,'date':d,'status':'pending_approval'};return {'draft_id':'id'}
  app.include_router(ca.configure_router(get_token=lambda:'tok',send_email=send_email,create_draft=create,load_drafts=lambda:drafts,save_drafts=lambda d:None,send_draft=send_draft,approve_draft=approve_draft,approvers=['a@example.com'],approval_senders=['a@example.com'],public_url='https://x',sender_email='iris@example.com',sender_name='Iris'));return TestClient(app)
 def test_deterministic_preserves_named_sections(self):
  raw=('Good day Beta Team\nSprint 2 Continues\nWe fixed the dashboard issue and added a new feature for testing. '*4+'\nYour One-Time Survey Opens Today\nPlease complete the survey and send feedback.')
  s=ca.deterministic_sections(raw);blob=' '.join(s.values());self.assertIn('Sprint 2 Continues',blob);self.assertIn('Survey Opens Today',blob)
 def test_prepare_dry_run_never_sends(self):
  raw=('Today we fixed the mobile dashboard issue and added a new feature for testing. Please send feedback. '*5)
  with patch.object(ca,'gmail_subject_sent_any',return_value=False),patch.object(ca,'drive_source',return_value=({'name':'x'},raw,{'name':'x'})):
   r=self.app().post('/api/lhos/automation/prepare?dry_run=true',headers={'x-lhos-automation-token':'secret'});self.assertEqual(r.status_code,200);self.assertEqual(r.json()['action'],'would_send_review')
 def test_unapproved_never_sends_at_deadline(self):
  at15=ca.now_et().replace(hour=15,minute=0,second=0,microsecond=0);date=at15.strftime('%Y-%m-%d');ca.atomic_json_write(ca.STATE_FILE,{date:{'stage':'review_sent','content_valid':True}})
  with patch.object(ca,'now_et',return_value=at15):
   r=self.app().post('/api/lhos/automation/auto-send?dry_run=true',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.json()['action'],'would_notify_not_sent')
 def test_deadline_refuses_before_three(self):
  before=ca.now_et().replace(hour=14,minute=59,second=0,microsecond=0)
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
  with patch.object(ca,'now_et',return_value=ca.datetime(2030,1,1,16,0,tzinfo=ca.ET)):
   r=self.app().post('/api/lhos/automation/watchdog?dry_run=true',headers={'x-lhos-automation-token':'secret'})
   self.assertEqual(r.json()['action'],'would_alert_bobby')
 def test_revision_persists_new_draft_and_supersedes_old(self):
  at8=ca.now_et().replace(hour=8,minute=0,second=0,microsecond=0);date=at8.strftime('%Y-%m-%d');raw='Daily Beta Notes\nA concrete beta update describes testing and feedback from users.\nWhat Changed\nThe dashboard has a revised navigation flow with clearer labels.\nHelpful Reminder\nPlease continue testing and report any specific issue.\nThank You\nThank you for the detailed feedback and continued beta participation.'
  ca.atomic_json_write(ca.STATE_FILE,{date:{'date':date,'date_display':'July 23, 2026','stage':'review_sent','content_valid':True,'draft_id':'old','subject':'S','review_subject':'[REVIEW] S','raw_content':raw}})
  sent=[];app=self.app(send_email=lambda *a:sent.append(a),initial_drafts={'old':{'id':'old','status':'pending_approval','text_body':raw}})
  with patch.object(ca,'now_et',return_value=at8),patch.object(ca,'revise_with_glm',return_value=raw.replace('clearer labels','clearer labels and revised colors')):
   r=app.post('/api/lhos/automation/decision',headers={'x-lhos-automation-token':'secret'},json={'actor':'Kristina','text':'change the labels','message_id':'m-revise','channel':'imessage'})
  self.assertEqual(r.status_code,200);self.assertEqual(r.json()['action'],'revised_review_sent');newid=r.json()['draft_id'];self.assertIn(newid,self._drafts);self.assertEqual(self._drafts['old']['status'],'revised');self.assertEqual(len(sent),1)
 def test_manual_late_send_requires_exact_confirmation(self):
  at16=ca.now_et().replace(hour=16,minute=0,second=0,microsecond=0)
  with patch.object(ca,'now_et',return_value=at16):
   r=self.app().post('/api/lhos/automation/manual-send',headers={'x-lhos-automation-token':'secret'},json={'date':at16.strftime('%Y-%m-%d'),'confirm':'send it'})
  self.assertEqual(r.status_code,400)
 def test_manual_late_send_dry_run_is_non_sending(self):
  at16=ca.now_et().replace(hour=16,minute=0,second=0,microsecond=0);date=at16.strftime('%Y-%m-%d');raw='Daily Beta Notes\nA concrete beta update describes testing and feedback from users.\nWhat Changed\nThe dashboard has a revised navigation flow with clearer labels.\nHelpful Reminder\nPlease continue testing and report any specific issue.\nThank You\nThank you for the detailed feedback and continued beta participation.'
  with patch.object(ca,'now_et',return_value=at16),patch.object(ca,'gmail_subject_sent_any',return_value=False),patch.object(ca,'drive_source',return_value=({'id':'f'},raw,{'name':'x.docx'})):
   r=self.app().post('/api/lhos/automation/manual-send?dry_run=true',headers={'x-lhos-automation-token':'secret'},json={'date':date,'confirm':f'SEND {date} LATE TO ACTIVE BETA TESTERS'})
  self.assertEqual(r.status_code,200);self.assertEqual(r.json()['action'],'would_manual_send')
 def test_manual_late_send_happy_path_calls_delivery_once(self):
  at16=ca.now_et().replace(hour=16,minute=0,second=0,microsecond=0);date=at16.strftime('%Y-%m-%d');raw='Daily Beta Notes\nA concrete beta update describes testing and feedback from users.\nWhat Changed\nThe dashboard has a revised navigation flow with clearer labels.\nHelpful Reminder\nPlease continue testing and report any specific issue.\nThank You\nThank you for the detailed feedback and continued beta participation.';calls=[];approvals=[]
  app=self.app(send_draft=lambda *a:(calls.append(a) or {'status':'sent','recipient_count':2,'newly_sent_count':2,'errors':[]}),approve_draft=lambda *a,**k:(approvals.append((a,k)) or {'status':'approved'}))
  with patch.object(ca,'now_et',return_value=at16),patch.object(ca,'gmail_subject_sent_any',return_value=False),patch.object(ca,'drive_source',return_value=({'id':'f'},raw,{'name':'x.docx'})):
   r=app.post('/api/lhos/automation/manual-send',headers={'x-lhos-automation-token':'secret'},json={'date':date,'confirm':f'SEND {date} LATE TO ACTIVE BETA TESTERS'})
  self.assertEqual(r.status_code,200);self.assertEqual(r.json()['action'],'manual_send_executed');self.assertEqual(len(calls),1);self.assertTrue(approvals[0][1]['manual_override'])
 def _gmail_message(self,mid,from_addr,subject,body,internal):
  enc=base64.urlsafe_b64encode(body.encode()).decode().rstrip('=')
  return {'id':mid,'internalDate':str(internal),'payload':{'headers':[{'name':'From','value':from_addr},{'name':'Subject','value':subject}],'mimeType':'text/plain','body':{'data':enc}}}
 def test_direct_authorized_inbox_approval_records_only(self):
  at8=ca.now_et().replace(hour=8,minute=0,second=0,microsecond=0);date=at8.strftime('%Y-%m-%d');state={'date':date,'date_display':'July 23, 2026','stage':'review_sent','content_valid':True,'draft_id':'old','subject':'S','review_subject':'[REVIEW] S','raw_content':'valid'};ca.atomic_json_write(ca.STATE_FILE,{date:state});approvals=[];app=self.app(approve_draft=lambda *a,**k:(approvals.append((a,k)) or {'status':'approved'}),initial_drafts={'old':{'id':'old','status':'pending_approval'}});msg=self._gmail_message('m1','Authorized <a@example.com>','Re: [REVIEW] S','Approved, please send the email.',1000)
  with patch.object(ca,'now_et',return_value=at8),patch.object(ca,'gmail_search',return_value=[{'id':'m1'}]),patch.object(ca,'gmail_get',return_value=msg):r=app.post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.status_code,200);self.assertEqual(r.json()['action'],'inbox_processed');self.assertEqual(len(approvals),1);self.assertEqual(ca.load(ca.STATE_FILE,{})[date]['stage'],'approved')
 def test_unauthorized_beta_email_cannot_approve(self):
  at8=ca.now_et().replace(hour=8,minute=0,second=0,microsecond=0);date=at8.strftime('%Y-%m-%d');ca.atomic_json_write(ca.STATE_FILE,{date:{'date':date,'date_display':'July 23, 2026','stage':'review_sent','content_valid':True,'draft_id':'old','subject':'S','review_subject':'[REVIEW] S','raw_content':'valid'}});approvals=[];app=self.app(approve_draft=lambda *a,**k:approvals.append(a),initial_drafts={'old':{'id':'old','status':'pending_approval'}});msg=self._gmail_message('u1','Stranger <stranger@example.com>','LifeHouse OS beta email','Approved send it',1000)
  with patch.object(ca,'now_et',return_value=at8),patch.object(ca,'gmail_search',return_value=[{'id':'u1'}]),patch.object(ca,'gmail_get',return_value=msg):r=app.post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(approvals,[]);self.assertEqual(r.json()['results'][0]['action'],'ignored_unauthorized')
 def test_conflicting_inbox_messages_process_oldest_then_newest(self):
  at8=ca.now_et().replace(hour=8,minute=0,second=0,microsecond=0);date=at8.strftime('%Y-%m-%d');ca.atomic_json_write(ca.STATE_FILE,{date:{'date':date,'date_display':'July 23, 2026','stage':'review_sent','content_valid':True,'draft_id':'old','subject':'S','review_subject':'[REVIEW] S','raw_content':'valid'}});app=self.app(approve_draft=lambda *a,**k:{'status':'approved'},initial_drafts={'old':{'id':'old','status':'pending_approval'}});msgs={'new':self._gmail_message('new','a@example.com','Re: [REVIEW] S',"Don't send it yet",2000),'old':self._gmail_message('old','a@example.com','Re: [REVIEW] S','Approved, send it',1000)}
  with patch.object(ca,'now_et',return_value=at8),patch.object(ca,'gmail_search',return_value=[{'id':'new'},{'id':'old'}]),patch.object(ca,'gmail_get',side_effect=lambda t,i:msgs[i]):r=app.post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.json()['processed_count'],2);self.assertEqual(ca.load(ca.STATE_FILE,{})[date]['stage'],'review_sent');self.assertEqual(self._drafts['old']['status'],'pending_approval')
 def test_sent_state_pauses_daily_polling(self):
  at8=ca.now_et().replace(hour=8,minute=0,second=0,microsecond=0);date=at8.strftime('%Y-%m-%d');ca.atomic_json_write(ca.STATE_FILE,{date:{'stage':'sent','draft_id':'done','content_valid':True}});app=self.app()
  with patch.object(ca,'now_et',return_value=at8),patch.object(ca,'gmail_search',side_effect=AssertionError('gmail touched')),patch.object(ca,'gmail_subject_sent_any',side_effect=AssertionError('sent search touched')),patch.object(ca,'drive_source',side_effect=AssertionError('drive touched')):
   a=app.post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'});b=app.post('/api/lhos/automation/prepare',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(a.json()['action'],'daily_complete');self.assertEqual(b.json()['action'],'daily_complete')
 def test_docx_attachment_extraction(self):
  xml=b'<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Daily Beta Content</w:t></w:r></w:p><w:p><w:r><w:t>Concrete update details</w:t></w:r></w:p></w:body></w:document>';buf=io.BytesIO()
  with zipfile.ZipFile(buf,'w') as z:z.writestr('word/document.xml',xml)
  self.assertEqual(ca.docx_text(buf.getvalue()),'Daily Beta Content\nConcrete update details')
if __name__=='__main__':unittest.main()
