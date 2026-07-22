"""Cloud orchestration endpoints invoked by n8n. All actions are fail-closed and idempotent."""
import base64, hashlib, html, hmac, json, os, re, tempfile, zipfile
from datetime import datetime, timedelta
from email import message_from_bytes
from email.header import decode_header, make_header
from email.mime.text import MIMEText
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo
import httpx
from fastapi import APIRouter, HTTPException, Request
from content_guard import validate_daily_content, validate_composed_sections, plain_text
from delivery import atomic_json_write
from email_template import build_beta_email

ET=ZoneInfo("America/New_York")
DRIVE_FOLDER_ID=os.getenv("LHOS_DRIVE_FOLDER_ID","1_u-jU56xvMCYO-yNmyAuZxkFuPVn-LHF")
AUTOMATION_TOKEN=os.getenv("LHOS_AUTOMATION_TOKEN","")
END_DATE=os.getenv("LHOS_END_DATE","").strip()
GLM_API_KEY=os.getenv("GLM_API_KEY","")
GLM_BASE_URL=os.getenv("GLM_BASE_URL","https://api.z.ai/api/paas/v4")
DATA_DIR=Path(os.getenv("DATA_DIR","/data"));STATE_FILE=DATA_DIR/"automation_state.json";PROCESSED_FILE=DATA_DIR/"processed_messages.json"
KRISTINA="kristina@freedomforgeai.com"
APPROVAL_WORDS=("approved","approve","looks good","send it","go ahead","lgtm","ship it")


def now_et(): return datetime.now(ET)
def load(path,default):
    try:return json.loads(path.read_text())
    except Exception:return default

def auth(req:Request):
    if not AUTOMATION_TOKEN: raise HTTPException(503,"Automation token not configured")
    supplied=req.headers.get("x-lhos-automation-token","")
    if not hmac.compare_digest(supplied,AUTOMATION_TOKEN): raise HTTPException(401,"Unauthorized")

def google_headers(token):return {"Authorization":f"Bearer {token}"}

def gmail_search(token,q,max_results=50):
    r=httpx.get("https://gmail.googleapis.com/gmail/v1/users/me/messages",headers=google_headers(token),params={"q":q,"maxResults":max_results},timeout=30)
    if r.status_code!=200:raise RuntimeError(f"Gmail search failed: {r.status_code} {r.text}")
    return r.json().get("messages",[])

def gmail_get(token,msg_id,fmt="full"):
    r=httpx.get(f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}",headers=google_headers(token),params={"format":fmt},timeout=30)
    if r.status_code!=200:raise RuntimeError(f"Gmail message fetch failed: {r.status_code}")
    return r.json()

def headers_map(payload):return {x.get("name","").lower():x.get("value","") for x in payload.get("headers",[])}
def dec_header(v):
    try:return str(make_header(decode_header(v or "")))
    except Exception:return v or ""

def extract_gmail_body(payload):
    candidates=[]
    def walk(p):
        if p.get("mimeType") in ("text/plain","text/html") and p.get("body",{}).get("data"):candidates.append(p)
        for x in p.get("parts",[]) or []:walk(x)
    walk(payload)
    part=next((x for x in candidates if x.get("mimeType")=="text/plain"),None) or next((x for x in candidates if x.get("mimeType")=="text/html"),None)
    if not part:return ""
    data=part["body"]["data"]+"="*((4-len(part["body"]["data"])%4)%4);text=base64.urlsafe_b64decode(data).decode("utf-8","replace")
    if part.get("mimeType")=="text/html":
        text=re.sub(r'<(?:br|/p|/div|/li|hr)[^>]*>','\n',text,flags=re.I);text=html.unescape(re.sub(r'<[^>]+>',' ',text))
    text=re.sub(r'[ \t]+',' ',text);return re.sub(r'\n\s*\n+','\n',text).strip()

def gmail_subject_sent_any(token,subject,date_key):
    day=datetime.strptime(date_key,"%Y-%m-%d").date();nxt=day+timedelta(days=1)
    for item in gmail_search(token,f'in:sent subject:"{subject}" after:{day:%Y/%m/%d} before:{nxt:%Y/%m/%d}',20):
        h=headers_map(gmail_get(token,item['id'],"metadata").get("payload",{}))
        if dec_header(h.get("subject"))==subject:return True
    return False

