import os,tempfile,unittest,importlib
from fastapi.testclient import TestClient
from unittest.mock import patch
from fastapi import HTTPException
from datetime import datetime as RealDateTime
class SecurityTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();os.environ['DATA_DIR']=self.t.name;os.environ['LHOS_AUTOMATION_TOKEN']='auto';os.environ['LHOS_APPROVAL_SECRET']='approve-secret'
  import main;self.main=importlib.reload(main);self.c=TestClient(self.main.app)
 def tearDown(self):self.t.cleanup()
 def test_draft_apis_require_auth(self):
  self.assertEqual(self.c.get('/api/lhos/drafts').status_code,401)
  self.assertEqual(self.c.get('/api/lhos/log').status_code,401)
 def test_signed_approval_url(self):
  d=self.main.create_draft_record('s','RECIPIENT_NAME_PLACEHOLDER UNSUB_URL_PLACEHOLDER','t','July 23, 2026');url=d['approval_url'];self.assertIn('token=',url)
  self.assertEqual(self.c.get(url.split('?')[0]).status_code,401)
  self.assertEqual(self.c.get(url).status_code,200)
 def test_create_requires_automation_header(self):
  body={'subject':'s','html_body':'h','date':'July 23, 2026'}
  self.assertEqual(self.c.post('/api/lhos/drafts',json=body).status_code,401)
  self.assertEqual(self.c.post('/api/lhos/drafts',json=body,headers={'x-lhos-automation-token':'auto'}).status_code,200)
 def test_test_mode_allowlist_and_delivery_isolation(self):
  with self.assertRaises(HTTPException):
   self.main.create_draft_record('[TEST] s','RECIPIENT_NAME_PLACEHOLDER UNSUB_URL_PLACEHOLDER','t','July 23, 2026',True,'someone@example.com')
  d=self.main.create_draft_record('[TEST] s','RECIPIENT_NAME_PLACEHOLDER UNSUB_URL_PLACEHOLDER','t','July 23, 2026',True,'bobbyatf@gmail.com')
  draft=self.main.load_drafts()[d['draft_id']];self.assertTrue(draft['test_mode']);self.assertEqual(draft['test_recipient'],'bobbyatf@gmail.com')
  calls=[]
  with patch.object(self.main,'get_google_access_token',return_value='tok'),patch.object(self.main,'gmail_exact_sent',return_value=False),patch.object(self.main,'send_gmail',side_effect=lambda token,to,subject,body,*a,**k:(calls.append(to) or {'id':'m'})),patch.object(self.main,'get_contact_group_id',side_effect=AssertionError('production contacts accessed')):
   result=self.main.send_draft_safely(d['draft_id'],'test-approver')
  self.assertEqual(result['status'],'sent');self.assertEqual(calls,['bobbyatf@gmail.com'])
 def test_production_approval_records_without_sending(self):
  d=self.main.create_draft_record('s','RECIPIENT_NAME_PLACEHOLDER UNSUB_URL_PLACEHOLDER','content','July 23, 2026')
  module=self.main
  class AtEight(RealDateTime):
   @classmethod
   def now(cls,tz=None):
    x=RealDateTime(2026,7,23,8,0,tzinfo=module.ET);return x.astimezone(tz) if tz else x
  with patch.object(module,'datetime',AtEight),patch.object(module,'send_gmail',side_effect=AssertionError('send called')):
   out=module.record_draft_approval(d['draft_id'],'Kristina via email')
  self.assertEqual(out['status'],'approved');self.assertEqual(module.load_drafts()[d['draft_id']]['status'],'approved')
 def test_production_approval_rejected_after_deadline(self):
  d=self.main.create_draft_record('s','RECIPIENT_NAME_PLACEHOLDER UNSUB_URL_PLACEHOLDER','content','July 23, 2026');module=self.main
  class AtThree(RealDateTime):
   @classmethod
   def now(cls,tz=None):
    x=RealDateTime(2026,7,23,15,1,tzinfo=module.ET);return x.astimezone(tz) if tz else x
  with patch.object(module,'datetime',AtThree):
   with self.assertRaises(HTTPException):module.record_draft_approval(d['draft_id'],'late')
 def test_production_signed_link_is_preview_only(self):
  d=self.main.create_draft_record('s','RECIPIENT_NAME_PLACEHOLDER UNSUB_URL_PLACEHOLDER','content','July 23, 2026');tok=self.main.approval_token(d['draft_id'])
  page=self.c.get(f"/lhos/approve/{d['draft_id']}?token={tok}");self.assertEqual(page.status_code,200);self.assertIn('reply directly to the review email',page.text)
  approve=self.c.post(f"/api/lhos/approve/{d['draft_id']}?token={tok}",json={});self.assertEqual(approve.status_code,403)
  edit=self.c.post(f"/api/lhos/drafts/{d['draft_id']}/edit?token={tok}",json={'subject':'x','html_body':'y'});self.assertEqual(edit.status_code,403)
if __name__=='__main__':unittest.main()
