import tempfile,unittest,base64,zipfile,io,json
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
import cloud_automation as ca
class CloudTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();root=Path(self.t.name);ca.STATE_FILE=root/'state.json';ca.PROCESSED_FILE=root/'processed.json';ca.ALERTS_FILE=root/'alerts.json';ca.HEARTBEAT_FILE=root/'heartbeat.json';ca.REPORTS_FILE=root/'reports.json';ca.SEND_POLICY='ON_APPROVAL';ca.AUTOMATION_LOCK=root/'automation.lock';ca.AUTOMATION_TOKEN='secret';ca.END_DATE='';ca.IMESSAGE_ENABLED=False;ca.ALERT_EMAIL='bobbyatf@gmail.com'
  self._gmail_search_patcher=patch.object(ca,'gmail_search',return_value=[]);self._gmail_search_patcher.start();self.addCleanup(self._gmail_search_patcher.stop)
 def tearDown(self):self.t.cleanup()
 def app(self,send_email=lambda *a:(_ for _ in ()).throw(AssertionError('send called')),send_draft=lambda *a:(_ for _ in ()).throw(AssertionError('send draft called')),approve_draft=lambda *a,**k:{'status':'approved'},initial_drafts=None,get_token=lambda:'tok'):
  app=FastAPI(); drafts=dict(initial_drafts or {}); self._drafts=drafts
  def create(s,h,t,d):
   did='id' if 'id' not in drafts else f'id{len(drafts)+1}';drafts[did]={'id':did,'subject':s,'html_body':h,'text_body':t,'date':d,'status':'pending_approval'};return {'draft_id':did}
  app.include_router(ca.configure_router(get_token=get_token,send_email=send_email,create_draft=create,load_drafts=lambda:drafts,save_drafts=lambda d:None,send_draft=send_draft,approve_draft=approve_draft,approvers=['a@example.com'],approval_senders=['a@example.com'],public_url='https://x',sender_email='iris@example.com',sender_name='Iris'));return TestClient(app)
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
 def test_watchdog_dry_run_does_not_record_heartbeat(self):
  at=ca.datetime(2030,1,1,10,0,tzinfo=ca.ET);date=at.strftime('%Y-%m-%d');fresh=at.isoformat();ca.atomic_json_write(ca.STATE_FILE,{date:{'stage':'hold','content_valid':False}});ca.atomic_json_write(ca.HEARTBEAT_FILE,{'prepare':fresh})
  with patch.object(ca,'now_et',return_value=at):
   r=self.app().post('/api/lhos/automation/watchdog?dry_run=true',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.status_code,200);self.assertNotIn('watchdog',ca.load(ca.HEARTBEAT_FILE,{}))
 def test_watchdog_dry_run_never_sends(self):
  with patch.object(ca,'now_et',return_value=ca.datetime(2030,1,1,16,0,tzinfo=ca.ET)):
   r=self.app().post('/api/lhos/automation/watchdog?dry_run=true',headers={'x-lhos-automation-token':'secret'})
   self.assertEqual(r.json()['action'],'would_alert_bobby')

 def test_auto_send_persists_no_source_before_google_notification_failure(self):
  at=ca.now_et().replace(hour=15,minute=0,second=0,microsecond=0);date=at.strftime('%Y-%m-%d')
  def dead_token():raise RuntimeError('invalid_grant')
  with patch.object(ca,'now_et',return_value=at):
   r=self.app(get_token=dead_token).post('/api/lhos/automation/auto-send',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.status_code,200);self.assertEqual(r.json()['action'],'not_sent')
  state=ca.load(ca.STATE_FILE,{})[date];self.assertEqual(state['stage'],'not_sent');self.assertFalse(state['content_valid']);self.assertEqual(r.json()['notification'],'failed')
 def test_reconcile_after_deadline_recovers_missing_terminal_state(self):
  at=ca.now_et().replace(hour=15,minute=2,second=0,microsecond=0);date=at.strftime('%Y-%m-%d')
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'gmail_subject_sent_any',return_value=True):
   r=self.app().post('/api/lhos/automation/reconcile',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.status_code,200);self.assertEqual(r.json()['action'],'not_sent');self.assertEqual(ca.load(ca.STATE_FILE,{})[date]['stage'],'not_sent')
 def test_reconcile_before_deadline_does_not_create_terminal_state(self):
  at=ca.now_et().replace(hour=14,minute=59,second=0,microsecond=0)
  with patch.object(ca,'now_et',return_value=at):
   r=self.app().post('/api/lhos/automation/reconcile',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.json()['action'],'no_state');self.assertEqual(ca.load(ca.STATE_FILE,{}),{})
 def test_watchdog_sources_have_separate_heartbeats(self):
  at=ca.datetime(2030,1,1,10,0,tzinfo=ca.ET);date=at.strftime('%Y-%m-%d');ca.atomic_json_write(ca.STATE_FILE,{date:{'stage':'hold','content_valid':False}});ca.atomic_json_write(ca.HEARTBEAT_FILE,{'prepare':at.isoformat()})
  with patch.object(ca,'now_et',return_value=at):
   r=self.app().post('/api/lhos/automation/watchdog?dry_run=true&source=core',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.status_code,200);hb=ca.load(ca.HEARTBEAT_FILE,{});self.assertNotIn('watchdog_core',hb);self.assertNotIn('watchdog_cloud',hb)




 def test_corrupt_state_fails_closed_without_overwrite_or_notification(self):
  at=ca.now_et().replace(hour=15,minute=2,second=0,microsecond=0);ca.STATE_FILE.write_text('{corrupt');sent=[]
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'gmail_subject_sent_any',return_value=False):r=self.app(send_email=lambda *a:sent.append(a)).post('/api/lhos/automation/reconcile',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.status_code,503);self.assertEqual(ca.STATE_FILE.read_text(),'{corrupt');self.assertEqual(sent,[]);self.assertEqual(r.json()['detail']['category'],'state_corrupt')

 def test_gate_heartbeat_distinguishes_started_from_completed(self):
  before=ca.now_et().replace(hour=14,minute=59,second=0,microsecond=0)
  with patch.object(ca,'now_et',return_value=before):r=self.app().post('/api/lhos/automation/auto-send',headers={'x-lhos-automation-token':'secret'})
  hb=ca.load(ca.HEARTBEAT_FILE,{});self.assertEqual(r.json()['action'],'too_early');self.assertIn('auto_send_started',hb);self.assertNotIn('auto_send',hb)
  at=before.replace(hour=15,minute=0)
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'gmail_subject_sent_any',return_value=True):r=self.app().post('/api/lhos/automation/auto-send',headers={'x-lhos-automation-token':'secret'})
  hb=ca.load(ca.HEARTBEAT_FILE,{});self.assertEqual(r.json()['action'],'not_sent');self.assertEqual(hb['auto_send'],at.isoformat())

 def test_auto_send_never_overwrites_partial_delivery(self):
  at=ca.now_et().replace(hour=15,minute=0,second=0,microsecond=0);date=at.strftime('%Y-%m-%d');state={"stage":"partial","content_valid":True,"draft_id":"id"};ca.atomic_json_write(ca.STATE_FILE,{date:state})
  with patch.object(ca,'now_et',return_value=at):r=self.app(initial_drafts={"id":{"status":"partial","approved_by":"Kristina"}}).post('/api/lhos/automation/auto-send',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.json()['action'],'reconciliation_pending');self.assertEqual(ca.load(ca.STATE_FILE,{})[date]['stage'],'partial')
 def test_three_pm_success_preserves_delivery_boundary_timestamps(self):
  at=ca.datetime(2030,1,2,15,0,tzinfo=ca.ET);date=at.strftime('%Y-%m-%d');state={'date':date,'date_display':'January 02, 2030','stage':'approved','content_valid':True,'draft_id':'id','subject':'Human','source':{'type':'drive'}};ca.atomic_json_write(ca.STATE_FILE,{date:state})
  with patch.object(ca,'now_et',return_value=at):r=self.app(send_draft=lambda *a:{'status':'sent'},initial_drafts={'id':{'id':'id','status':'approved','approved_by':'Kristina'}}).post('/api/lhos/automation/auto-send',headers={'x-lhos-automation-token':'secret'})
  persisted=ca.load(ca.STATE_FILE,{})[date];self.assertEqual(r.status_code,200);self.assertEqual(persisted['stage'],'sent');self.assertEqual(persisted['delivery_started_at'],at.isoformat());self.assertEqual(persisted['source_authority_locked_at'],at.isoformat())
 def test_recovered_not_sent_state_is_idempotent(self):
  at=ca.now_et().replace(hour=15,minute=2,second=0,microsecond=0);date=at.strftime('%Y-%m-%d');sent=[]
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'gmail_subject_sent_any',return_value=False):
   app=self.app(send_email=lambda *a:sent.append(a));first=app.post('/api/lhos/automation/reconcile',headers={'x-lhos-automation-token':'secret'});second=app.post('/api/lhos/automation/reconcile',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(first.json()['action'],'not_sent');self.assertEqual(second.json()['action'],'daily_complete');self.assertEqual(len(sent),1);self.assertEqual(ca.load(ca.STATE_FILE,{})[date]['stage'],'not_sent')

 def test_machine_token_revision_is_disabled(self):
  at8=ca.now_et().replace(hour=8,minute=0,second=0,microsecond=0);date=at8.strftime('%Y-%m-%d');raw='Daily Beta Notes\nA concrete beta update describes testing and feedback from users.\nWhat Changed\nThe dashboard has a revised navigation flow with clearer labels.\nHelpful Reminder\nPlease continue testing and report any specific issue.\nThank You\nThank you for the detailed feedback and continued beta participation.'
  ca.atomic_json_write(ca.STATE_FILE,{date:{'date':date,'date_display':'July 23, 2026','stage':'review_sent','content_valid':True,'draft_id':'old','subject':'S','review_subject':'[REVIEW] S','raw_content':raw}})
  sent=[];app=self.app(send_email=lambda *a:sent.append(a),initial_drafts={'old':{'id':'old','status':'pending_approval','text_body':raw}})
  with patch.object(ca,'now_et',return_value=at8),patch.object(ca,'revise_with_glm',return_value=raw.replace('clearer labels','clearer labels and revised colors')):
   r=app.post('/api/lhos/automation/decision',headers={'x-lhos-automation-token':'secret'},json={'actor':'Kristina','text':'change the labels','message_id':'m-revise','channel':'email'})
  self.assertEqual(r.status_code,410);self.assertEqual(self._drafts['old']['status'],'pending_approval');self.assertEqual(sent,[])
 def test_imessage_decision_channel_is_disabled(self):
  at8=ca.now_et().replace(hour=8,minute=0,second=0,microsecond=0)
  with patch.object(ca,'now_et',return_value=at8):
   r=self.app().post('/api/lhos/automation/decision',headers={'x-lhos-automation-token':'secret'},json={'actor':'Kristina','text':'approve','message_id':'m1','channel':'imessage'})
  self.assertEqual(r.status_code,410);self.assertIn('decision intake is disabled',r.json()['detail'])
 def test_missing_content_alert_goes_only_to_bobby(self):
  at8=ca.now_et().replace(hour=8,minute=0,second=0,microsecond=0);sent=[]
  with patch.object(ca,'now_et',return_value=at8),patch.object(ca,'drive_source',return_value=(None,'',{'name':None})),patch.object(ca,'gmail_subject_sent_any',return_value=False),patch.object(ca,'generate_fallback_bundle',side_effect=RuntimeError('generation failed')):
   r=self.app(send_email=lambda *a:sent.append(a)).post('/api/lhos/automation/prepare',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.json()['action'],'hold');self.assertEqual(len(sent),1);self.assertEqual(sent[0][1],'bobbyatf@gmail.com')
 def test_not_sent_alert_goes_only_to_bobby(self):
  at15=ca.now_et().replace(hour=15,minute=0,second=0,microsecond=0);date=at15.strftime('%Y-%m-%d');sent=[]
  ca.atomic_json_write(ca.STATE_FILE,{date:{'stage':'review_sent','content_valid':True,'draft_id':'id'}})
  with patch.object(ca,'now_et',return_value=at15),patch.object(ca,'gmail_subject_sent_any',return_value=False):
   r=self.app(send_email=lambda *a:sent.append(a),initial_drafts={'id':{'status':'pending_approval'}}).post('/api/lhos/automation/auto-send',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.json()['action'],'not_sent');self.assertEqual(len(sent),1);self.assertEqual(sent[0][1],'bobbyatf@gmail.com')
 def test_manual_late_send_endpoint_is_disabled(self):
  at16=ca.now_et().replace(hour=16,minute=0,second=0,microsecond=0)
  with patch.object(ca,'now_et',return_value=at16):
   r=self.app().post('/api/lhos/automation/manual-send',headers={'x-lhos-automation-token':'secret'},json={'date':at16.strftime('%Y-%m-%d'),'confirm':'send it'})
  self.assertEqual(r.status_code,410)
 def test_manual_late_send_dry_run_is_also_disabled(self):
  at16=ca.now_et().replace(hour=16,minute=0,second=0,microsecond=0);date=at16.strftime('%Y-%m-%d');raw='Daily Beta Notes\nA concrete beta update describes testing and feedback from users.\nWhat Changed\nThe dashboard has a revised navigation flow with clearer labels.\nHelpful Reminder\nPlease continue testing and report any specific issue.\nThank You\nThank you for the detailed feedback and continued beta participation.'
  with patch.object(ca,'now_et',return_value=at16),patch.object(ca,'gmail_subject_sent_any',return_value=False),patch.object(ca,'drive_source',return_value=({'id':'f'},raw,{'name':'x.docx'})):
   r=self.app().post('/api/lhos/automation/manual-send?dry_run=true',headers={'x-lhos-automation-token':'secret'},json={'date':date,'confirm':f'SEND {date} LATE TO ACTIVE BETA TESTERS'})
  self.assertEqual(r.status_code,410)
 def test_manual_late_send_cannot_call_delivery(self):
  at16=ca.now_et().replace(hour=16,minute=0,second=0,microsecond=0);date=at16.strftime('%Y-%m-%d');raw='Daily Beta Notes\nA concrete beta update describes testing and feedback from users.\nWhat Changed\nThe dashboard has a revised navigation flow with clearer labels.\nHelpful Reminder\nPlease continue testing and report any specific issue.\nThank You\nThank you for the detailed feedback and continued beta participation.';calls=[];approvals=[]
  app=self.app(send_draft=lambda *a:(calls.append(a) or {'status':'sent','recipient_count':2,'newly_sent_count':2,'errors':[]}),approve_draft=lambda *a,**k:(approvals.append((a,k)) or {'status':'approved'}))
  with patch.object(ca,'now_et',return_value=at16),patch.object(ca,'gmail_subject_sent_any',return_value=False),patch.object(ca,'drive_source',return_value=({'id':'f'},raw,{'name':'x.docx'})):
   r=app.post('/api/lhos/automation/manual-send',headers={'x-lhos-automation-token':'secret'},json={'date':date,'confirm':f'SEND {date} LATE TO ACTIVE BETA TESTERS'})
  self.assertEqual(r.status_code,410);self.assertEqual(calls,[]);self.assertEqual(approvals,[])
 def _gmail_message(self,mid,from_addr,subject,body,internal,auth='dkim=pass header.i=@example.com; spf=pass; dmarc=pass'):
  enc=base64.urlsafe_b64encode(body.encode()).decode().rstrip('=')
  hs=[{'name':'From','value':from_addr},{'name':'Subject','value':subject}]
  if auth:hs.append({'name':'Authentication-Results','value':'mx.google.com; '+auth})
  return {'id':mid,'internalDate':str(internal),'payload':{'headers':hs,'mimeType':'text/plain','body':{'data':enc}}}
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
 def test_watchdog_detects_missing_active_window_heartbeat(self):
  at8=ca.now_et().replace(hour=8,minute=0,second=0,microsecond=0);app=self.app()
  with patch.object(ca,'now_et',return_value=at8):r=app.post('/api/lhos/automation/watchdog?dry_run=true',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.json()['action'],'would_alert_bobby');self.assertIn('No daily cloud state',r.json()['reason'])
 def test_watchdog_accepts_fresh_active_heartbeat(self):
  at8=ca.now_et().replace(hour=8,minute=0,second=0,microsecond=0);date=at8.strftime('%Y-%m-%d');ca.atomic_json_write(ca.STATE_FILE,{date:{'stage':'review_sent','content_valid':True}});ca.atomic_json_write(ca.HEARTBEAT_FILE,{'prepare':at8.isoformat()});app=self.app()
  with patch.object(ca,'now_et',return_value=at8):r=app.post('/api/lhos/automation/watchdog?dry_run=true',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.json()['action'],'healthy_active_window')

 def _approval_env(self,at,body='Approved, send it to the beta testers.'):
  date=at.strftime('%Y-%m-%d')
  ca.atomic_json_write(ca.STATE_FILE,{date:{'date':date,'date_display':'July 24, 2026','stage':'review_sent','content_valid':True,'draft_id':'id','subject':'S','review_subject':'[REVIEW] X','raw_content':'body text'}})
  raw=base64.urlsafe_b64encode(body.encode()).decode()
  return date,{'internalDate':'1000','payload':{'headers':[{'name':'From','value':'a@example.com'},{'name':'Subject','value':'Re: [REVIEW] X'},{'name':'Authentication-Results','value':'mx.google.com; dkim=pass header.i=@example.com; spf=pass; dmarc=pass'}],'mimeType':'text/plain','body':{'data':raw}}}
 def test_on_approval_sends_immediately(self):
  at=ca.now_et().replace(hour=9,minute=0,second=0,microsecond=0);date,msg=self._approval_env(at);calls=[]
  drafts={'id':{'id':'id','subject':'S','html_body':'<p>x</p>','text_body':'t','date':'July 24, 2026','status':'approved'}}
  def sd(did,actor):calls.append((did,actor));return {'status':'sent','sent':27}
  app=self.app(send_email=lambda *a:None,send_draft=sd,initial_drafts=drafts)
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'SEND_POLICY','ON_APPROVAL'),patch.object(ca,'gmail_search',return_value=[{'id':'m1'}]),patch.object(ca,'gmail_get',return_value=msg):
   r=app.post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.status_code,200);acts=[a.get('action') for a in r.json().get('results',r.json() if isinstance(r.json(),list) else [])] if isinstance(r.json(),dict) else []
  self.assertEqual(len(calls),1,f'expected exactly one send, got {calls} :: {r.json()}');self.assertEqual(calls[0][0],'id')
 def test_at_gate_records_without_sending(self):
  at=ca.now_et().replace(hour=9,minute=0,second=0,microsecond=0);date,msg=self._approval_env(at)
  drafts={'id':{'id':'id','subject':'S','html_body':'<p>x</p>','text_body':'t','date':'July 24, 2026','status':'approved'}}
  app=self.app(send_email=lambda *a:None,initial_drafts=drafts)  # send_draft raises if called
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'SEND_POLICY','AT_GATE'),patch.object(ca,'gmail_search',return_value=[{'id':'m1'}]),patch.object(ca,'gmail_get',return_value=msg):
   r=app.post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.status_code,200)
 def test_on_approval_refuses_when_revision_changed_draft(self):
  at=ca.now_et().replace(hour=9,minute=0,second=0,microsecond=0);date,msg=self._approval_env(at)
  drafts={'id':{'id':'id','subject':'S','html_body':'<p>x</p>','text_body':'t','date':'July 24, 2026','status':'revised'}}
  app=self.app(send_email=lambda *a:None,initial_drafts=drafts,approve_draft=lambda *a,**k:{'status':'approved'})
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'SEND_POLICY','ON_APPROVAL'),patch.object(ca,'gmail_search',return_value=[{'id':'m1'}]),patch.object(ca,'gmail_get',return_value=msg):
   r=app.post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.status_code,200)
 def test_close_out_reports_incident_when_not_delivered(self):
  at=ca.now_et().replace(hour=15,minute=30,second=0,microsecond=0);date=at.strftime('%Y-%m-%d')
  ca.atomic_json_write(ca.STATE_FILE,{date:{'stage':'review_sent','content_valid':True,'draft_id':'id'}});sent=[]
  app=self.app(send_email=lambda *a:sent.append(a))
  with patch.object(ca,'now_et',return_value=at):r=app.post('/api/lhos/automation/close-out',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.json()['action'],'report_sent');self.assertTrue(r.json()['incident']);self.assertEqual(len(sent),1);self.assertEqual(sent[0][1],'bobbyatf@gmail.com')
 def test_close_out_is_sent_once_per_day(self):
  at=ca.now_et().replace(hour=15,minute=30,second=0,microsecond=0);date=at.strftime('%Y-%m-%d')
  ca.atomic_json_write(ca.STATE_FILE,{date:{'stage':'sent','content_valid':True,'draft_id':'id'}});sent=[]
  app=self.app(send_email=lambda *a:sent.append(a))
  with patch.object(ca,'now_et',return_value=at):
   a=app.post('/api/lhos/automation/close-out',headers={'x-lhos-automation-token':'secret'});b=app.post('/api/lhos/automation/close-out',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(a.json()['action'],'report_sent');self.assertEqual(b.json()['action'],'already_reported');self.assertEqual(len(sent),1)

 def test_close_out_flags_failed_closed_with_valid_content_as_incident(self):
  at=ca.now_et().replace(hour=16,minute=5,second=0,microsecond=0);date=at.strftime('%Y-%m-%d')
  ca.atomic_json_write(ca.STATE_FILE,{date:{'stage':'not_sent','content_valid':True,'draft_id':'id','not_sent_reason':'No approval by deadline.'}});sent=[]
  app=self.app(send_email=lambda *a:sent.append(a))
  with patch.object(ca,'now_et',return_value=at):r=app.post('/api/lhos/automation/close-out',headers={'x-lhos-automation-token':'secret'})
  self.assertTrue(r.json()['incident'],'valid content that never shipped must raise an incident')
 def test_close_out_no_incident_when_delivered(self):
  at=ca.now_et().replace(hour=16,minute=5,second=0,microsecond=0);date=at.strftime('%Y-%m-%d')
  ca.atomic_json_write(ca.STATE_FILE,{date:{'stage':'sent','content_valid':True,'draft_id':'id'}})
  app=self.app(send_email=lambda *a:None)
  with patch.object(ca,'now_et',return_value=at):r=app.post('/api/lhos/automation/close-out',headers={'x-lhos-automation-token':'secret'})
  self.assertFalse(r.json()['incident'])

 def _msg(self,frm,body,ar='dkim=pass header.i=@example.com; spf=pass; dmarc=pass',extra=None):
  hs=[{'name':'From','value':frm},{'name':'Subject','value':'Re: [REVIEW] X'}]
  if ar is not None:hs.append({'name':'Authentication-Results','value':'mx.google.com; '+ar})
  for k,v in (extra or {}).items():hs.append({'name':k,'value':v})
  return {'internalDate':'1000','payload':{'headers':hs,'mimeType':'text/plain','body':{'data':base64.urlsafe_b64encode(body.encode()).decode()}}}
 def _state(self,at):
  date=at.strftime('%Y-%m-%d')
  ca.atomic_json_write(ca.STATE_FILE,{date:{'date':date,'date_display':'July 24, 2026','stage':'review_sent','content_valid':True,'draft_id':'id','subject':'S','review_subject':'[REVIEW] X','raw_content':'body'}})
  return date
 def test_spoofed_from_cannot_approve(self):
  at=ca.now_et().replace(hour=9,minute=0,second=0,microsecond=0);self._state(at)
  # Correct allow-listed address, but authentication FAILS -> must never send
  msg=self._msg('a@example.com','Approved, send it.',ar='dkim=fail; spf=softfail; dmarc=fail')
  app=self.app(send_email=lambda *a:None,initial_drafts={'id':{'id':'id','status':'approved','subject':'S','html_body':'x','text_body':'t','date':'July 24, 2026'}})
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'gmail_search',return_value=[{'id':'sp1'}]),patch.object(ca,'gmail_get',return_value=msg):
   r=app.post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.status_code,200)
  txt=json.dumps(r.json());self.assertIn('failed_dmarc_authentication',txt)
 def test_missing_auth_headers_fails_closed(self):
  at=ca.now_et().replace(hour=9,minute=0,second=0,microsecond=0);self._state(at)
  msg=self._msg('a@example.com','Approved, send it.',ar=None)
  app=self.app(send_email=lambda *a:None,initial_drafts={'id':{'id':'id','status':'approved','subject':'S','html_body':'x','text_body':'t','date':'July 24, 2026'}})
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'gmail_search',return_value=[{'id':'na1'}]),patch.object(ca,'gmail_get',return_value=msg):
   r=app.post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'})
  self.assertIn('failed_dmarc_authentication',json.dumps(r.json()))
 def test_out_of_office_autoreply_cannot_approve(self):
  at=ca.now_et().replace(hour=9,minute=0,second=0,microsecond=0);self._state(at)
  msg=self._msg('a@example.com','Approved - automatic reply, I am away.',extra={'Auto-Submitted':'auto-replied'})
  app=self.app(send_email=lambda *a:None,initial_drafts={'id':{'id':'id','status':'approved','subject':'S','html_body':'x','text_body':'t','date':'July 24, 2026'}})
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'gmail_search',return_value=[{'id':'oo1'}]),patch.object(ca,'gmail_get',return_value=msg):
   r=app.post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'})
  self.assertIn('auto_reply',json.dumps(r.json()))
 def test_dkim_aligned_authenticates_without_dmarc_verdict(self):
  at=ca.now_et().replace(hour=9,minute=0,second=0,microsecond=0);self._state(at);calls=[]
  msg=self._msg('a@example.com','Approved, send it to beta testers.',ar='dkim=pass header.i=@example.com; spf=pass')
  app=self.app(send_email=lambda *a:None,send_draft=lambda d,a:(calls.append(d) or {'status':'sent','sent':27}),initial_drafts={'id':{'id':'id','status':'approved','subject':'S','html_body':'x','text_body':'t','date':'July 24, 2026'}})
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'SEND_POLICY','ON_APPROVAL'),patch.object(ca,'gmail_search',return_value=[{'id':'ok1'}]),patch.object(ca,'gmail_get',return_value=msg):
   r=app.post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(calls,['id'],f'aligned DKIM should authorize: {r.json()}')

 def _pl(self,*hs):return {'headers':[{'name':n,'value':v} for n,v in hs]}
 def test_header_injection_forged_auth_results_rejected(self):
  """Attacker appends their own Authentication-Results; Google's real verdict is topmost."""
  pay=self._pl(('Authentication-Results','mx.google.com; dkim=fail; spf=fail; dmarc=fail'),
               ('Authentication-Results','attacker.example; dkim=pass header.i=@example.com; dmarc=pass'))
  ok,v=ca.sender_authenticated('a@example.com',pay)
  self.assertFalse(ok,'topmost (real) Authentication-Results must win')
 def test_untrusted_authserv_rejected(self):
  pay=self._pl(('Authentication-Results','evil.example; dkim=pass header.i=@example.com; dmarc=pass'))
  ok,v=ca.sender_authenticated('a@example.com',pay)
  self.assertFalse(ok);self.assertEqual(v['basis'],'untrusted_authserv')
 def test_dkim_pass_for_attacker_domain_rejected(self):
  pay=self._pl(('Authentication-Results','mx.google.com; dkim=pass header.i=@attacker.com; spf=pass; dmarc=pass'))
  ok,_=ca.sender_authenticated('a@example.com',pay)
  self.assertFalse(ok,'DKIM must be ALIGNED to the From domain')
 def test_rfc3834_auto_submitted_detected(self):
  self.assertTrue(ca.is_auto_submitted(self._pl(('Auto-Submitted','auto-replied'))))
  self.assertTrue(ca.is_auto_submitted(self._pl(('Precedence','list'))))
  self.assertFalse(ca.is_auto_submitted(self._pl(('Auto-Submitted','no'))))
 def test_arc_results_are_never_trusted_without_verified_chain(self):
  pay=self._pl(('ARC-Authentication-Results','mx.google.com; dkim=pass header.i=@example.com; dmarc=pass'))
  ok,v=ca.sender_authenticated('a@example.com',pay)
  self.assertFalse(ok);self.assertEqual(v['basis'],'no_auth_results')
 def _fallback_bundle(self):
  raw=('Today’s Beta Notes\nUse one real household situation to test a clearer routine. '*5+'\nToday’s Beta Mission\nChoose one transition, try the steps, and share specific feedback. '*4+'\nThank You\nThank you for helping make household life more intentional.')
  return {'subject':'LifeHouse OS Daily Briefing - January 02, 2030','intro':'Today combines a practical household system with a focused beta mission.','sections':[{'title':'Today’s Beta Notes','body':'Small tests can reveal useful improvements for everyday household life.'},{'title':'Today’s Beta Mission','body':'Choose one real household transition and note what feels clear, where you hesitate, and what would help.'},{'title':'A Household Win','body':'List what must happen today, what would be helpful, and what can safely wait.'},{'title':'A Question for Your House','body':'Which household transition creates the most friction right now?'},{'title':'Thank You','body':'Thank you for testing thoughtfully and sharing specific feedback.'}],'raw':raw,'topic_id':'work-home-handoff','generator':'test'}
 def test_missing_source_waits_until_730_without_email(self):
  at=ca.datetime(2030,1,2,7,29,tzinfo=ca.ET);sent=[]
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'drive_source',return_value=(None,'',{'name':'300102.docx','missing':True})),patch.object(ca,'gmail_subject_sent_any',return_value=False),patch.object(ca,'generate_fallback_bundle',side_effect=AssertionError('too early')):
   r=self.app(send_email=lambda *a:sent.append(a)).post('/api/lhos/automation/prepare',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.json()['action'],'awaiting_iris_fallback');self.assertEqual(sent,[]);self.assertEqual(ca.load(ca.STATE_FILE,{})['2030-01-02']['stage'],'hold')
 def test_missing_source_generates_one_review_at_730(self):
  at=ca.datetime(2030,1,2,7,30,tzinfo=ca.ET);sent=[];bundle=self._fallback_bundle()
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'drive_source',return_value=(None,'',{'name':'300102.docx','missing':True})),patch.object(ca,'gmail_subject_sent_any',return_value=False),patch.object(ca,'generate_fallback_bundle',return_value=bundle):
   app=self.app(send_email=lambda *a:sent.append(a));first=app.post('/api/lhos/automation/prepare',headers={'x-lhos-automation-token':'secret'});second=app.post('/api/lhos/automation/prepare',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(first.json()['action'],'review_sent');self.assertEqual(second.json()['action'],'no_op');self.assertEqual(len(sent),1);state=ca.load(ca.STATE_FILE,{})['2030-01-02'];self.assertEqual(state['source']['type'],'iris_generated');self.assertEqual(state['stage'],'review_sent');self.assertTrue(state['content_valid'])
 def test_valid_drive_content_always_beats_fallback(self):
  at=ca.datetime(2030,1,2,7,45,tzinfo=ca.ET);raw=('Today we fixed the mobile dashboard issue and added a concrete feature for beta testing and feedback. '*5)
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'drive_source',return_value=({'id':'f'},raw,{'name':'300102.docx'})),patch.object(ca,'gmail_subject_sent_any',return_value=False),patch.object(ca,'generate_fallback_bundle',side_effect=AssertionError('human source must win')):
   r=self.app(send_email=lambda *a:None).post('/api/lhos/automation/prepare',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.json()['action'],'review_sent');self.assertNotEqual(ca.load(ca.STATE_FILE,{})['2030-01-02']['source'].get('type'),'iris_generated')
 def test_late_drive_source_replaces_unapproved_fallback(self):
  at=ca.datetime(2030,1,2,7,30,tzinfo=ca.ET);bundle=self._fallback_bundle();raw=('Today we fixed the mobile dashboard issue and added a concrete feature for beta testing and feedback. '*5);sources=[(None,'',{'name':'300102.docx','missing':True}),({'id':'f'},raw,{'name':'300102.docx'})];sent=[]
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'drive_source',side_effect=sources),patch.object(ca,'gmail_subject_sent_any',return_value=False),patch.object(ca,'generate_fallback_bundle',return_value=bundle):
   app=self.app(send_email=lambda *a:sent.append(a));a=app.post('/api/lhos/automation/prepare',headers={'x-lhos-automation-token':'secret'});b=app.post('/api/lhos/automation/prepare',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(a.json()['action'],'review_sent');self.assertEqual(b.json()['action'],'review_sent');state=ca.load(ca.STATE_FILE,{})['2030-01-02'];self.assertEqual(state['source']['name'],'300102.docx');self.assertEqual(len(sent),2);self.assertEqual(self._drafts['id']['status'],'revised')
 def test_fallback_generation_failure_alerts_only_bobby(self):
  at=ca.datetime(2030,1,2,7,30,tzinfo=ca.ET);sent=[]
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'drive_source',return_value=(None,'',{'name':'300102.docx','missing':True})),patch.object(ca,'gmail_subject_sent_any',return_value=False),patch.object(ca,'generate_fallback_bundle',side_effect=RuntimeError('provider unavailable')):
   r=self.app(send_email=lambda *a:sent.append(a)).post('/api/lhos/automation/prepare',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.json()['action'],'hold');self.assertEqual(len(sent),1);self.assertEqual(sent[0][1],'bobbyatf@gmail.com');self.assertNotIn('provider unavailable',str(sent[0]))
 def test_authenticated_short_reference_is_saved_for_730_generation(self):
  at=ca.datetime(2030,1,2,7,20,tzinfo=ca.ET);date=at.strftime('%Y-%m-%d');ca.atomic_json_write(ca.STATE_FILE,{date:{'date':date,'date_display':'January 02, 2030','stage':'hold','content_valid':False,'source':{'missing':True}}});msg=self._gmail_message('ref1','a@example.com','LifeHouse OS daily briefing reference','Please use travel preparation and a smoother return home as today’s reference.',1000);sent=[]
  app=self.app(send_email=lambda *a:sent.append(a))
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'gmail_search',return_value=[{'id':'ref1'}]),patch.object(ca,'gmail_get',return_value=msg):
   r=app.post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'})
  at_next=at.replace(minute=21)
  with patch.object(ca,'now_et',return_value=at_next),patch.object(ca,'drive_source',return_value=(None,'',{'name':'300102.docx','missing':True})),patch.object(ca,'gmail_subject_sent_any',return_value=False):
   polled=app.post('/api/lhos/automation/prepare',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.json()['results'][0]['action'],'reference_recorded');self.assertEqual(polled.json()['action'],'awaiting_iris_fallback');state=ca.load(ca.STATE_FILE,{})[date];self.assertIn('travel preparation',state['reference_content']);self.assertEqual(sent,[])
 def test_730_dry_run_with_no_source_never_calls_creative_provider(self):
  at=ca.datetime(2030,1,2,7,30,tzinfo=ca.ET)
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'drive_source',return_value=(None,'',{'missing':True})),patch.object(ca,'gmail_subject_sent_any',return_value=False),patch.object(ca,'generate_fallback_bundle',side_effect=AssertionError('dry run called creative provider')):
   r=self.app().post('/api/lhos/automation/prepare?dry_run=true',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.status_code,200);self.assertEqual(r.json()['action'],'would_generate_iris_fallback');self.assertFalse(ca.STATE_FILE.exists())
 def test_730_creative_fallback_creates_review_only_with_provenance(self):
  at=ca.datetime(2030,1,2,7,30,tzinfo=ca.ET);date=at.strftime('%Y-%m-%d');bundle=self._fallback_bundle();bundle.update({'generator':'iris-creative-v1','creative_model':'glm-4.7-flash','creative_attempted':True,'subject':'LifeHouse OS Daily Briefing — An Original Household Experiment'});reviews=[]
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'drive_source',return_value=(None,'',{'missing':True})),patch.object(ca,'gmail_subject_sent_any',return_value=False),patch.object(ca,'generate_fallback_bundle',return_value=bundle):
   r=self.app(send_email=lambda *a:reviews.append(a)).post('/api/lhos/automation/prepare',headers={'x-lhos-automation-token':'secret'})
  state=ca.load(ca.STATE_FILE,{})[date];self.assertEqual(r.json()['action'],'review_sent');self.assertEqual(state['stage'],'review_sent');self.assertEqual(state['source']['generator'],'iris-creative-v1');self.assertEqual(state['source']['creative_model'],'glm-4.7-flash');self.assertEqual(len(reviews),1);self.assertIn('[REVIEW] LifeHouse OS Iris Fallback Draft',reviews[0][2]);self.assertEqual(self._drafts['id']['status'],'pending_approval')
 def test_generated_approval_rechecks_drive_and_human_source_wins(self):
  at=ca.datetime(2030,1,2,9,0,tzinfo=ca.ET);date=at.strftime('%Y-%m-%d');generated=self._fallback_bundle()['raw'];state={'date':date,'date_display':'January 02, 2030','stage':'review_sent','content_valid':True,'draft_id':'id','subject':'Generated','review_subject':'[REVIEW] Fallback','raw_content':generated,'source':{'type':'iris_generated'}};ca.atomic_json_write(ca.STATE_FILE,{date:state});raw=('Today we fixed the mobile dashboard issue and added a concrete feature for beta testing and feedback. '*5);msg=self._gmail_message('approve1','a@example.com','Re: [REVIEW] Fallback','Approved, send it.',1000);calls=[];sent=[]
  app=self.app(send_email=lambda *a:sent.append(a),send_draft=lambda *a:calls.append(a),initial_drafts={'id':{'id':'id','status':'pending_approval','subject':'Generated','html_body':'x','text_body':generated,'date':'January 02, 2030'}})
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'gmail_search',return_value=[{'id':'approve1'}]),patch.object(ca,'gmail_get',return_value=msg),patch.object(ca,'drive_source',return_value=({'id':'f'},raw,{'name':'300102.docx'})),patch.object(ca,'gmail_subject_sent_any',return_value=False):
   r=app.post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(calls,[]);result=r.json()['results'][0];self.assertEqual(result['action'],'review_sent');self.assertEqual(ca.load(ca.STATE_FILE,{})[date]['source']['name'],'300102.docx');self.assertEqual(self._drafts['id']['status'],'revised');self.assertEqual(len(sent),1)
 def test_at_gate_generated_fallback_rechecks_human_source_before_send(self):
  at=ca.datetime(2030,1,2,15,0,tzinfo=ca.ET);date=at.strftime('%Y-%m-%d');raw=('Today we fixed the mobile dashboard issue and added a concrete feature for beta testing and feedback. '*5);state={'date':date,'date_display':'January 02, 2030','stage':'approved','content_valid':True,'draft_id':'id','subject':'Generated','source':{'type':'iris_generated'}};ca.atomic_json_write(ca.STATE_FILE,{date:state});sent=[]
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'drive_source',return_value=({'id':'f'},raw,{'name':'300102.docx'})),patch.object(ca,'gmail_subject_sent_any',return_value=False):
   r=self.app(send_email=lambda *a:sent.append(a),initial_drafts={'id':{'id':'id','status':'approved','approved_by':'Kristina'}}).post('/api/lhos/automation/auto-send',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.json()['action'],'not_sent');self.assertIn('requires a new review',r.json()['reason']);self.assertEqual(ca.load(ca.STATE_FILE,{})[date]['stage'],'not_sent');self.assertEqual(sent[0][1],'bobbyatf@gmail.com')
 def test_human_source_appearing_between_approval_checks_revokes_send(self):
  at=ca.datetime(2030,1,2,9,0,tzinfo=ca.ET);date=at.strftime('%Y-%m-%d');generated=self._fallback_bundle()['raw'];review='[REVIEW] Fallback [draft:id]'
  ca.atomic_json_write(ca.STATE_FILE,{date:{'date':date,'date_display':'January 02, 2030','stage':'review_sent','content_valid':True,'draft_id':'id','subject':'Generated','review_subject':review,'raw_content':generated,'source':{'type':'iris_generated'}}})
  valid=('Today we fixed the household dashboard and provided concrete beta testing guidance and feedback steps. '*6);msg=self._gmail_message('approve-race','a@example.com','Re: '+review,'Approved',1000);beta=[];reviews=[]
  def approve(did,actor):self._drafts[did].update({'status':'approved','approved_by':actor});return {'status':'approved'}
  app=self.app(send_email=lambda *a:reviews.append(a),send_draft=lambda *a:beta.append(a),approve_draft=approve,initial_drafts={'id':{'id':'id','status':'pending_approval','subject':'Generated','html_body':'x','text_body':generated,'date':'January 02, 2030'}})
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'gmail_search',return_value=[{'id':'approve-race'}]),patch.object(ca,'gmail_get',return_value=msg),patch.object(ca,'drive_source',side_effect=[(None,'',{'missing':True}),({'id':'f'},valid,{'id':'f','name':'300102.docx'})]),patch.object(ca,'gmail_subject_sent_any',return_value=False):
   r=app.post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(beta,[]);self.assertEqual(r.json()['results'][0]['action'],'review_sent');self.assertEqual(ca.load(ca.STATE_FILE,{})[date]['source']['name'],'300102.docx');self.assertEqual(len(reviews),1)
 def test_human_source_at_durable_delivery_start_revokes_send(self):
  at=ca.datetime(2030,1,2,9,0,tzinfo=ca.ET);date=at.strftime('%Y-%m-%d');generated=self._fallback_bundle()['raw'];review='[REVIEW] Fallback [draft:id]';ca.atomic_json_write(ca.STATE_FILE,{date:{'date':date,'date_display':'January 02, 2030','stage':'review_sent','content_valid':True,'draft_id':'id','subject':'Generated','review_subject':review,'raw_content':generated,'source':{'type':'iris_generated'}}});valid=('Today we fixed the household dashboard and provided concrete beta testing guidance and feedback steps. '*6);msg=self._gmail_message('approve-race-3','a@example.com','Re: '+review,'Approved',1000);beta=[];reviews=[]
  def approve(did,actor):self._drafts[did].update({'status':'approved','approved_by':actor});return {'status':'approved'}
  app=self.app(send_email=lambda *a:reviews.append(a),send_draft=lambda *a:(beta.append(a) or {'status':'sent'}),approve_draft=approve,initial_drafts={'id':{'id':'id','status':'pending_approval','subject':'Generated','html_body':'x','text_body':generated,'date':'January 02, 2030'}})
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'gmail_search',return_value=[{'id':'approve-race-3'}]),patch.object(ca,'gmail_get',return_value=msg),patch.object(ca,'drive_source',side_effect=[(None,'',{'missing':True}),(None,'',{'missing':True}),({'id':'f'},valid,{'id':'f','name':'300102.docx'})]),patch.object(ca,'gmail_subject_sent_any',return_value=False):r=app.post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(beta,[]);self.assertEqual(r.json()['results'][0]['action'],'review_sent');self.assertEqual(ca.load(ca.STATE_FILE,{})[date]['source']['name'],'300102.docx')
 def test_gate_human_source_at_durable_delivery_start_blocks_send(self):
  at=ca.datetime(2030,1,2,15,0,tzinfo=ca.ET);date=at.strftime('%Y-%m-%d');valid=('Today we fixed the household dashboard and provided concrete beta testing guidance and feedback steps. '*6);state={'date':date,'date_display':'January 02, 2030','stage':'approved','content_valid':True,'draft_id':'id','subject':'Generated','source':{'type':'iris_generated'}};ca.atomic_json_write(ca.STATE_FILE,{date:state});beta=[];alerts=[]
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'drive_source',side_effect=[(None,'',{'missing':True}),(None,'',{'missing':True}),({'id':'f'},valid,{'id':'f','name':'300102.docx'})]),patch.object(ca,'gmail_subject_sent_any',return_value=False):r=self.app(send_email=lambda *a:alerts.append(a),send_draft=lambda *a:(beta.append(a) or {'status':'sent'}),initial_drafts={'id':{'id':'id','status':'approved','approved_by':'Kristina'}}).post('/api/lhos/automation/auto-send',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(beta,[]);self.assertEqual(r.json()['action'],'not_sent');self.assertEqual(ca.load(ca.STATE_FILE,{})[date]['stage'],'not_sent')
 def test_reconcile_rechecks_source_after_durable_delivery_transition(self):
  at=ca.datetime(2030,1,2,9,0,tzinfo=ca.ET);date=at.strftime('%Y-%m-%d');valid=('Today we fixed the household dashboard and provided concrete beta testing guidance and feedback steps. '*6);state={'date':date,'date_display':'January 02, 2030','stage':'sending','delivery_started_at':at.isoformat(),'source_authority_locked_at':at.isoformat(),'content_valid':True,'draft_id':'id','subject':'Generated','source':{'type':'iris_generated'}};ca.atomic_json_write(ca.STATE_FILE,{date:state});beta=[];reviews=[]
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'drive_source',return_value=({'id':'f'},valid,{'id':'f','name':'300102.docx'})),patch.object(ca,'gmail_subject_sent_any',return_value=False):r=self.app(send_email=lambda *a:reviews.append(a),send_draft=lambda *a:(beta.append(a) or {'status':'sent'}),initial_drafts={'id':{'id':'id','status':'approved','approved_by':'Kristina','subject':'Generated','html_body':'x','text_body':'x','date':'January 02, 2030'}}).post('/api/lhos/automation/reconcile',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(beta,[]);self.assertEqual(r.json()['action'],'review_sent');self.assertEqual(ca.load(ca.STATE_FILE,{})[date]['source']['name'],'300102.docx');self.assertEqual(len(reviews),1)
if __name__=='__main__':unittest.main()
