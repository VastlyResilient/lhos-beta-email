import tempfile,unittest,base64,zipfile,io,json
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
import cloud_automation as ca
class CloudTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();root=Path(self.t.name);ca.STATE_FILE=root/'state.json';ca.PROCESSED_FILE=root/'processed.json';ca.INBOX_FILE=root/'inbox.json';ca.ALERTS_FILE=root/'alerts.json';ca.HEARTBEAT_FILE=root/'heartbeat.json';ca.REPORTS_FILE=root/'reports.json';ca.SEND_POLICY='ON_APPROVAL';ca.AUTOMATION_LOCK=root/'automation.lock';ca.AUTOMATION_TOKEN='secret';ca.END_DATE='';ca.INBOX_AGENT_START_DATE='1969-01-01';ca.INBOX_CONTEXT_SINCE='1970-01-01';ca.IMESSAGE_ENABLED=False;ca.ALERT_EMAIL='bobbyatf@gmail.com'
  self._gmail_search_patcher=patch.object(ca,'gmail_search',return_value=[]);self._gmail_search_patcher.start();self.addCleanup(self._gmail_search_patcher.stop);self._thread_patcher=patch.object(ca,'gmail_thread_reply_sent',return_value=False);self._thread_patcher.start();self.addCleanup(self._thread_patcher.stop)
 def tearDown(self):self.t.cleanup()
 def app(self,send_email=lambda *a:(_ for _ in ()).throw(AssertionError('send called')),reply_email=lambda *a:None,send_draft=lambda *a:(_ for _ in ()).throw(AssertionError('send draft called')),approve_draft=lambda *a,**k:{'status':'approved'},initial_drafts=None,get_token=lambda:'tok'):
  app=FastAPI(); drafts=dict(initial_drafts or {}); self._drafts=drafts
  def create(s,h,t,d):
   did='id' if 'id' not in drafts else f'id{len(drafts)+1}';drafts[did]={'id':did,'subject':s,'html_body':h,'text_body':t,'date':d,'status':'pending_approval'};return {'draft_id':did}
  app.include_router(ca.configure_router(get_token=get_token,send_email=send_email,reply_email=reply_email,create_draft=create,load_drafts=lambda:drafts,save_drafts=lambda d:None,send_draft=send_draft,approve_draft=approve_draft,approvers=['a@example.com'],approval_senders=['a@example.com'],public_url='https://x',sender_email='iris@example.com',sender_name='Iris'));return TestClient(app)
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
  hs=[{'name':'From','value':from_addr},{'name':'To','value':'iris@example.com'},{'name':'Subject','value':subject},{'name':'Message-ID','value':f'<{mid}@example.com>'}]
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
 def test_sent_state_still_polls_direct_inbox_but_prepare_pauses(self):
  at8=ca.now_et().replace(hour=8,minute=0,second=0,microsecond=0);date=at8.strftime('%Y-%m-%d');ca.atomic_json_write(ca.STATE_FILE,{date:{'stage':'sent','draft_id':'done','content_valid':True}});app=self.app()
  with patch.object(ca,'now_et',return_value=at8),patch.object(ca,'gmail_search',return_value=[]),patch.object(ca,'gmail_subject_sent_any',side_effect=AssertionError('sent search touched')),patch.object(ca,'drive_source',side_effect=AssertionError('drive touched')):
   a=app.post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'});b=app.post('/api/lhos/automation/prepare',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(a.json()['action'],'no_relevant_inbox');self.assertEqual(b.json()['action'],'daily_complete')
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
  return date,{'internalDate':'1000','payload':{'headers':[{'name':'From','value':'a@example.com'},{'name':'To','value':'iris@example.com'},{'name':'Subject','value':'Re: [REVIEW] X'},{'name':'Message-ID','value':'<m1@example.com>'},{'name':'Authentication-Results','value':'mx.google.com; dkim=pass header.i=@example.com; spf=pass; dmarc=pass'}],'mimeType':'text/plain','body':{'data':raw}}}
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
  hs=[{'name':'From','value':frm},{'name':'To','value':'iris@example.com'},{'name':'Subject','value':'Re: [REVIEW] X'},{'name':'Message-ID','value':'<m@example.com>'}]
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
 def test_direct_complete_content_creates_review_replies_and_deduplicates(self):
  at=ca.datetime(2030,1,2,8,0,tzinfo=ca.ET);date=at.strftime('%Y-%m-%d');stamp=int(at.timestamp()*1000);ca.atomic_json_write(ca.STATE_FILE,{date:{'date':date,'date_display':'January 02, 2030','stage':'hold','content_valid':False}})
  body=('Today’s Notes\nBeta Sprint 1 survey feedback remains the current priority. We are scheduling one-on-one conversations and preparing the next beta sprint. '*4+'\nThank You\nThank you for testing and sharing detailed feedback.')
  msg=self._gmail_message('content-1','a@example.com','LifeHouse OS content for today',body,stamp);reviews=[];replies=[];app=self.app(send_email=lambda *a:reviews.append(a),reply_email=lambda *a:replies.append(a))
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'gmail_search',return_value=[{'id':'content-1'}]),patch.object(ca,'gmail_get',return_value=msg),patch.object(ca,'gmail_subject_sent_any',return_value=False):
   first=app.post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'});second=app.post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(first.json()['results'][0]['action'],'review_sent');self.assertEqual(len(reviews),1);self.assertEqual(reviews[0][1],'a@example.com');self.assertEqual(len(replies),1);self.assertEqual(replies[0][1],'a@example.com');self.assertEqual(second.json()['action'],'no_relevant_inbox');self.assertEqual(ca.load(ca.STATE_FILE,{})[date]['source']['message_id'],'content-1');self.assertEqual(ca.load(ca.INBOX_FILE,{})['messages']['content-1']['status'],'replied')
 def test_thomas_style_standing_hold_persists_and_blocks_prepare(self):
  at=ca.datetime(2030,1,2,8,0,tzinfo=ca.ET);date=at.strftime('%Y-%m-%d');stamp=int(at.timestamp()*1000);raw=('Today we fixed the dashboard issue and added concrete beta testing guidance and feedback steps. '*5);review='[REVIEW] Daily [draft:id]';ca.atomic_json_write(ca.STATE_FILE,{date:{'date':date,'date_display':'January 02, 2030','stage':'review_sent','content_valid':True,'draft_id':'id','review_subject':review,'raw_content':raw}})
  msg=self._gmail_message('hold-1','a@example.com','Re: '+review,'Please stop these emails until the next beta sprint. The current beta sprint has stopped. Please respond.',stamp);replies=[];app=self.app(reply_email=lambda *a:replies.append(a),initial_drafts={'id':{'id':'id','status':'pending_approval','text_body':raw}})
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'gmail_search',return_value=[{'id':'hold-1'}]),patch.object(ca,'gmail_get',return_value=msg):first=app.post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'})
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'drive_source',side_effect=AssertionError('standing hold must block source access')):second=app.post('/api/lhos/automation/prepare',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(first.json()['results'][0]['action'],'send_held');self.assertTrue(ca.load(ca.INBOX_FILE,{})['standing_hold']['active']);self.assertEqual(second.json()['action'],'held_for_standing_approver_instruction');self.assertEqual(len(replies),1)
 def test_unbound_approval_gets_clarification_but_never_approves(self):
  at=ca.datetime(2030,1,2,9,0,tzinfo=ca.ET);date=at.strftime('%Y-%m-%d');stamp=int(at.timestamp()*1000);raw=('Today we fixed the dashboard issue and added concrete beta testing guidance and feedback steps. '*5);ca.atomic_json_write(ca.STATE_FILE,{date:{'date':date,'date_display':'January 02, 2030','stage':'review_sent','content_valid':True,'draft_id':'id','review_subject':'[REVIEW] Exact [draft:id]','raw_content':raw}});msg=self._gmail_message('unbound-1','a@example.com','LifeHouse OS general note','Approved',stamp);approvals=[];replies=[]
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'gmail_search',return_value=[{'id':'unbound-1'}]),patch.object(ca,'gmail_get',return_value=msg):r=self.app(reply_email=lambda *a:replies.append(a),approve_draft=lambda *a,**k:approvals.append(a),initial_drafts={'id':{'id':'id','status':'pending_approval'}}).post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.json()['results'][0]['action'],'clarification_needed');self.assertEqual(approvals,[]);self.assertEqual(len(replies),1);self.assertIn('exact current review',replies[0][3])
 def test_sent_day_still_answers_direct_question_without_new_delivery(self):
  at=ca.datetime(2030,1,2,10,0,tzinfo=ca.ET);date=at.strftime('%Y-%m-%d');stamp=int(at.timestamp()*1000);ca.atomic_json_write(ca.STATE_FILE,{date:{'stage':'sent','draft_id':'done','content_valid':True}});msg=self._gmail_message('question-1','a@example.com','Tomorrow’s LifeHouse OS briefing','Can tomorrow’s briefing focus on the survey and one-on-one conversations? Please respond.',stamp);replies=[]
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'gmail_search',return_value=[{'id':'question-1'}]),patch.object(ca,'gmail_get',return_value=msg):r=self.app(reply_email=lambda *a:replies.append(a)).post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.json()['results'][0]['action'],'context_recorded');self.assertEqual(ca.load(ca.STATE_FILE,{})[date]['stage'],'sent');self.assertEqual(len(replies),1);self.assertIn('recorded',replies[0][3])
 def test_agent_activation_gate_touches_no_gmail_or_inbox(self):
  at=ca.datetime(2030,1,2,8,0,tzinfo=ca.ET);ca.INBOX_AGENT_START_DATE='2030-01-03'
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'gmail_search',side_effect=AssertionError('gmail touched')):r=self.app().post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.json()['action'],'inbox_agent_not_active');self.assertFalse(ca.INBOX_FILE.exists())
 def test_direct_inbox_dry_run_has_no_side_effects(self):
  at=ca.datetime(2030,1,2,9,0,tzinfo=ca.ET);date=at.strftime('%Y-%m-%d');stamp=int(at.timestamp()*1000);ca.atomic_json_write(ca.STATE_FILE,{date:{'stage':'hold','content_valid':False}});ca.atomic_json_write(ca.PROCESSED_FILE,[]);ca.atomic_json_write(ca.INBOX_FILE,{'version':1,'messages':{},'context':[],'standing_hold':None});before={p:p.read_bytes() for p in (ca.STATE_FILE,ca.PROCESSED_FILE,ca.INBOX_FILE)};msg=self._gmail_message('dry-1','a@example.com','LifeHouse OS question','Can the next beta email focus on the survey?',stamp);replies=[]
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'gmail_search',return_value=[{'id':'dry-1'}]),patch.object(ca,'gmail_get',return_value=msg):r=self.app(reply_email=lambda *a:replies.append(a)).post('/api/lhos/automation/check-replies?dry_run=true',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.json()['action'],'would_process_direct_approver_email');self.assertEqual(replies,[]);self.assertEqual({p:p.read_bytes() for p in before},before)
 def test_historical_context_is_imported_and_acknowledged_without_explicit_request(self):
  at=ca.datetime(2030,1,2,8,0,tzinfo=ca.ET);old=ca.datetime(2030,1,1,14,0,tzinfo=ca.ET);ca.INBOX_AGENT_START_DATE='2030-01-02';msg=self._gmail_message('old-1','a@example.com','LifeHouse OS beta status','The beta sprint has stopped. Future emails must focus on surveys and one-on-one conversations.',int(old.timestamp()*1000));replies=[]
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'gmail_search',return_value=[{'id':'old-1'}]),patch.object(ca,'gmail_get',return_value=msg):r=self.app(reply_email=lambda *a:replies.append(a)).post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.json()['results'][0]['action'],'historical_context_imported');self.assertEqual(len(replies),1);data=ca.load(ca.INBOX_FILE,{});self.assertIsNone(data.get('standing_hold'));self.assertIn('beta sprint has stopped',data['context'][0]['body'].lower())
 def test_730_fallback_is_rewritten_from_authenticated_durable_context(self):
  at=ca.datetime(2030,1,2,7,30,tzinfo=ca.ET);date=at.strftime('%Y-%m-%d');context={'version':1,'messages':{},'standing_hold':None,'context':[{'message_id':'ctx-1','received_date':'2030-01-01','from':'a@example.com','subject':'Beta direction','intent':'context','body':'The current priorities are the survey, one-on-one conversations, and the upcoming beta sprint.'}]};ca.atomic_json_write(ca.INBOX_FILE,context);bundle=self._fallback_bundle();revised=('Today’s Beta Notes\nThe current beta priority is survey feedback and scheduling one-on-one conversations. The next sprint remains upcoming. '*5+'\nThank You\nThank you for testing and sharing specific feedback.');reviews=[]
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'drive_source',return_value=(None,'',{'missing':True})),patch.object(ca,'gmail_subject_sent_any',return_value=False),patch.object(ca,'generate_fallback_bundle',return_value=bundle),patch.object(ca,'revise_with_glm',return_value=revised) as revise:
   r=self.app(send_email=lambda *a:reviews.append(a)).post('/api/lhos/automation/prepare',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.json()['action'],'review_sent');self.assertIn('current priorities',revise.call_args.args[1]);self.assertEqual(ca.load(ca.STATE_FILE,{})[date]['source']['type'],'iris_generated');self.assertEqual(ca.load(ca.STATE_FILE,{})[date]['source']['generation_mode'],'authenticated_inbox_context');self.assertEqual(len(reviews),1)
 def test_temporary_revision_failure_replies_once_and_retries_action(self):
  at=ca.datetime(2030,1,2,9,0,tzinfo=ca.ET);date=at.strftime('%Y-%m-%d');stamp=int(at.timestamp()*1000);raw=('Today we fixed the dashboard issue and added concrete beta testing guidance and feedback steps. '*5);review='[REVIEW] Daily [draft:id]';ca.atomic_json_write(ca.STATE_FILE,{date:{'date':date,'date_display':'January 02, 2030','stage':'review_sent','content_valid':True,'draft_id':'id','review_subject':review,'raw_content':raw}});msg=self._gmail_message('retry-1','a@example.com','Re: '+review,'Please change the briefing to focus on the survey and one-on-one conversations.',stamp);replies=[];reviews=[];revised=('Today’s Beta Notes\nPlease complete the survey and share feedback during a one-on-one conversation. '*6+'\nThank You\nThank you for testing and sharing detailed feedback.')
  app=self.app(send_email=lambda *a:reviews.append(a),reply_email=lambda *a:replies.append(a),initial_drafts={'id':{'id':'id','status':'pending_approval','text_body':raw}})
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'gmail_search',return_value=[{'id':'retry-1'}]),patch.object(ca,'gmail_get',return_value=msg),patch.object(ca,'gmail_subject_sent_any',return_value=False),patch.object(ca,'gmail_thread_reply_sent',side_effect=[False,True]),patch.object(ca,'revise_with_glm',side_effect=[RuntimeError('provider down'),revised]):
   first=app.post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'});second=app.post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(first.json()['results'][0]['action'],'processing_deferred');self.assertEqual(second.json()['results'][0]['action'],'review_sent');self.assertEqual(len(replies),1);self.assertEqual(len(reviews),1);self.assertEqual(ca.load(ca.INBOX_FILE,{})['messages']['retry-1']['status'],'replied')
 def test_action_applied_crash_recovery_sends_only_missing_acknowledgment(self):
  at=ca.datetime(2030,1,2,9,0,tzinfo=ca.ET);date=at.strftime('%Y-%m-%d');stamp=int(at.timestamp()*1000);body=('Today’s Notes\nPlease complete the survey and share beta feedback in one-on-one conversations. '*6+'\nThank You\nThank you for testing.');msg=self._gmail_message('crash-1','a@example.com','LifeHouse OS content',body,stamp);ca.atomic_json_write(ca.STATE_FILE,{date:{'stage':'review_sent','content_valid':True,'draft_id':'already','review_subject':'[REVIEW] Already'}});ca.atomic_json_write(ca.INBOX_FILE,{'version':1,'context':[],'standing_hold':None,'messages':{'crash-1':{'status':'action_applied','result_action':'review_sent','intent':'content'}}});replies=[]
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'gmail_search',return_value=[{'id':'crash-1'}]),patch.object(ca,'gmail_get',return_value=msg):r=self.app(reply_email=lambda *a:replies.append(a),initial_drafts={'already':{'id':'already','status':'pending_approval'}}).post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'})
  self.assertTrue(r.json()['results'][0]['recovered']);self.assertEqual(len(replies),1);self.assertEqual(ca.load(ca.INBOX_FILE,{})['messages']['crash-1']['status'],'replied')
 def test_clear_bound_review_approval_phrases_and_stop_precedence(self):
  for text in ('Looks good','This is approved','I approve','Approved, thank you'):
   self.assertEqual(ca.classify_direct_message('Re: review',text,thread_bound=True),'approve',text)
  self.assertEqual(ca.classify_direct_message('Re: review',"Looks good, but don't send it",thread_bound=True),'hold')
  self.assertEqual(ca.classify_direct_message('Re: review',"Resume the emails, but don't send this one",thread_bound=True),'hold')
 def test_quoted_original_is_removed_from_authenticated_human_copy(self):
  at=ca.datetime(2030,1,2,8,0,tzinfo=ca.ET);date=at.strftime('%Y-%m-%d');stamp=int(at.timestamp()*1000);ca.atomic_json_write(ca.STATE_FILE,{date:{'stage':'hold','content_valid':False}});authored=('Today’s Notes\nPlease complete the current survey and share beta feedback in one-on-one conversations. '*6+'\nThank You\nThank you for testing.');body=authored+'\nFrom: LifeHouse OS <iris@lifehouseos.com>\nSubject: old review\nOLD QUOTED DRAFT MUST NOT APPEAR';msg=self._gmail_message('quote-1','a@example.com','LifeHouse OS content for today',body,stamp);reviews=[]
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'gmail_search',return_value=[{'id':'quote-1'}]),patch.object(ca,'gmail_get',return_value=msg),patch.object(ca,'gmail_subject_sent_any',return_value=False):r=self.app(send_email=lambda *a:reviews.append(a)).post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.json()['results'][0]['action'],'review_sent');self.assertNotIn('OLD QUOTED',ca.load(ca.STATE_FILE,{})[date]['raw_content']);self.assertNotIn('OLD QUOTED',self._drafts['id']['text_body'])
 def test_future_dated_complete_copy_waits_then_becomes_exact_review(self):
  received=ca.datetime(2030,8,1,14,0,tzinfo=ca.ET);tomorrow=ca.datetime(2030,8,2,7,0,tzinfo=ca.ET);date=received.strftime('%Y-%m-%d');stamp=int(received.timestamp()*1000);ca.atomic_json_write(ca.STATE_FILE,{date:{'stage':'sent','content_valid':True,'draft_id':'done'}});body=('Today’s Notes\nPlease complete the current survey and share beta feedback in one-on-one conversations before the upcoming sprint. '*6+'\nThank You\nThank you for testing.');msg=self._gmail_message('future-1','a@example.com','LifeHouse OS content for August 2, 2030',body,stamp);reviews=[];replies=[];app=self.app(send_email=lambda *a:reviews.append(a),reply_email=lambda *a:replies.append(a),initial_drafts={'done':{'id':'done','status':'sent'}})
  with patch.object(ca,'now_et',return_value=received),patch.object(ca,'gmail_search',return_value=[{'id':'future-1'}]),patch.object(ca,'gmail_get',return_value=msg):first=app.post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(first.json()['results'][0]['action'],'dated_content_recorded');self.assertEqual(first.json()['results'][0]['target_date'],'2030-08-02');self.assertEqual(reviews,[]);self.assertEqual(len(replies),1)
  with patch.object(ca,'now_et',return_value=tomorrow),patch.object(ca,'gmail_subject_sent_any',return_value=False),patch.object(ca,'drive_source',side_effect=AssertionError('dated direct content must win before Drive')):second=app.post('/api/lhos/automation/prepare',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(second.json()['action'],'review_sent');state=ca.load(ca.STATE_FILE,{})['2030-08-02'];self.assertEqual(state['source']['type'],'authorized_dated_direct_email');self.assertEqual(ca.plain_text(state['raw_content']),ca.plain_text(body));self.assertEqual(len(reviews),1)
 def test_processing_crash_recovers_existing_review_without_duplicate(self):
  at=ca.datetime(2030,1,2,9,0,tzinfo=ca.ET);date=at.strftime('%Y-%m-%d');stamp=int(at.timestamp()*1000);body=('Today’s Notes\nPlease complete the survey and share beta feedback in one-on-one conversations. '*6+'\nThank You\nThank you for testing.');msg=self._gmail_message('processing-1','a@example.com','LifeHouse OS content for today',body,stamp);ca.atomic_json_write(ca.STATE_FILE,{date:{'stage':'review_sent','content_valid':True,'draft_id':'already','review_subject':'[REVIEW] Already','source':{'type':'authorized_direct_email','message_id':'processing-1'}}});ca.atomic_json_write(ca.INBOX_FILE,{'version':1,'context':[],'standing_hold':None,'messages':{'processing-1':{'status':'processing','intent':'content'}}});replies=[]
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'gmail_search',return_value=[{'id':'processing-1'}]),patch.object(ca,'gmail_get',return_value=msg):r=self.app(reply_email=lambda *a:replies.append(a),initial_drafts={'already':{'id':'already','status':'pending_approval'}}).post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'})
  self.assertTrue(r.json()['results'][0]['recovered']);self.assertEqual(r.json()['results'][0]['action'],'review_sent');self.assertEqual(len(replies),1);self.assertEqual(list(self._drafts),['already'])
 def test_corrupt_inbox_state_fails_closed_without_reply_or_overwrite(self):
  at=ca.datetime(2030,1,2,9,0,tzinfo=ca.ET);ca.INBOX_FILE.write_text('{corrupt');replies=[];before=ca.INBOX_FILE.read_bytes()
  with patch.object(ca,'now_et',return_value=at),self.assertRaises(RuntimeError):self.app(reply_email=lambda *a:replies.append(a)).post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(replies,[]);self.assertEqual(ca.INBOX_FILE.read_bytes(),before)
 def test_gate_blocks_approved_human_draft_for_deferred_inbox_action(self):
  at=ca.datetime(2030,1,2,15,0,tzinfo=ca.ET);date=at.strftime('%Y-%m-%d');ca.atomic_json_write(ca.STATE_FILE,{date:{'date':date,'date_display':'January 02, 2030','stage':'approved','content_valid':True,'draft_id':'id','subject':'Human','source':{'type':'drive'}}});ca.atomic_json_write(ca.INBOX_FILE,{'version':1,'context':[],'standing_hold':None,'messages':{'newer-1':{'status':'replied_action_pending','intent':'revision'}}});beta=[];alerts=[]
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'gmail_subject_sent_any',return_value=False):r=self.app(send_email=lambda *a:alerts.append(a),send_draft=lambda *a:(beta.append(a) or {'status':'sent'}),initial_drafts={'id':{'id':'id','status':'approved','approved_by':'Kristina'}}).post('/api/lhos/automation/auto-send',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(beta,[]);self.assertEqual(r.json()['action'],'not_sent');self.assertIn('newer authenticated approver instruction',r.json()['reason']);self.assertEqual(ca.load(ca.STATE_FILE,{})[date]['stage'],'not_sent')
 def test_reconcile_revokes_approved_human_draft_for_processing_inbox_action(self):
  at=ca.datetime(2030,1,2,9,0,tzinfo=ca.ET);date=at.strftime('%Y-%m-%d');ca.atomic_json_write(ca.STATE_FILE,{date:{'date':date,'date_display':'January 02, 2030','stage':'approved','content_valid':True,'draft_id':'id','subject':'Human','source':{'type':'drive'}}});ca.atomic_json_write(ca.INBOX_FILE,{'version':1,'context':[],'standing_hold':None,'messages':{'newer-2':{'status':'processing','intent':'content'}}});beta=[]
  with patch.object(ca,'now_et',return_value=at):r=self.app(send_draft=lambda *a:(beta.append(a) or {'status':'sent'}),initial_drafts={'id':{'id':'id','status':'approved','approved_by':'Kristina'}}).post('/api/lhos/automation/reconcile',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(beta,[]);self.assertEqual(r.json()['action'],'approval_revoked_pending_reference');self.assertEqual(r.json()['message_id'],'newer-2');self.assertEqual(ca.load(ca.STATE_FILE,{})[date]['stage'],'review_sent')
 def test_new_review_persists_recipients_and_gmail_thread_evidence(self):
  at=ca.datetime(2030,1,2,8,0,tzinfo=ca.ET);date=at.strftime('%Y-%m-%d');raw=('Today’s Notes\nPlease complete the current survey and share beta feedback in one-on-one conversations. '*6+'\nThank You\nThank you for testing.');ca.atomic_json_write(ca.STATE_FILE,{date:{'stage':'hold','content_valid':False}})
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'gmail_subject_sent_any',return_value=False),patch.object(ca,'drive_source',return_value=({'name':'x'},raw,{'name':'x'})):r=self.app(send_email=lambda *a:{'id':'review-gmail-1','threadId':'review-thread-1'}).post('/api/lhos/automation/prepare',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(r.json()['action'],'review_sent');state=ca.load(ca.STATE_FILE,{})[date];self.assertEqual(state['review_gmail_id'],'review-gmail-1');self.assertEqual(state['review_thread_id'],'review-thread-1');self.assertEqual(state['review_recipients'],['a@example.com'])
 def test_copied_current_review_subject_in_wrong_thread_cannot_approve(self):
  at=ca.datetime(2030,1,2,9,0,tzinfo=ca.ET);date=at.strftime('%Y-%m-%d');stamp=int(at.timestamp()*1000);review='[REVIEW] LifeHouse OS Beta Email Draft - January 02, 2030 [draft:id]';raw=('Today’s Notes\nPlease complete the current survey and share beta feedback in one-on-one conversations. '*6+'\nThank You\nThank you for testing.');ca.atomic_json_write(ca.STATE_FILE,{date:{'stage':'review_sent','content_valid':True,'draft_id':'id','review_subject':review,'review_thread_id':'actual-review-thread','raw_content':raw,'source':{'type':'drive'}}});msg=self._gmail_message('copy-subject-1','a@example.com','Re: '+review,'Approved',stamp);msg['threadId']='different-thread';approved=[];replies=[]
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'gmail_search',return_value=[{'id':'copy-subject-1'}]),patch.object(ca,'gmail_get',return_value=msg):r=self.app(approve_draft=lambda *a,**k:approved.append(a),reply_email=lambda *a:replies.append(a),initial_drafts={'id':{'id':'id','status':'pending_approval'}}).post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(approved,[]);self.assertEqual(r.json()['results'][0]['action'],'clarification_needed');self.assertEqual(len(replies),1)
 def test_approval_text_inside_docx_attachment_cannot_authorize(self):
  at=ca.datetime(2030,1,2,9,0,tzinfo=ca.ET);date=at.strftime('%Y-%m-%d');stamp=int(at.timestamp()*1000);review='[REVIEW] LifeHouse OS Beta Email Draft - January 02, 2030 [draft:id]';raw=('Today’s Notes\nPlease complete the current survey and share beta feedback in one-on-one conversations. '*6+'\nThank You\nThank you for testing.');ca.atomic_json_write(ca.STATE_FILE,{date:{'stage':'review_sent','content_valid':True,'draft_id':'id','review_subject':review,'raw_content':raw,'source':{'type':'drive'}}});msg=self._gmail_message('attach-approval-1','a@example.com','Re: '+review,'Please review the attached note.',stamp);approved=[];replies=[]
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'gmail_search',return_value=[{'id':'attach-approval-1'}]),patch.object(ca,'gmail_get',return_value=msg),patch.object(ca,'gmail_docx_attachments',return_value='Approved'):
   r=self.app(approve_draft=lambda *a,**k:approved.append(a),reply_email=lambda *a:replies.append(a),initial_drafts={'id':{'id':'id','status':'pending_approval'}}).post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(approved,[]);self.assertNotEqual(r.json()['results'][0]['action'],'approved');self.assertEqual(len(replies),1)
 def test_html_quote_container_cannot_supply_instruction_authority(self):
  enc=lambda text:base64.urlsafe_b64encode(text.encode()).decode().rstrip('=')
  payload={'mimeType':'text/html','body':{'data':enc('<p>Approved</p><blockquote><p>Do not send it yet</p></blockquote>')}}
  self.assertEqual(ca.classify_instruction(ca.clean_reply(ca.extract_gmail_body(payload))),'approve')
  payload={'mimeType':'text/html','body':{'data':enc('<p>Do not send it yet</p><div class="gmail_quote"><p>Approved</p></div>')}}
  self.assertEqual(ca.classify_instruction(ca.clean_reply(ca.extract_gmail_body(payload))),'hold')
 def test_newer_hold_preempts_older_approval_in_same_poll(self):
  at=ca.datetime(2030,1,2,9,0,tzinfo=ca.ET);date=at.strftime('%Y-%m-%d');review='[REVIEW] LifeHouse OS Beta Email Draft - January 02, 2030 [draft:id]';raw=('Today’s Notes\nPlease complete the survey and share detailed beta feedback. '*8+'\nThank You\nThank you for testing.');ca.atomic_json_write(ca.STATE_FILE,{date:{'date':date,'date_display':'January 02, 2030','stage':'review_sent','content_valid':True,'draft_id':'id','review_subject':review,'raw_content':raw,'source':{'type':'drive'}}});approval=self._gmail_message('older-approval','a@example.com','Re: '+review,'Approved',1000);hold=self._gmail_message('newer-hold','a@example.com','LifeHouse OS delivery instruction','Stop these emails until I explicitly tell you to resume.',2000);messages={'older-approval':approval,'newer-hold':hold};beta=[]
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'gmail_search',return_value=[{'id':'older-approval'},{'id':'newer-hold'}]),patch.object(ca,'gmail_get',side_effect=lambda token,mid,*a:messages[mid]):r=self.app(send_draft=lambda *a:beta.append(a),initial_drafts={'id':{'id':'id','status':'pending_approval'}}).post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(beta,[]);self.assertEqual(r.json()['results'][0]['action'],'approval_deferred_pending_reference');self.assertTrue((ca.load(ca.INBOX_FILE,{})['standing_hold'] or {})['active'])
 def test_partial_reconciliation_pauses_for_pending_approver_instruction(self):
  at=ca.datetime(2030,1,2,12,0,tzinfo=ca.ET);date=at.strftime('%Y-%m-%d');ca.atomic_json_write(ca.STATE_FILE,{date:{'date':date,'date_display':'January 02, 2030','stage':'partial','content_valid':True,'draft_id':'id','source':{'type':'drive'}}});ca.atomic_json_write(ca.INBOX_FILE,{'version':1,'messages':{'hold-1':{'status':'replied_action_pending','intent':'standing_hold'}},'context':[],'standing_hold':None});beta=[]
  with patch.object(ca,'now_et',return_value=at):r=self.app(send_draft=lambda *a:beta.append(a),initial_drafts={'id':{'id':'id','status':'partial','approved_by':'Kristina'}}).post('/api/lhos/automation/reconcile',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(beta,[]);self.assertEqual(r.json()['action'],'delivery_paused_pending_approver_instruction');self.assertEqual(ca.load(ca.STATE_FILE,{})[date]['stage'],'delivery_paused_pending_instruction');self.assertEqual(self._drafts['id']['status'],'partial');self.assertIn('authorization_revoked_at',self._drafts['id'])
 def test_ambiguous_review_send_failure_retries_same_direct_content_draft(self):
  at=ca.datetime(2030,1,2,8,0,tzinfo=ca.ET);date=at.strftime('%Y-%m-%d');stamp=int(at.timestamp()*1000);ca.atomic_json_write(ca.STATE_FILE,{date:{'date':date,'date_display':'January 02, 2030','stage':'hold','content_valid':False}});body=('Today’s Notes\nBeta feedback and one-on-one conversations remain the current priority. '*8+'\nThank You\nThank you for testing.');msg=self._gmail_message('retry-content','a@example.com','LifeHouse OS content for today',body,stamp);attempts=[];replies=[]
  def review_send(*args):
   attempts.append(args)
   raise RuntimeError('ambiguous Gmail acceptance')
  app=self.app(send_email=review_send,reply_email=lambda *a:replies.append(a))
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'gmail_search',return_value=[{'id':'retry-content'}]),patch.object(ca,'gmail_get',return_value=msg),patch.object(ca,'gmail_subject_sent_any',side_effect=[False,True]),patch.object(ca,'gmail_sent_evidence',return_value={'id':'review-1','threadId':'review-thread-1'}),patch.object(ca,'gmail_thread_reply_sent',side_effect=[False,True]):
   first=app.post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'});second=app.post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(first.json()['results'][0]['action'],'processing_deferred');self.assertEqual(second.json()['results'][0]['action'],'review_sent');self.assertEqual(set(self._drafts),{'id'});self.assertEqual(len(attempts),1);self.assertEqual(len(replies),1);self.assertEqual(ca.load(ca.STATE_FILE,{})[date]['review_thread_id'],'review-thread-1')
 def test_editorial_provenance_ids_match_only_rendered_context(self):
  data={'context':[{'message_id':'old','received_date':'2029-01-01','intent':'context','body':'old'},{'message_id':'approval','received_date':'2030-01-01','intent':'approve','body':'Approved'},{'message_id':'future','received_date':'2030-01-01','target_date':'2030-01-03','intent':'content','body':'future'},{'message_id':'selected','received_date':'2030-01-01','intent':'standing_hold','body':'current beta reality'}]}
  rendered,ids=ca.editorial_context(data,'2030-01-02',include_message_ids=True);self.assertEqual(ids,['selected']);self.assertIn('current beta reality',rendered);self.assertNotIn('future',rendered)
  large={'context':[{'message_id':f'id-{i}','received_date':'2030-01-01','intent':'context','from':'a@example.com','body':f'marker-{i} '+('x'*6000)} for i in range(6)]};bounded,bounded_ids=ca.editorial_context(large,'2030-01-02',include_message_ids=True);self.assertLessEqual(len(bounded),24000);self.assertEqual(bounded_ids[-1],'id-5')
  for mid in bounded_ids:self.assertIn('marker-'+mid.split('-')[1],bounded)
  for mid in set(f'id-{i}' for i in range(6))-set(bounded_ids):self.assertNotIn('marker-'+mid.split('-')[1],bounded)
 def test_sent_review_recovery_requires_exact_reviewer_set(self):
  payload={'headers':[{'name':'Subject','value':'Review 1'},{'name':'To','value':'A <a@example.com>, b@example.com'},{'name':'Message-ID','value':'<review@example.com>'}]};msg={'id':'sent-1','threadId':'thread-1','payload':payload}
  with patch.object(ca,'gmail_search',return_value=[{'id':'sent-1'}]),patch.object(ca,'gmail_get',return_value=msg):
   self.assertEqual(ca.gmail_sent_evidence('tok','Review 1','2030-01-02',['a@example.com','b@example.com'])['threadId'],'thread-1');self.assertIsNone(ca.gmail_sent_evidence('tok','Review 1','2030-01-02',['a@example.com','c@example.com']))
 def test_new_review_without_persisted_thread_evidence_cannot_approve(self):
  at=ca.datetime(2030,1,2,9,0,tzinfo=ca.ET);date=at.strftime('%Y-%m-%d');stamp=int(at.timestamp()*1000);review='[REVIEW] LifeHouse OS Beta Email Draft - January 02, 2030 [draft:id]';raw=('Today’s Notes\nPlease complete the survey and share detailed beta feedback. '*8+'\nThank You\nThank you for testing.');ca.atomic_json_write(ca.STATE_FILE,{date:{'stage':'review_sent','content_valid':True,'draft_id':'id','review_subject':review,'review_thread_binding_required':True,'raw_content':raw,'source':{'type':'drive'}}});msg=self._gmail_message('missing-thread-evidence','a@example.com','Re: '+review,'Approved',stamp);approved=[]
  with patch.object(ca,'now_et',return_value=at),patch.object(ca,'gmail_search',return_value=[{'id':'missing-thread-evidence'}]),patch.object(ca,'gmail_get',return_value=msg):r=self.app(approve_draft=lambda *a,**k:approved.append(a),initial_drafts={'id':{'id':'id','status':'pending_approval'}}).post('/api/lhos/automation/check-replies',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(approved,[]);self.assertEqual(r.json()['results'][0]['action'],'clarification_needed');self.assertEqual(ca.TRUSTED_AUTHSERV,'mx.google.com')
 def test_active_standing_hold_blocks_gate_and_reconcile_after_message_completed(self):
  for hour,endpoint in [(12,'reconcile'),(15,'auto-send')]:
   with self.subTest(endpoint=endpoint):
    at=ca.datetime(2030,1,2,hour,0,tzinfo=ca.ET);date=at.strftime('%Y-%m-%d');ca.atomic_json_write(ca.STATE_FILE,{date:{'date':date,'date_display':'January 02, 2030','stage':'approved','content_valid':True,'draft_id':'id','source':{'type':'drive'}}});ca.atomic_json_write(ca.INBOX_FILE,{'version':1,'messages':{'hold-done':{'status':'replied','intent':'standing_hold'}},'context':[],'standing_hold':{'active':True,'message_id':'hold-done','from':'a@example.com'}});beta=[];alerts=[]
    with patch.object(ca,'now_et',return_value=at):r=self.app(send_email=lambda *a:alerts.append(a),send_draft=lambda *a:beta.append(a),initial_drafts={'id':{'id':'id','status':'approved','approved_by':'Kristina'}}).post('/api/lhos/automation/'+endpoint,headers={'x-lhos-automation-token':'secret'})
    self.assertEqual(beta,[]);self.assertIn(r.json()['action'],('approval_revoked_pending_reference','not_sent'))
 def test_reconcile_cannot_start_invalid_approved_draft(self):
  at=ca.datetime(2030,1,2,12,0,tzinfo=ca.ET);date=at.strftime('%Y-%m-%d');ca.atomic_json_write(ca.STATE_FILE,{date:{'stage':'approved','content_valid':False,'draft_id':'id','source':{'type':'drive'}}});beta=[]
  with patch.object(ca,'now_et',return_value=at):r=self.app(send_draft=lambda *a:beta.append(a),initial_drafts={'id':{'id':'id','status':'approved','approved_by':'Kristina'}}).post('/api/lhos/automation/reconcile',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(beta,[]);self.assertEqual(r.json()['action'],'no_op')
 def test_sending_pause_preserves_delivery_ledger_status(self):
  at=ca.datetime(2030,1,2,12,0,tzinfo=ca.ET);date=at.strftime('%Y-%m-%d');ca.atomic_json_write(ca.STATE_FILE,{date:{'stage':'sending','content_valid':True,'draft_id':'id','source':{'type':'drive'}}});ca.atomic_json_write(ca.INBOX_FILE,{'version':1,'messages':{'instruction-1':{'status':'processing','intent':'revision'}},'context':[],'standing_hold':None});beta=[]
  with patch.object(ca,'now_et',return_value=at):r=self.app(send_draft=lambda *a:beta.append(a),initial_drafts={'id':{'id':'id','status':'sending','approved_by':'Kristina'}}).post('/api/lhos/automation/reconcile',headers={'x-lhos-automation-token':'secret'})
  self.assertEqual(beta,[]);self.assertEqual(self._drafts['id']['status'],'sending');self.assertIn('authorization_revoked_at',self._drafts['id']);self.assertEqual(r.json()['action'],'delivery_paused_pending_approver_instruction')
if __name__=='__main__':unittest.main()
