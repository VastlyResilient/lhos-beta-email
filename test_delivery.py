import tempfile,unittest
from pathlib import Path
from delivery import deliver_once
class DeliveryTests(unittest.TestCase):
 def test_retry_never_resends_accepted_recipient(self):
  with tempfile.TemporaryDirectory() as td:
   calls=[]; fail={'b@example.com':True}; sent=set()
   def pre(a,s):return a in sent
   def send(a,s,h):
    calls.append(a)
    if fail.get(a):fail[a]=False;raise RuntimeError('temporary')
    sent.add(a);return {'id':a}
   kw=dict(date_key='2026-07-23',subject='S',html_body='RECIPIENT_NAME_PLACEHOLDER UNSUB_URL_PLACEHOLDER',contacts=[{'email':'a@example.com','name':'A'},{'email':'b@example.com','name':'B'}],suppressed=[],ledger_file=Path(td)/'l.json',already_sent=pre,send_one=send,unsubscribe_base='https://u')
   one=deliver_once(**kw);self.assertEqual(one['newly_sent_count'],1);self.assertFalse(one['complete'])
   two=deliver_once(**kw);self.assertEqual(two['newly_sent_count'],1);self.assertTrue(two['complete']);self.assertEqual(calls.count('a@example.com'),1)
 def test_suppressed_never_sent(self):
  with tempfile.TemporaryDirectory() as td:
   calls=[]
   r=deliver_once(date_key='d',subject='s',html_body='RECIPIENT_NAME_PLACEHOLDER UNSUB_URL_PLACEHOLDER',contacts=[{'email':'x@example.com'}],suppressed=['x@example.com'],ledger_file=Path(td)/'l.json',already_sent=lambda a,s:False,send_one=lambda *a:calls.append(a),unsubscribe_base='u')
   self.assertEqual(calls,[]);self.assertTrue(r['complete'])
 def test_concurrent_calls_send_each_recipient_once(self):
  import threading,time
  with tempfile.TemporaryDirectory() as td:
   calls=[];guard=threading.Lock();sent=set()
   def pre(a,s):
    with guard:return a in sent
   def send(a,s,h):
    time.sleep(0.01)
    with guard:calls.append(a);sent.add(a)
    return {'id':a}
   kw=dict(date_key='2026-07-23',subject='S',html_body='RECIPIENT_NAME_PLACEHOLDER UNSUB_URL_PLACEHOLDER',contacts=[{'email':f'u{i}@example.com','name':str(i)} for i in range(20)],suppressed=[],ledger_file=Path(td)/'l.json',already_sent=pre,send_one=send,unsubscribe_base='https://u')
   threads=[threading.Thread(target=lambda:deliver_once(**kw)) for _ in range(12)]
   [t.start() for t in threads];[t.join() for t in threads]
   self.assertEqual(len(calls),20);self.assertEqual(len(set(calls)),20)
if __name__=='__main__':unittest.main()
