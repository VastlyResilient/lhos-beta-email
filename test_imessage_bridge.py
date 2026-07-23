import importlib.util,unittest,hashlib
from unittest.mock import patch
spec=importlib.util.spec_from_file_location("bridge","/Users/bobby/lhos-beta-email/scripts/imessage_bridge.py");b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
class BridgeTests(unittest.TestCase):
 def setUp(self):
  self.old=b.NAMES.copy();b.NAMES={hashlib.sha256(b"k").hexdigest():"Kristina",hashlib.sha256(b"t").hexdigest():"Thomas Appling"}
 def tearDown(self):b.NAMES=self.old
 def test_signature_exactly_once(self):
  self.assertEqual(b.signed("hello").count(b.SIGN),1);self.assertEqual(b.signed("hello\n\n"+b.SIGN).count(b.SIGN),1)
 def test_only_direct_workflow_language_routes(self):
  s={"outbound_guids":["z1"]}
  self.assertTrue(b.direct({"text":"Iris, change the email headline"},s))
  self.assertTrue(b.direct({"text":"good send it"},s))
  self.assertTrue(b.direct({"text":"yes","reply_to_guid":"z1"},s))
  self.assertFalse(b.direct({"text":"meeting Frank at three"},s))
  self.assertFalse(b.direct({"text":"confirmed"},s))
 def test_actor_allowlist_and_own_signature(self):
  self.assertEqual(b.actor({"is_from_me":False,"sender":"k","text":"x"}),"Kristina")
  self.assertEqual(b.actor({"is_from_me":False,"sender":"unknown","text":"x"}),None)
  self.assertEqual(b.actor({"is_from_me":True,"text":"approve"}),"Bobby")
  self.assertEqual(b.actor({"is_from_me":True,"text":"ok\n"+b.SIGN}),None)
 def test_dry_run_send_is_ffai_signed_and_deduped(self):
  s={"sent_hashes":[],"outbound_guids":[]}
  with patch.object(b,'verify',return_value=True):
   r=b.send_group('notice',s,True)
  self.assertEqual(r['action'],'would_send');self.assertTrue(r['text'].endswith(b.SIGN))
  key=hashlib.sha256(r['text'].encode()).hexdigest();s['sent_hashes']=[key]
  with patch.object(b,'verify',return_value=True):self.assertEqual(b.send_group('notice',s,True)['action'],'duplicate_suppressed')
if __name__=='__main__':unittest.main()
