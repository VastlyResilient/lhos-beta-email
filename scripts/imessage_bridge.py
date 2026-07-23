#!/Users/bobby/zilla/venv/bin/python
import argparse,datetime as dt,hashlib,json,os,re,sqlite3,subprocess,sys,tempfile
from pathlib import Path
from zoneinfo import ZoneInfo
import httpx
ET=ZoneInfo("America/New_York"); CHAT_ID=1079; CHAT_GUID="any;+;2232f1a0c45c4a14832adff0ec8120da"; CHAT_IDENTIFIER="2232f1a0c45c4a14832adff0ec8120da"; GROUP_ID="CD24A6BC-7F93-4D2D-A565-ADED19DD01FC"
NAMES={'16770e54d6d075bf96c70208dfd7e781364e2cf3b832c85bfebaaed28d33f027': 'Thomas Appling', '67ba33e3c4f07c6cb101e1af49df6fa3162d118c8804176ec8f1c9ba2fd90ad0': 'Kristina'}; SIGN="-AUTOSENT BY ZILLA"; STATE=Path("/Users/bobby/.zilla/lhos-imessage/state.json"); LOG=Path("/Users/bobby/.zilla/logs/lhos-imessage-bridge.log")
TOKEN=Path("/Users/bobby/lhos-beta-email/.automation_token"); API="https://lhos-beta-email-production.up.railway.app"; WORDS=("email","draft","review","content","upload","send","approve","revision","revise","change","iris","zilla")
def log(x): LOG.parent.mkdir(parents=True,exist_ok=True);LOG.open("a").write(f"{dt.datetime.now(ET).isoformat()} {x}\n")
def load():
 try:return json.loads(STATE.read_text())
 except:return {"processed":[],"sent_hashes":[],"outbound_guids":[],"send_attempts":{},"last_seen_id":0}
def save(s):
 STATE.parent.mkdir(parents=True,exist_ok=True);fd,tmp=tempfile.mkstemp(dir=STATE.parent,prefix='.state.',text=True)
 try:
  with os.fdopen(fd,'w') as f:json.dump(s,f,indent=2,sort_keys=True);f.flush();os.fsync(f.fileno())
  os.replace(tmp,STATE)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)
def hh(v):return hashlib.sha256((v or '').strip().lower().encode()).hexdigest()
def verify():
 c=sqlite3.connect(os.path.expanduser('~/Library/Messages/chat.db'));row=c.execute('SELECT display_name,guid,chat_identifier,service_name,style,group_id FROM chat WHERE ROWID=?',(CHAT_ID,)).fetchone();handles=[r[0] for r in c.execute('SELECT h.id FROM chat_handle_join j JOIN handle h ON h.ROWID=j.handle_id WHERE j.chat_id=?',(CHAT_ID,))]
 expected=("FFAI",CHAT_GUID,CHAT_IDENTIFIER,"iMessage",43,GROUP_ID)
 if not row or row!=expected or set(map(hh,handles))!=set(NAMES):raise RuntimeError('FFAI identity, service, group, or participant allowlist mismatch')
def ndjson(t):
 out=[]
 for line in t.splitlines():
  try:out.append(json.loads(line))
  except:pass
 return out
def history(limit=200):
 r=subprocess.run(['/opt/homebrew/bin/imsg','history','--chat-id',str(CHAT_ID),'--limit',str(limit),'--json'],capture_output=True,text=True,timeout=30)
 if r.returncode:raise RuntimeError('imsg history failed: '+r.stderr[:240])
 return ndjson(r.stdout)
def headers():return {'X-LHOS-Automation-Token':TOKEN.read_text().strip()}
def status():
 r=httpx.get(API+'/api/lhos/automation/status',headers=headers(),timeout=30);r.raise_for_status();return r.json()
def decide(actor,text,guid,dry=False):
 r=httpx.post(API+'/api/lhos/automation/decision',headers=headers(),params={'dry_run':str(dry).lower()},json={'actor':actor,'text':text,'message_id':guid,'channel':'imessage'},timeout=180);r.raise_for_status();return r.json()
def signed(t):return t.strip() if t.strip().endswith(SIGN) else t.strip()+'\n\n'+SIGN
def send_group(text,s,dry=False):
 verify();text=signed(text);day=dt.datetime.now(ET).strftime('%Y-%m-%d');key=hashlib.sha256((day+CHAT_GUID+text).encode()).hexdigest();s.setdefault('send_attempts',{})
 if key in s['sent_hashes']:return {'action':'duplicate_suppressed'}
 if s['send_attempts'].get(key,{}).get('status')=='UNCERTAIN':return {'action':'uncertain_send_blocked'}
 if dry:return {'action':'would_send','text':text}
 s['send_attempts'][key]={'status':'SEND_STARTED','body':text,'attempted_at':dt.datetime.now(ET).isoformat()};save(s)
 r=subprocess.run(['/opt/homebrew/bin/imsg','send','--chat-id',str(CHAT_ID),'--text',text,'--json'],capture_output=True,text=True,timeout=45)
 match=next((x for x in history(40) if x.get('is_from_me') and (x.get('text') or '').strip()==text),None)
 if not match:
  s['send_attempts'][key]['status']='UNCERTAIN';save(s);raise RuntimeError('iMessage dispatch uncertain; automatic resend blocked')
 s['send_attempts'][key].update({'status':'SENT','outbound_guid':match.get('guid')});s['sent_hashes'].append(key)
 if match.get('guid'):s['outbound_guids'].append(match['guid'])
 save(s);return {'action':'sent','guid':match.get('guid')}