def drive_source(token,date_key):
    dt=datetime.strptime(date_key,"%Y-%m-%d");name=dt.strftime("%y%m%d")+".docx";q=f"'{DRIVE_FOLDER_ID}' in parents and trashed=false and name='{name}'"
    r=httpx.get("https://www.googleapis.com/drive/v3/files",headers=google_headers(token),params={"q":q,"fields":"files(id,name,size,modifiedTime,lastModifyingUser(displayName,emailAddress))"},timeout=30)
    if r.status_code!=200:raise RuntimeError(f"Drive lookup failed: {r.status_code} {r.text}")
    files=r.json().get("files",[])
    if not files:return None,"",{"name":name,"missing":True}
    f=files[0];r=httpx.get(f"https://www.googleapis.com/drive/v3/files/{f['id']}",headers=google_headers(token),params={"alt":"media"},timeout=60)
    if r.status_code!=200:raise RuntimeError(f"Drive download failed: {r.status_code}")
    with zipfile.ZipFile(BytesIO(r.content)) as z:
        import xml.etree.ElementTree as ETX
        root=ETX.fromstring(z.read("word/document.xml"));ns='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
        raw='\n'.join(''.join(t.text or '' for t in p.iter(ns+'t')) for p in root.iter(ns+'p')).strip()
    return f,raw,f

def paragraphize(lines):
    out=[]
    for line in lines:
        line=line.strip()
        if not line:continue
        if re.match(r'^(?:Profile\s*[→>-]|\d+[.)]|[-•])',line):out.append(f"<p>{html.escape(line)}</p>")
        else:out.append(f"<p>{html.escape(line)}</p>")
    return ''.join(out)

def deterministic_sections(raw):
    lines=[x.strip() for x in raw.splitlines() if x.strip()]
    buckets={"beta_notes":[],"what_changed":[],"known_issues":[],"helpful_reminder":[],"what_were_watching":[],"thank_you":[],"support_contact":[]};current="beta_notes"
    def heading(line):
        if len(line)>95:return None
        l=line.lower().strip(':')
        if any(x in l for x in ('known issue','bug','problem')):return "known_issues"
        if any(x in l for x in ('reminder','survey','challenge','how to','quick')):return "helpful_reminder"
        if any(x in l for x in ('what changed','sprint','you asked','we listened','continues','new today')):return "what_changed"
        if any(x in l for x in ('watching','looking ahead','next')):return "what_were_watching"
        if l in ('thank you','thanks') or l.startswith('thank you'):return "thank_you"
        if any(x in l for x in ('support','contact','ask iris')):return "support_contact"
        return None
    for line in lines:
        h=heading(line)
        if h:current=h;buckets[current].append(f"<p><strong>{html.escape(line)}</strong></p>")
        else:buckets[current].append(f"<p>{html.escape(line)}</p>")
    sections={k:''.join(v) for k,v in buckets.items() if v}
    ok,reasons=validate_composed_sections(sections)
    if not ok:raise RuntimeError("Deterministic composition failed: "+'; '.join(reasons))
    return sections

def clean_reply(body):
    out=[]
    for line in body.splitlines():
        s=line.strip()
        if s.startswith('>') or re.match(r'^On .+wrote:$',s):break
        if s:out.append(s)
    text='\n'.join(out).strip()
    return re.sub(r"(?:Have a great day[—-]and )?if you(?:’|')re curious, I can also.*?(?=Thank you|$)","",text,flags=re.I|re.S).strip()

def revise_with_glm(raw,feedback):
    if not GLM_API_KEY:raise RuntimeError("GLM not configured for reply-based revisions")
    prompt=f"Original daily briefing:\n{raw}\n\nApprover changes:\n{feedback}\n\nReturn the complete revised briefing as plain text. Preserve all unaffected details. No commentary."
    last=""
    for attempt in range(3):
        r=httpx.post(f"{GLM_BASE_URL}/chat/completions",headers={"Authorization":f"Bearer {GLM_API_KEY}","Content-Type":"application/json"},json={"model":"glm-4.7-flash","messages":[{"role":"system","content":"Apply requested editorial changes accurately. Never invent facts or add meta commentary."},{"role":"user","content":prompt}],"temperature":0.2,"max_tokens":5000},timeout=90)
        if r.status_code==200:
            text=r.json()['choices'][0]['message']['content'].strip();ok,reasons=validate_daily_content(text)
            if ok:return text
            last='; '.join(reasons)
        else:last=f"{r.status_code} {r.text[:300]}"
    raise RuntimeError("Revision failed: "+last)

