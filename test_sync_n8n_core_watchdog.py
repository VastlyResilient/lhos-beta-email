import unittest
from scripts.sync_n8n_core_watchdog import patch_workflow,update_payload,TRIGGER_NAME,REQUEST_NAME,WATCHDOG_URL

class SyncN8nTests(unittest.TestCase):
 def base(self):return {"id":"wf","name":"LHOS","active":True,"nodes":[{"name":"Existing","id":"existing","type":"n8n-nodes-base.httpRequest","credentials":{"httpHeaderAuth":{"id":"cred","name":"Header"}}}],"connections":{},"settings":{"executionOrder":"v1","availableInMCP":True,"callerPolicy":"workflowsFromSameOwner"},"versionId":"do-not-send"}
 def test_patch_is_idempotent_and_reuses_existing_credential(self):
  once=patch_workflow(self.base());twice=patch_workflow(once);names=[n['name'] for n in twice['nodes']];self.assertEqual(names.count(TRIGGER_NAME),1);self.assertEqual(names.count(REQUEST_NAME),1)
  by={n['name']:n for n in twice['nodes']};self.assertEqual(by[REQUEST_NAME]['credentials']['httpHeaderAuth']['id'],'cred');self.assertEqual(by[REQUEST_NAME]['parameters']['url'],WATCHDOG_URL);self.assertEqual(twice['connections'][TRIGGER_NAME]['main'][0][0]['node'],REQUEST_NAME)
 def test_update_payload_omits_read_only_and_activation_fields(self):
  payload=update_payload(patch_workflow(self.base()));self.assertEqual(set(payload),{'name','nodes','connections','settings'});self.assertNotIn('active',payload);self.assertNotIn('id',payload);self.assertEqual(payload['settings'],{'executionOrder':'v1'})
 def test_patch_requires_existing_authenticated_node(self):
  with self.assertRaises(ValueError):patch_workflow({"nodes":[],"connections":{}})

if __name__=='__main__':unittest.main()
