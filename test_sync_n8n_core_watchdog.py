import unittest
from unittest.mock import patch
import scripts.sync_n8n_core_watchdog as sync

class SyncN8nTests(unittest.TestCase):
 def base(self):return {"id":"wf","name":"LHOS","active":True,"nodes":[{"name":"Existing","id":"existing","type":"n8n-nodes-base.httpRequest","parameters":{"url":sync.BACKEND_AUTOMATION_PREFIX+"prepare"},"credentials":{"httpHeaderAuth":{"id":"cred","name":"LHOS Header"}}}],"connections":{},"settings":{"executionOrder":"v1","availableInMCP":True,"callerPolicy":"workflowsFromSameOwner"},"versionId":"do-not-send"}
 def test_patch_is_idempotent_and_reuses_trusted_credential(self):
  base=self.base();base['nodes'].insert(0,{"name":"Unrelated","type":"n8n-nodes-base.httpRequest","parameters":{"url":"https://example.com"},"credentials":{"httpHeaderAuth":{"id":"wrong","name":"Wrong"}}})
  once=sync.patch_workflow(base);twice=sync.patch_workflow(once);names=[n['name'] for n in twice['nodes']];self.assertEqual(names.count(sync.TRIGGER_NAME),1);self.assertEqual(names.count(sync.REQUEST_NAME),1)
  by={n['name']:n for n in twice['nodes']};self.assertEqual(by[sync.REQUEST_NAME]['credentials']['httpHeaderAuth']['id'],'cred');self.assertEqual(by[sync.REQUEST_NAME]['parameters']['url'],sync.WATCHDOG_URL);self.assertEqual(twice['connections'][sync.TRIGGER_NAME]['main'][0][0]['node'],sync.REQUEST_NAME)
 def test_ambiguous_trusted_credentials_fail_closed(self):
  base=self.base();base['nodes'].append({"name":"Other LHOS","type":"n8n-nodes-base.httpRequest","parameters":{"url":sync.BACKEND_AUTOMATION_PREFIX+"status"},"credentials":{"httpHeaderAuth":{"id":"other","name":"Other"}}})
  with self.assertRaises(ValueError):sync.patch_workflow(base)
  self.assertEqual(sync.select_trusted_credential(base,'other')['id'],'other')
 def test_update_payload_omits_read_only_and_activation_fields(self):
  payload=sync.update_payload(sync.patch_workflow(self.base()));self.assertEqual(set(payload),{'name','nodes','connections','settings'});self.assertNotIn('active',payload);self.assertNotIn('id',payload);self.assertEqual(payload['settings'],{'executionOrder':'v1'})
 def test_patch_requires_existing_trusted_authenticated_node(self):
  with self.assertRaises(ValueError):sync.patch_workflow({"nodes":[],"connections":{}})
 def test_verification_rejects_wrong_credential(self):
  patched=sync.patch_workflow(self.base());next(n for n in patched['nodes'] if n['name']==sync.REQUEST_NAME)['credentials']['httpHeaderAuth']['id']='wrong'
  with self.assertRaises(RuntimeError):sync.verify_workflow(patched,'cred',True)
 def test_post_update_failure_attempts_activation_rollback(self):
  calls=[];current=self.base()
  def fake_api(base,key,path,method='GET',body=None):
   calls.append((path,method))
   if len(calls)==1:return 200,current
   if method=='GET':raise RuntimeError('verification read failed')
   return 200,{}
  with patch.object(sync,'api',side_effect=fake_api):
   with self.assertRaisesRegex(RuntimeError,'verification read failed'):sync.sync_workflow('https://n8n','key','wf')
  self.assertGreaterEqual(calls.count(('/workflows/wf/activate','POST')),2)

if __name__=='__main__':unittest.main()