def configure_router(*,get_token,send_email,create_draft,load_drafts,save_drafts,send_draft,approvers,public_url,sender_email,sender_name):
    router=APIRouter(prefix="/api/lhos/automation")
    def state_all():return load(STATE_FILE,{})
    def save_state(d):atomic_json_write(STATE_FILE,d)
    def current():
        now=now_et();return now.strftime("%Y-%m-%d"),now.strftime("%B %d, %Y")
    def make_review(date_display,draft_id,email_html,subtitle="Daily content validated"):
        url=f"{public_url}/lhos/approve/{draft_id}"
        preview=email_html.replace("RECIPIENT_NAME_PLACEHOLDER","Hello Beta Tester!").replace("UNSUB_URL_PLACEHOLDER","#")
        return f'<html><body><div style="background:#0E1B33;color:white;padding:20px;text-align:center;font-family:Nunito,Arial,sans-serif"><h2>{html.escape(subtitle)}</h2><p>Review the validated email below. Approve, edit, or request changes.</p><a style="display:inline-block;background:#4BC0C4;color:white;padding:14px 30px;text-decoration:none;font-weight:700" href="{url}">Review, Edit, Approve &amp; Send</a></div>{preview}</body></html>'
    def prepare_from_raw(date_key,date_display,raw,source,token,dry_run=False,subtitle="Daily content validated"):
        ok,reasons=validate_daily_content(raw)
        if not ok:return {"action":"hold","valid":False,"reasons":reasons,"source":source}
        sections=deterministic_sections(raw);email_html=build_beta_email(sections,date_display);subject=f"LifeHouse OS Beta Update - {date_display}"
        if dry_run:return {"action":"would_send_review","valid":True,"sections":list(sections),"subject":subject,"source":source}
        result=create_draft(subject,email_html,raw,date_display);did=result['draft_id'];review_subject=f"[REVIEW] LifeHouse OS Beta Email Draft - {date_display}"
        if not gmail_subject_sent_any(token,review_subject,date_key):send_email(token,','.join(approvers),review_subject,make_review(date_display,did,email_html,subtitle),sender_email,sender_name)
        st=state_all();st[date_key]={"date":date_key,"date_display":date_display,"stage":"review_sent","content_valid":True,"draft_id":did,"subject":subject,"review_subject":review_subject,"source":source,"raw_content":raw,"updated_at":now_et().isoformat()};save_state(st)
        return {"action":"review_sent","draft_id":did,"subject":subject}
    def prepare_impl(dry_run=False,force=False):
        date_key,date_display=current()
        if END_DATE and date_key>END_DATE:return {"action":"stopped","reason":"end_date","end_date":END_DATE}
        token=get_token();subject=f"LifeHouse OS Beta Update - {date_display}"
        if gmail_subject_sent_any(token,subject,date_key):
            if not dry_run:
                st=state_all();st[date_key]={"date":date_key,"date_display":date_display,"stage":"sent_external","content_valid":True,"subject":subject,"updated_at":now_et().isoformat()};save_state(st)
            return {"action":"already_sent","subject":subject}
        st=state_all();existing=st.get(date_key,{})
        if not force and existing.get('stage') in ('review_sent','sent','sending','partial'):return {"action":"no_op","stage":existing['stage'],"draft_id":existing.get('draft_id')}
        f,raw,meta=drive_source(token,date_key);ok,reasons=validate_daily_content(raw)
        if not ok:
            action_subject=f"[ACTION REQUIRED] LifeHouse OS content needed - {date_display}"
            if dry_run:return {"action":"would_hold_and_notify_kristina","valid":False,"reasons":reasons,"source":meta}
            if not gmail_subject_sent_any(token,action_subject,date_key):
                body='<p>Hi Kristina,</p><p>I cannot prepare today\'s LifeHouse OS beta update because the dated source is missing or incomplete.</p><ul>'+''.join(f'<li>{html.escape(x)}</li>' for x in reasons)+'</ul><p>Please update today\'s dated document and reply that it is ready, or reply with the complete content. If usable content is not provided, no beta email will be sent.</p><p>Warm regards,<br>Iris</p>'
                send_email(token,KRISTINA,action_subject,body,sender_email,sender_name)
            st=state_all();st[date_key]={"date":date_key,"date_display":date_display,"stage":"hold","content_valid":False,"reasons":reasons,"source":meta,"action_subject":action_subject,"updated_at":now_et().isoformat()};save_state(st)
            return {"action":"hold","reasons":reasons}
        return prepare_from_raw(date_key,date_display,raw,meta,token,dry_run)
    @router.get("/status")
    async def status(req:Request):
        auth(req);date_key,_=current();return {"date":date_key,"state":state_all().get(date_key),"persistent_data":str(DATA_DIR),"end_date":END_DATE or None}
    @router.post("/prepare")
    async def prepare(req:Request,dry_run:bool=False):auth(req);return prepare_impl(dry_run=dry_run)
    @router.post("/check-replies")
    async def check_replies(req:Request,dry_run:bool=False):
        auth(req);date_key,date_display=current();st=state_all();state=st.get(date_key)
        if not state:return {"action":"no_state"}
        token=get_token();processed=set(load(PROCESSED_FILE,[]))
        if state.get('stage')=='hold':
            subj=state.get('action_subject',f"[ACTION REQUIRED] LifeHouse OS content needed - {date_display}")
            msgs=gmail_search(token,f'subject:"{subj}" after:{date_key.replace("-","/")}',50)
            for item in msgs:
                if item['id'] in processed:continue
                msg=gmail_get(token,item['id']);h=headers_map(msg.get('payload',{}));frm=h.get('from','').lower()
                if 'kristina' not in frm:continue
                body=clean_reply(extract_gmail_body(msg.get('payload',{})))
                if dry_run:return {"action":"would_process_kristina_reply","message_id":item['id'],"chars":len(body)}
                if re.search(r'\b(updated|uploaded|ready|revised|fixed)\b',body,re.I) and len(body)<500:
                    result=prepare_impl(force=True)
                else:
                    result=prepare_from_raw(date_key,date_display,body,{"type":"kristina_reply","message_id":item['id']},token,False,"Updated content received from Kristina")
                processed.add(item['id']);atomic_json_write(PROCESSED_FILE,sorted(processed));return result
            return {"action":"no_reply","stage":"hold"}
        if state.get('stage')=='review_sent':
            drafts=load_drafts();draft=drafts.get(state.get('draft_id'),{})
            if draft.get('status')=='sent':state['stage']='sent';state['updated_at']=now_et().isoformat();st[date_key]=state;save_state(st);return {"action":"already_sent"}
            msgs=gmail_search(token,f'subject:"{state.get("review_subject")}" after:{date_key.replace("-","/")}',50)
            for item in msgs:
                if item['id'] in processed:continue
                msg=gmail_get(token,item['id']);h=headers_map(msg.get('payload',{}));frm=h.get('from','').lower()
                if not any(a.lower() in frm for a in approvers):continue
                body=clean_reply(extract_gmail_body(msg.get('payload',{})));low=body.lower()
                if dry_run:return {"action":"would_approve" if any(x in low for x in APPROVAL_WORDS) else "would_apply_changes","message_id":item['id']}
                if any(x in low for x in APPROVAL_WORDS):
                    result=send_draft(state['draft_id'],frm);state['stage']=result.get('status','partial');state['updated_at']=now_et().isoformat();st[date_key]=state;save_state(st)
                else:
                    revised=revise_with_glm(state['raw_content'],body);sections=deterministic_sections(revised);email_html=build_beta_email(sections,date_display);subject=state['subject'];new=create_draft(subject,email_html,revised,date_display);old=drafts.get(state['draft_id']);
                    if old:old['status']='revised';save_drafts(drafts)
                    did=new['draft_id'];review_subject=f"[REVIEW] LifeHouse OS Beta Email Draft - {date_display} (Revised)";send_email(token,','.join(approvers),review_subject,make_review(date_display,did,email_html,"Requested changes applied"),sender_email,sender_name);state.update({"stage":"review_sent","draft_id":did,"review_subject":review_subject,"raw_content":revised,"updated_at":now_et().isoformat()});st[date_key]=state;save_state(st);result={"action":"revised_review_sent","draft_id":did}
                processed.add(item['id']);atomic_json_write(PROCESSED_FILE,sorted(processed));return result
            return {"action":"no_reply","stage":"review_sent"}
        return {"action":"no_op","stage":state.get('stage')}
    @router.post("/auto-send")
    async def auto_send(req:Request,dry_run:bool=False):
        auth(req);date_key,_=current();st=state_all();state=st.get(date_key)
        if not state:return {"action":"no_state"}
        if state.get('stage')!='review_sent' or not state.get('content_valid'):return {"action":"blocked","stage":state.get('stage'),"content_valid":state.get('content_valid')}
        drafts=load_drafts();draft=drafts.get(state.get('draft_id'),{})
        if draft.get('status')=='sent':state['stage']='sent';st[date_key]=state;save_state(st);return {"action":"already_sent"}
        if dry_run:return {"action":"would_auto_send","draft_id":state.get('draft_id')}
        result=send_draft(state['draft_id'],'auto-send@n8n');state['stage']=result.get('status','partial');state['updated_at']=now_et().isoformat();st[date_key]=state;save_state(st);return result
    return router
