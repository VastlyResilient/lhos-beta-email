import os,tempfile,unittest,importlib
from fastapi.testclient import TestClient
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
if __name__=='__main__':unittest.main()