def actor(row):
 text=(row.get('text') or '').strip()
 if row.get('is_from_me'):return None if text.endswith(SIGN) else 'Bobby'
 return NAMES.get(hh(row.get('sender')))
def when(row):
 try:return dt.datetime.fromisoformat(row['created_at'].replace('Z','+00:00')).astimezone(ET)
 except:return None
def conversation(rows,i):
 a=actor(rows[i]);t=when(rows[i])
 return bool(a and t and any(j!=i and actor(x) and actor(x)!=a and when(x) and abs((when(x)-t).total_seconds())<=300 for j,x in enumerate(rows)))
def direct(row,s):
 t=(row.get('text') or '')
 return bool(re.match(r'(?is)\A[ \t]*@?(?:zilla|iris)[ \t]*:[ \t]*(?:\S(?:.*\S)?)[ \t]*\Z',t))
def run(dry=False,initialize=False,health=False):
 now=dt.datetime.now(ET)
 if not (initialize or health or 7<=now.hour<9 or (now.hour==9 and now.minute<15)):
  return {"action":"outside_active_window","time":now.isoformat(),"window":"07:00-09:15 America/New_York"}
 verify();s=load();rows=sorted(history(),key=lambda x:x.get('id',0));maxid=max([x.get('id',0) for x in rows] or [0])
 if initialize:s['last_seen_id']=maxid;save(s);return {'action':'initialized','last_seen_id':maxid,'chat':'FFAI','participants':sorted(NAMES.values())}
 st=status();now=dt.datetime.now(ET);stage=(st.get('state') or {}).get('stage');out={'time':now.isoformat(),'stage':stage,'actions':[]}
 if health:return {**out,'health':'ok','chat':'FFAI'}
 if ((now.hour==7 and now.minute>=5) or now.hour==8) and stage in (None,'hold'):
  out['actions'].append(send_group("Kristina and Thomas — today’s LifeHouse OS email content has not been uploaded or is incomplete. Iris has emailed Kristina. Please upload the dated content before 9:00 AM ET. No beta email will be sent without valid content and approval.",s,dry))
 if 7<=now.hour<9:
  new=[x for x in rows if int(x.get('id',0))>int(s.get('last_seen_id',0))]
  for i,row in enumerate(new):
   guid=row.get('guid') or ('local-'+str(row.get('id')));inbound_key='imsg:v1:'+CHAT_GUID+':'+guid;text=(row.get('text') or '').strip();who=actor(row)
   if not who or not text or inbound_key in s['processed']:continue
   if direct(row,s):
    d=decide(who,text,guid,dry);out['actions'].append({'actor':who,'decision':d})
    if not dry:
     a=d.get('action')
     if a=='approval_recorded':send_group(f"Approval recorded from {who}. If no later revision is requested, Iris will send the validated email at 9:00 AM ET.",s)
     elif a=='revised_review_sent':send_group(f"I applied {who}’s requested changes and emailed a revised review to Kristina, Bobby, and Thomas. A new approval is required before 9:00 AM ET.",s)
     elif a=='send_held':send_group(f"The email is on hold per {who}. A fresh approval is required before 9:00 AM ET.",s)
     elif a=='clarification_needed':send_group(f"{who}, were you talking to me about today’s LifeHouse OS email? Please mention Iris or Zilla and state approve, hold, or the exact revision.",s)
   elif re.search(r'\b(?:iris|zilla)\b',text,re.I) and not conversation(new,i):out['actions'].append(send_group(f"{who}, were you talking to me? Please use ‘Zilla:’ followed by approve, hold, status, or the exact email revision.",s,dry))
   s['processed'].append(inbound_key)
  if not dry:s['last_seen_id']=maxid;save(s)
 if now.hour==9 and 2<=now.minute<15 and (status().get('state') or {}).get('stage') not in ('sent','sent_external'):
  out['actions'].append(send_group("Kristina and Thomas — today’s LifeHouse OS beta email was not sent. Either valid content or final approval was missing by the 9:00 AM ET deadline. No beta tester received an email.",s,dry))
 return out
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--dry-run',action='store_true');p.add_argument('--initialize',action='store_true');p.add_argument('--health-check',action='store_true');a=p.parse_args()
 try:o=run(a.dry_run,a.initialize,a.health_check);print(json.dumps(o,indent=2));log('ok '+json.dumps(o,separators=(',',':'))[:1200])
 except Exception as e:log('ERROR '+repr(e));print(json.dumps({'error':str(e)}));sys.exit(1)
