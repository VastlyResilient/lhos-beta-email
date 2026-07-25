"""Cloud orchestration endpoints invoked by n8n. All actions are fail-closed and idempotent."""
import base64, hashlib, html, hmac, json, os, re, tempfile, zipfile, fcntl
from contextlib import contextmanager
from datetime import datetime, timedelta
from email import message_from_bytes
from email.header import decode_header, make_header
from email.utils import parseaddr
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
SEND_POLICY=os.getenv("SEND_POLICY","ON_APPROVAL").strip().upper()
if SEND_POLICY not in ("ON_APPROVAL","AT_GATE"):SEND_POLICY="ON_APPROVAL"
GLM_API_KEY=os.getenv("GLM_API_KEY","")
GLM_BASE_URL=os.getenv("GLM_BASE_URL","https://api.z.ai/api/paas/v4")
DATA_DIR=Path(os.getenv("DATA_DIR","/data"));STATE_FILE=DATA_DIR/"automation_state.json";PROCESSED_FILE=DATA_DIR/"processed_messages.json";ALERTS_FILE=DATA_DIR/"watchdog_alerts.json";REPORTS_FILE=DATA_DIR/"daily_reports.json";HEARTBEAT_FILE=DATA_DIR/"automation_heartbeat.json";AUTOMATION_LOCK=DATA_DIR/"automation.lock"
KRISTINA="kristina@freedomforgeai.com"
APPROVAL_WORDS=("approved","approve","looks good","send it","send the email","good to send","go ahead","confirmed","confirm","lgtm","ship it","ship this","release it","ready to send")
REVISION_WORDS=("change","revise","revision","edit","replace","remove","add","fix","correct","update","rewrite","adjust")
HOLD_PATTERNS=(r"\bdo not send\b",r"\bdon[’']?t send\b",r"\bnot approved\b",r"\bhold (?:off|this|the email)\b",r"\bwait\b",r"\bnot ready\b")

def classify_instruction(text):
    low=re.sub(r"\s+"," ",(text or "").lower()).strip()
    if any(re.search(p,low) for p in HOLD_PATTERNS):return "hold"
    if any(re.search(rf"\b{re.escape(w)}\b",low) for w in REVISION_WORDS):return "revise"
    if any(w in low for w in APPROVAL_WORDS):return "approve"
    return "ambiguous"


def now_et(): return datetime.now(ET)
def load(path,default):
    try:return json.loads(path.read_text())
    except Exception:return default

@contextmanager
def automation_lock():
    AUTOMATION_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(AUTOMATION_LOCK, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield

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

def docx_text(data):
    with zipfile.ZipFile(BytesIO(data)) as z:
        import xml.etree.ElementTree as ETX
        root=ETX.fromstring(z.read("word/document.xml"));ns='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
        return '\n'.join(''.join(t.text or '' for t in p.iter(ns+'t')) for p in root.iter(ns+'p')).strip()

def gmail_docx_attachments(token,msg_id,payload):
    texts=[]
    def walk(part):
        filename=(part.get('filename') or '').lower();aid=part.get('body',{}).get('attachmentId')
        if aid and filename.endswith('.docx'):
            r=httpx.get(f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}/attachments/{aid}",headers=google_headers(token),timeout=30)
            if r.status_code!=200:raise RuntimeError(f"Gmail attachment fetch failed: {r.status_code}")
            data=base64.urlsafe_b64decode(r.json().get('data','')+'===');texts.append(docx_text(data))
        for child in part.get('parts',[]) or []:walk(child)
    walk(payload);return '\n'.join(x for x in texts if x)

def drive_source(token,date_key):
    dt=datetime.strptime(date_key,"%Y-%m-%d");name=dt.strftime("%y%m%d")+".docx";q=f"'{DRIVE_FOLDER_ID}' in parents and trashed=false and name='{name}'"
    r=httpx.get("https://www.googleapis.com/drive/v3/files",headers=google_headers(token),params={"q":q,"fields":"files(id,name,size,modifiedTime,lastModifyingUser(displayName,emailAddress))"},timeout=30)
    if r.status_code!=200:raise RuntimeError(f"Drive lookup failed: {r.status_code} {r.text}")
    files=r.json().get("files",[])
    if not files:return None,"",{"name":name,"missing":True}
    f=files[0];r=httpx.get(f"https://www.googleapis.com/drive/v3/files/{f['id']}",headers=google_headers(token),params={"alt":"media"},timeout=60)
    if r.status_code!=200:raise RuntimeError(f"Drive download failed: {r.status_code}")
    raw=docx_text(r.content)
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


AUTH_REQUIRED=os.getenv("REQUIRE_DMARC","1").strip() not in ("0","false","False")
def auth_verdicts(headers_lower):
    """Parse Gmail's Authentication-Results. Gmail stamps this itself at delivery time,
    so it cannot be forged by the sender. ARC-Authentication-Results is used only as a
    fallback for legitimately forwarded mail."""
    raw=headers_lower.get("authentication-results") or ""
    if not raw:raw=headers_lower.get("arc-authentication-results") or ""
    flat=" ".join(raw.split())
    def grab(name):
        m=re.search(name+r"=(\w+)",flat);return m.group(1).lower() if m else None
    dom=re.search(r"header\.i=@([\w.\-]+)",flat) or re.search(r"header\.d=([\w.\-]+)",flat)
    return {"raw":flat[:400],"dkim":grab("dkim"),"spf":grab("spf"),"dmarc":grab("dmarc"),"dkim_domain":(dom.group(1).lower() if dom else None)}

def sender_authenticated(addr,headers_lower):
    """Fail closed: an approver's message must be cryptographically attributable.
    Accept on dmarc=pass, or dkim=pass with the DKIM domain aligned to the From domain."""
    v=auth_verdicts(headers_lower)
    if not AUTH_REQUIRED:return True,{**v,"enforced":False}
    from_domain=(addr.rsplit("@",1)[-1] or "").lower()
    if v["dmarc"]=="pass":return True,{**v,"basis":"dmarc_pass"}
    if v["dkim"]=="pass" and v["dkim_domain"] and from_domain and (v["dkim_domain"]==from_domain or from_domain.endswith("."+v["dkim_domain"])):
        return True,{**v,"basis":"dkim_aligned"}
    return False,{**v,"basis":"unauthenticated"}

def configure_router(*,get_token,send_email,create_draft,load_drafts,save_drafts,send_draft,approve_draft,approvers,approval_senders,public_url,sender_email,sender_name):
    router=APIRouter(prefix="/api/lhos/automation")
    def state_all():return load(STATE_FILE,{})
    def heartbeat(name):
        h=load(HEARTBEAT_FILE,{});h[name]=now_et().isoformat();atomic_json_write(HEARTBEAT_FILE,h);return h
    def save_state(d):atomic_json_write(STATE_FILE,d)
    def current():
        now=now_et();return now.strftime("%Y-%m-%d"),now.strftime("%B %d, %Y")
    def make_review(date_display,approval_path,email_html,subtitle="Daily content validated"):
        url=approval_path if approval_path.startswith("http") else public_url + approval_path
        preview=email_html.replace("RECIPIENT_NAME_PLACEHOLDER","Hello Beta Tester!").replace("UNSUB_URL_PLACEHOLDER","#")
        return f'<html><body><div style="background:#0E1B33;color:white;padding:20px;text-align:center;font-family:Nunito,Arial,sans-serif"><h2>{html.escape(subtitle)}</h2><p>Review the validated email below. Approve, edit, or request changes.</p><a style="display:inline-block;background:#4BC0C4;color:white;padding:14px 30px;text-decoration:none;font-weight:700" href="{url}">Review, Edit, or Approve for 3 PM</a></div>{preview}</body></html>'
    def prepare_from_raw(date_key,date_display,raw,source,token,dry_run=False,subtitle="Daily content validated"):
        ok,reasons=validate_daily_content(raw)
        if not ok:return {"action":"hold","valid":False,"reasons":reasons,"source":source}
        sections=deterministic_sections(raw);email_html=build_beta_email(sections,date_display);subject=f"LifeHouse OS Beta Update - {date_display}"
        if dry_run:return {"action":"would_send_review","valid":True,"sections":list(sections),"subject":subject,"source":source}
        result=create_draft(subject,email_html,raw,date_display);did=result['draft_id'];review_subject=f"[REVIEW] LifeHouse OS Beta Email Draft - {date_display}"
        if not gmail_subject_sent_any(token,review_subject,date_key):send_email(token,','.join(approvers),review_subject,make_review(date_display,result.get("approval_url", f"/lhos/approve/{did}"),email_html,subtitle),sender_email,sender_name)
        st=state_all();_created=now_et();st[date_key]={"date":date_key,"date_display":date_display,"stage":"review_sent","content_valid":True,"draft_id":did,"subject":subject,"review_subject":review_subject,"source":source,"raw_content":raw,"review_sent_at":_created.isoformat(),"deadline":"15:00 America/New_York","updated_at":_created.isoformat()};save_state(st)
        return {"action":"review_sent","draft_id":did,"subject":subject}
    def prepare_impl(dry_run=False,force=False):
        date_key,date_display=current()
        if END_DATE and date_key>END_DATE:return {"action":"stopped","reason":"end_date","end_date":END_DATE}
        st=state_all();existing=st.get(date_key,{})
        if not force and existing.get("stage") in ("review_sent","approved","sending","partial","sent","sent_external"):return {"action":"daily_complete" if existing.get("stage") in ("sent","sent_external") else "no_op","stage":existing.get("stage"),"draft_id":existing.get("draft_id")}
        token=get_token();subject=f"LifeHouse OS Beta Update - {date_display}"
        if gmail_subject_sent_any(token,subject,date_key):
            if not dry_run:
                st=state_all();st[date_key]={"date":date_key,"date_display":date_display,"stage":"sent_external","content_valid":True,"subject":subject,"updated_at":now_et().isoformat()};save_state(st)
            return {"action":"already_sent","subject":subject}
        st=state_all();existing=st.get(date_key,{})
        if not force and existing.get('stage') in ('review_sent','approved','sent','sending','partial','not_sent','sent_external'):return {"action":"no_op","stage":existing['stage'],"draft_id":existing.get('draft_id')}
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
    def apply_instruction(date_key,date_display,state,actor,text,token,channel):
        kind=classify_instruction(text);st=state_all();drafts=load_drafts();draft=drafts.get(state.get("draft_id"),{})
        if not draft:return {"action":"draft_missing","kind":kind}
        if kind=="approve":
            approved_draft_id=state["draft_id"]
            result=approve_draft(approved_draft_id,f"{actor} via {channel}");state.update({"stage":"approved","approved_by":actor,"approval_channel":channel,"approval_text":text[:1000],"approved_at":now_et().isoformat(),"updated_at":now_et().isoformat()});st[date_key]=state;save_state(st)
            if SEND_POLICY!="ON_APPROVAL":
                return {"action":"approval_recorded","send_policy":SEND_POLICY,"scheduled_for":"15:00 America/New_York","draft_id":approved_draft_id,"actor":actor,**result}
            # ON_APPROVAL: deliver now, but only the exact approved revision and only if still valid.
            fresh=state_all().get(date_key) or {}
            if fresh.get("draft_id")!=approved_draft_id or fresh.get("stage")!="approved" or not fresh.get("content_valid"):
                return {"action":"approval_recorded_not_sent","send_policy":SEND_POLICY,"reason":"draft or state changed after approval","draft_id":approved_draft_id,"actor":actor}
            current_draft=load_drafts().get(approved_draft_id,{})
            if current_draft.get("status")!="approved":
                return {"action":"approval_recorded_not_sent","send_policy":SEND_POLICY,"reason":f"draft status {current_draft.get('status')}","draft_id":approved_draft_id,"actor":actor}
            send_result=send_draft(approved_draft_id,f"{actor} via {channel}")
            st=state_all();state=st.get(date_key,state);state.update({"stage":send_result.get("status","partial"),"sent_trigger":"on_approval","updated_at":now_et().isoformat()});st[date_key]=state;save_state(st)
            return {"action":"approved_and_sent","send_policy":SEND_POLICY,"draft_id":approved_draft_id,"actor":actor,**send_result}
        if kind=="hold":
            draft.update({"status":"pending_approval","approved_by":None,"approved_at":None});save_drafts(drafts);state.update({"stage":"review_sent","approved_by":None,"approval_channel":None,"updated_at":now_et().isoformat()});st[date_key]=state;save_state(st);return {"action":"send_held","draft_id":state["draft_id"],"actor":actor}
        if kind=="ambiguous":return {"action":"clarification_needed","draft_id":state["draft_id"],"actor":actor}
        revised=revise_with_glm(state.get("raw_content",draft.get("text_body","")),text);sections=deterministic_sections(revised);email_html=build_beta_email(sections,date_display);subject=state["subject"];new=create_draft(subject,email_html,revised,date_display)
        drafts=load_drafts();old_draft=drafts.get(state["draft_id"])
        if not old_draft:raise RuntimeError("Original draft disappeared during revision")
        old_draft["status"]="revised";old_draft["revised_at"]=now_et().isoformat();save_drafts(drafts)
        did=new["draft_id"];count=int(state.get("revision_count",0))+1;review_subject=f"[REVIEW] LifeHouse OS Beta Email Draft - {date_display} (Revision {count})";send_email(token,','.join(approvers),review_subject,make_review(date_display,new.get("approval_url",f"/lhos/approve/{did}"),email_html,f"Revision {count} applied from {actor}"),sender_email,sender_name)
        state.update({"stage":"review_sent","draft_id":did,"review_subject":review_subject,"raw_content":revised,"revision_count":count,"approved_by":None,"approval_channel":None,"last_revision_by":actor,"last_revision_channel":channel,"updated_at":now_et().isoformat()});st[date_key]=state;save_state(st);return {"action":"revised_review_sent","draft_id":did,"revision_count":count,"actor":actor}
    @router.get("/connectors")
    async def connectors(req:Request):
        auth(req);token=get_token();checks={}
        for name,url,params in [
            ("gmail","https://gmail.googleapis.com/gmail/v1/users/me/profile",None),
            ("drive","https://www.googleapis.com/drive/v3/about",{"fields":"user(displayName)"}),
            ("contacts","https://people.googleapis.com/v1/contactGroups",{"pageSize":1,"groupFields":"name"})]:
            r=httpx.get(url,headers=google_headers(token),params=params,timeout=30);checks[name]=r.status_code
            if r.status_code!=200:raise HTTPException(status_code=503,detail={"connector":name,"status":r.status_code,"body":r.text[:300]})
        return {"status":"ok","checks":checks}
    @router.get("/status")
    async def status(req:Request):
        auth(req);date_key,_=current();return {"date":date_key,"state":state_all().get(date_key),"heartbeat":load(HEARTBEAT_FILE,{}),"persistent_data":str(DATA_DIR),"end_date":END_DATE or None}
    @router.post("/prepare")
    async def prepare(req:Request,dry_run:bool=False):
        auth(req)
        if not dry_run and not (7 <= now_et().hour < 15):return {"action":"outside_active_window","window":"07:00-15:00 America/New_York"}
        with automation_lock(): heartbeat("prepare");return prepare_impl(dry_run=dry_run)
    @router.post("/check-replies")
    async def check_replies(req:Request,dry_run:bool=False):
        auth(req)
        if not dry_run and not (7 <= now_et().hour < 15):return {"action":"outside_active_window","window":"07:00-15:00 America/New_York"}
        with automation_lock():
            heartbeat("check_replies");date_key,date_display=current();st=state_all();state=st.get(date_key)
            if not state:return {"action":"no_state"}
            if state.get("stage") in ("sent","sent_external"):return {"action":"daily_complete","stage":state.get("stage"),"draft_id":state.get("draft_id")}
            token=get_token();processed=set(load(PROCESSED_FILE,[]));allowed={a.strip().lower() for a in approval_senders}
            auth_query=' '.join('from:'+a for a in sorted(allowed));queries=[f'in:inbox after:{date_key.replace("-","/")} {{{auth_query}}}',f'in:inbox after:{date_key.replace("-","/")} {{subject:"LifeHouse OS" subject:"beta email" subject:LHOS}}']
            ids={}
            for q in queries:
                for item in gmail_search(token,q,100):ids[item['id']]=item
            records=[]
            for mid in ids:
                if mid in processed:continue
                msg=gmail_get(token,mid);h=headers_map(msg.get('payload',{}));addr=parseaddr(h.get('from',''))[1].strip().lower();subj=dec_header(h.get('subject',''));body=clean_reply(extract_gmail_body(msg.get('payload',{}))+'\n'+gmail_docx_attachments(token,mid,msg.get('payload',{})))
                auto=str(h.get('auto-submitted','')).lower();precedence=str(h.get('precedence','')).lower();is_auto=('auto-replied' in auto or 'auto-generated' in auto or precedence in ('bulk','junk','auto_reply') or bool(h.get('x-autoreply')) or bool(h.get('x-autorespond')) or 'out of office' in subj.lower() or subj.lower().startswith('automatic reply'))
                authed,verdict=sender_authenticated(addr,h)
                records.append({"id":mid,"internal":int(msg.get("internalDate",0)),"addr":addr,"subject":subj,"body":body,"authenticated":authed,"auth":verdict,"is_auto":is_auto})
            records.sort(key=lambda x:(x["internal"],x["id"]));actions=[]
            for rec in records:
                current_state=state_all().get(date_key) or state;stage=current_state.get("stage");addr=rec["addr"];body=rec["body"];subj=rec["subject"];combined=(subj+'\n'+body).lower();authorized=(addr in allowed) and bool(rec.get("authenticated")) and not rec.get("is_auto")
                relevant=bool(re.search(r'\b(lifehouse|lhos|beta(?: email| update)?|daily briefing)\b',combined,re.I)) or (current_state.get('review_subject','').lower() in subj.lower() if current_state.get('review_subject') else False) or (current_state.get('action_subject','').lower() in subj.lower() if current_state.get('action_subject') else False)
                if not authorized:
                    if not dry_run:
                        current_state["ignored_unauthorized_inbox_count"]=int(current_state.get("ignored_unauthorized_inbox_count",0))+1;current_state["last_ignored_unauthorized_at"]=now_et().isoformat();st=state_all();st[date_key]=current_state;save_state(st);processed.add(rec["id"])
                    why=("auto_reply" if rec.get("is_auto") else ("failed_dmarc_authentication" if addr in allowed else "sender_not_allow_listed"))
                    if addr in allowed and not rec.get("authenticated"):
                        current_state["spoof_attempts"]=int(current_state.get("spoof_attempts",0))+1;current_state["last_spoof_at"]=now_et().isoformat()
                        if not dry_run:st=state_all();st[date_key]=current_state;save_state(st)
                    actions.append({"action":"would_ignore_unauthorized" if dry_run else "ignored_unauthorized","message_id":rec["id"],"reason":why,"auth":rec.get("auth",{}).get("basis")});continue
                if stage in ("sent","sent_external"):break
                if dry_run and (relevant or stage=="hold"):
                    return {"action":"would_process_inbox","message_id":rec["id"],"stage":stage,"from":addr,"classification":classify_instruction(body)}
                if stage=="hold":
                    ready=bool(re.search(r'\b(updated|uploaded|ready|revised|fixed)\b',body,re.I));ok_content,_=validate_daily_content(body)
                    if ready and len(body)<500 and (relevant or 'content' in combined):result=prepare_impl(force=True)
                    elif ok_content:result=prepare_from_raw(date_key,date_display,body,{"type":"authorized_direct_email","message_id":rec["id"],"from":addr},token,False,f"Updated content received from {addr}")
                    else:
                        processed.add(rec["id"]);actions.append({"action":"ignored_unrelated_or_incomplete","message_id":rec["id"]});continue
                elif stage in ("review_sent","approved"):
                    kind=classify_instruction(body);thread_bound=bool(current_state.get('review_subject') and current_state.get('review_subject').lower() in subj.lower())
                    if not (thread_bound or relevant):processed.add(rec["id"]);actions.append({"action":"ignored_unrelated","message_id":rec["id"]});continue
                    if dry_run:return {"action":f"would_{kind}","message_id":rec['id'],"source":"inbox"}
                    ok_content,_=validate_daily_content(body)
                    if ok_content and len(body)>=180:
                        result=prepare_from_raw(date_key,date_display,body,{"type":"authorized_replacement_email","message_id":rec["id"],"from":addr},token,False,f"Replacement content received from {addr}")
                        drafts=load_drafts();old=drafts.get(current_state.get('draft_id'))
                        if old and old.get('status')!='sent':old['status']='revised';save_drafts(drafts)
                    else:
                        result=apply_instruction(date_key,date_display,current_state,addr,body,token,"email")
                        if result.get("action")=="clarification_needed":
                            clarification_subject=f"[CLARIFICATION] {current_state.get('review_subject')}";clarification_body='<p>Hi,</p><p>I could not determine whether your message was an approval or a revision request. Please reply with either <strong>approve/send</strong>, <strong>hold</strong>, or the exact change you want made.</p><p>Warm regards,<br>Iris</p>';send_email(token,addr,clarification_subject,clarification_body,sender_email,sender_name)
                else:processed.add(rec["id"]);actions.append({"action":"no_op","stage":stage,"message_id":rec["id"]});continue
                processed.add(rec["id"]);actions.append({**result,"message_id":rec["id"]})
            if not dry_run:atomic_json_write(PROCESSED_FILE,sorted(processed))
            if actions:return {"action":"inbox_processed","processed_count":len(actions),"results":actions}
            return {"action":"no_relevant_inbox","stage":state.get("stage")}
    @router.post("/decision")
    async def decision(req:Request,dry_run:bool=False):
        auth(req)
        if not dry_run and not (7 <= now_et().hour < 15):return {"action":"outside_active_window","window":"07:00-15:00 America/New_York"}
        with automation_lock():
            payload=await req.json();actor=str(payload.get("actor","")).strip();text=str(payload.get("text","")).strip();channel=str(payload.get("channel","imessage")).strip();message_id=str(payload.get("message_id","")).strip()
            if actor not in ("Kristina","Thomas Appling","Bobby"):raise HTTPException(status_code=403,detail="Actor is not an authorized approver")
            if not message_id or not text:raise HTTPException(status_code=400,detail="message_id and text are required")
            key=f"{channel}:{message_id}";processed=set(load(PROCESSED_FILE,[]))
            if key in processed:return {"action":"already_processed","message_id":message_id}
            date_key,date_display=current();st=state_all();state=st.get(date_key)
            if not state:return {"action":"no_state"}
            token=get_token()
            if state.get("stage")=="hold":
                if re.search(r'\b(updated|uploaded|ready|revised|fixed)\b',text,re.I):result=prepare_impl(force=True)
                elif len(text)>=180:result=prepare_from_raw(date_key,date_display,text,{"type":channel,"message_id":message_id,"actor":actor},token,False,f"Updated content received from {actor}")
                else:result={"action":"clarification_needed","actor":actor}
            elif state.get("stage") in ("review_sent","approved"):
                if dry_run:return {"action":f"would_{classify_instruction(text)}","actor":actor}
                result=apply_instruction(date_key,date_display,state,actor,text,token,channel)
            else:result={"action":"no_op","stage":state.get("stage")}
            if not dry_run:
                processed.add(key);atomic_json_write(PROCESSED_FILE,sorted(processed))
            return result
    @router.post("/manual-send")
    async def manual_send(req:Request,dry_run:bool=False):
        auth(req)
        with automation_lock():
            payload=await req.json();date_key,date_display=current();expected=f"SEND {date_key} LATE TO ACTIVE BETA TESTERS"
            if payload.get("date")!=date_key or payload.get("confirm")!=expected:
                raise HTTPException(status_code=400,detail="Exact current-date late-send confirmation is required")
            if now_et().hour < 15:raise HTTPException(status_code=409,detail="Manual late-send override is available only after the 3 PM deadline")
            token=get_token();subject=f"LifeHouse OS Beta Update - {date_display}"
            if gmail_subject_sent_any(token,subject,date_key):return {"action":"already_sent","subject":subject}
            f,raw,meta=drive_source(token,date_key);ok,reasons=validate_daily_content(raw)
            if not ok:raise HTTPException(status_code=409,detail={"message":"Current Drive content is invalid","reasons":reasons,"source":meta})
            sections=deterministic_sections(raw);email_html=build_beta_email(sections,date_display)
            if dry_run:return {"action":"would_manual_send","subject":subject,"source":meta,"content_chars":len(raw),"confirm":expected}
            created=create_draft(subject,email_html,raw,date_display);did=created["draft_id"];approval=approve_draft(did,"Bobby explicit late-send authorization",manual_override=True)
            st=state_all();state={"date":date_key,"date_display":date_display,"stage":"approved","content_valid":True,"draft_id":did,"subject":subject,"source":meta,"raw_content":raw,"approved_by":"Bobby","approval_channel":"explicit chat authorization","approved_at":now_et().isoformat(),"manual_override":True,"updated_at":now_et().isoformat()};st[date_key]=state;save_state(st)
            result=send_draft(did,"Bobby explicit late-send authorization");state["stage"]=result.get("status","partial");state["updated_at"]=now_et().isoformat();st=state_all();st[date_key]=state;save_state(st);return {"action":"manual_send_executed","draft_id":did,"approval":approval,**result}
    def notify_not_sent(date_key,date_display,state,reason,token,dry_run):
        subject=f"[NOT SENT] LifeHouse OS beta update - {date_display}"
        if dry_run:return {"action":"would_notify_not_sent","reason":reason,"stage":state.get("stage") if state else None}
        if not gmail_subject_sent_any(token,subject,date_key):
            body=f"<p>Hi Kristina,</p><p>Today's LifeHouse OS beta email was <strong>not sent</strong> at 3:00 PM Eastern.</p><p>{html.escape(reason)}</p><p>No beta tester received an email.</p><p>Warm regards,<br>Iris</p>";send_email(token,KRISTINA,subject,body,sender_email,sender_name)
        st=state_all();base=state or {"date":date_key,"date_display":date_display,"content_valid":False};base.update({"stage":"not_sent","not_sent_reason":reason,"not_sent_at":now_et().isoformat(),"updated_at":now_et().isoformat()});st[date_key]=base;save_state(st);return {"action":"not_sent","reason":reason}
    @router.post("/auto-send")
    async def auto_send(req:Request,dry_run:bool=False):
        auth(req)
        with automation_lock():
            heartbeat("auto_send")
            if now_et().hour < 15:return {"action":"too_early","scheduled_for":"15:00 America/New_York"}
            date_key,date_display=current();st=state_all();state=st.get(date_key);token=get_token()
            if not state:return notify_not_sent(date_key,date_display,None,"No dated content or review state was available by the 3:00 PM deadline.",token,dry_run)
            drafts=load_drafts();draft=drafts.get(state.get('draft_id'),{})
            if draft.get('status')=='sent':state['stage']='sent';state['updated_at']=now_et().isoformat();st[date_key]=state;save_state(st);return {"action":"already_sent"}
            if draft.get('status')=='approved' and state.get('content_valid'):
                state['stage']='approved';st[date_key]=state;save_state(st)
            if state.get('stage')!='approved' or draft.get('status')!='approved' or not state.get('content_valid'):
                reason=("No authorized approver gave clear final approval." if state.get('content_valid') else "The dated source was missing or invalid.")+(f" Send policy in effect: {SEND_POLICY}." if SEND_POLICY=="ON_APPROVAL" else "")
                return notify_not_sent(date_key,date_display,state,reason,token,dry_run)
            if dry_run:return {"action":"would_send_approved","draft_id":state.get('draft_id'),"approved_by":draft.get('approved_by')}
            result=send_draft(state['draft_id'],draft.get('approved_by') or 'approved@n8n');state['stage']=result.get('status','partial');state['updated_at']=now_et().isoformat();st[date_key]=state;save_state(st);return result
    @router.post("/reconcile")
    async def reconcile(req:Request,dry_run:bool=False):
        auth(req)
        with automation_lock():
            heartbeat("reconcile");date_key,_=current();st=state_all();state=st.get(date_key)
            if not state:return {"action":"no_state"}
            drafts=load_drafts();draft=drafts.get(state.get("draft_id"),{})
            if draft.get("status")=="sent":
                state["stage"]="sent";state["updated_at"]=now_et().isoformat();st[date_key]=state;save_state(st);return {"action":"already_sent"}
            # Reconcile only a batch already authorized before the 3 PM gate.
            authorized=bool(draft.get("approved_by")) and draft.get("status") in ("sending","partial","approved")
            if not authorized:return {"action":"no_op","stage":state.get("stage"),"draft_status":draft.get("status")}
            if dry_run:return {"action":"would_reconcile","draft_id":state.get("draft_id"),"draft_status":draft.get("status")}
            result=send_draft(state["draft_id"],draft.get("approved_by") or "reconcile@n8n");state["stage"]=result.get("status","partial");state["updated_at"]=now_et().isoformat();st[date_key]=state;save_state(st);return result
    @router.post("/close-out")
    async def close_out(req:Request,dry_run:bool=False):
        auth(req)
        with automation_lock():
            heartbeat("close_out");date_key,date_display=current();state=state_all().get(date_key) or {};reports=load(REPORTS_FILE,{})
            if reports.get(date_key) and not dry_run:return {"action":"already_reported","date":date_key,"stage":reports[date_key].get("stage")}
            stage=state.get("stage") or "no_state";ledger={}
            try:
                led=load(DATA_DIR/"delivery_ledger.json",{});ledger=led.get(date_key) or led.get(state.get("draft_id"),{}) or {}
            except Exception:ledger={}
            recips=ledger.get("recipients",{}) if isinstance(ledger,dict) else {}
            counts={"sent":0,"uncertain":0,"error":0,"reserved":0}
            for r in (recips.values() if isinstance(recips,dict) else []):
                stt=(r or {}).get("status") if isinstance(r,dict) else None
                if stt in counts:counts[stt]+=1
                elif stt:counts["reserved"]+=1
            terminal={"sent":"DELIVERED","sent_external":"DELIVERED","not_sent":"NOT SENT (failed closed)","hold":"HELD - content missing or invalid","approved":"APPROVED, DELIVERY INCOMPLETE","partial":"PARTIAL DELIVERY","review_sent":"AWAITING APPROVAL","no_state":"NO ACTIVITY RECORDED"}.get(stage,stage.upper())
            # A failed-closed day whose content WAS valid still needs human attention:
            # the edition did not reach beta testers even though usable content existed.
            incident=stage in ("approved","partial","sending","review_sent","hold","no_state") or (stage=="not_sent" and bool(state.get("content_valid")))
            hb=load(HEARTBEAT_FILE,{})
            rows="".join(f"<tr><td style='padding:4px 12px 4px 0'>{html.escape(k)}</td><td style='padding:4px 0'><strong>{v}</strong></td></tr>" for k,v in [
                ("Business date",date_display),("Send policy",SEND_POLICY),("Outcome",terminal),
                ("Delivered",str(counts["sent"])),("Uncertain",str(counts["uncertain"])),("Errors",str(counts["error"])),
                ("Draft revision",str(state.get("draft_id") or "none")),("Approved by",str(state.get("approved_by") or "not approved")),
                ("Source file",str((state.get("source") or {}).get("name") or "none")),("Last prepare heartbeat",str(hb.get("prepare") or "none"))])
            note=("<p style='color:#b03030'><strong>An incident is open.</strong> "+html.escape(str(state.get("not_sent_reason") or "Delivery did not reach a verified terminal state; automated remediation and reconciliation are active."))+"</p>") if incident else "<p style='color:#1f7a4d'><strong>No action needed.</strong> Today's edition reached a verified terminal state.</p>"
            subject=f"[IRIS DAILY REPORT] {date_display} - {terminal}"
            body=f"<div style='font-family:Nunito,Arial,sans-serif;color:#0E1B33'><h2 style='color:#0E1B33'>LifeHouse OS pipeline close-out</h2>{note}<table style='border-collapse:collapse'>{rows}</table><p style='font-size:13px;color:#555'>This report is generated automatically so no one has to ask whether the edition went out.</p></div>"
            if dry_run:return {"action":"would_report","subject":subject,"stage":stage,"counts":counts,"incident":incident}
            send_email(get_token(),"bobbyatf@gmail.com",subject,body,sender_email,sender_name)
            reports[date_key]={"stage":stage,"terminal":terminal,"counts":counts,"incident":incident,"reported_at":now_et().isoformat()};atomic_json_write(REPORTS_FILE,reports)
            return {"action":"report_sent","stage":stage,"terminal":terminal,"counts":counts,"incident":incident}
    @router.post("/watchdog")
    async def watchdog(req:Request,dry_run:bool=False):
        auth(req)
        with automation_lock():
            date_key,date_display=current();now=now_et();state=state_all().get(date_key);hb=load(HEARTBEAT_FILE,{});reason=None
            if now.hour < 7:return {"action":"too_early","time":now.isoformat()}
            if 7 <= now.hour < 15:
                if state and state.get("stage") in ("sent","sent_external"):return {"action":"healthy_or_expected_terminal_state","stage":state.get("stage")}
                stamp=hb.get("prepare");last=None
                if stamp:
                    try:last=datetime.fromisoformat(stamp)
                    except:pass
                stale=not last or (now-last).total_seconds()>240
                if (now.hour>7 or now.minute>=10) and not state:reason="No daily cloud state exists more than 10 minutes after the 7 AM start; n8n may not be reaching Railway."
                elif stale:reason="The n8n preparation heartbeat is missing or more than four minutes stale during the active window."
                else:return {"action":"healthy_active_window","stage":state.get("stage") if state else None,"last_prepare_at":stamp}
            elif not state:reason="No cloud automation state exists after 3:00 PM ET; the preparation schedule may have been missed."
            elif state.get("stage") in ("sending","partial","approved"):reason=f"Authorized batch is stuck in {state.get('stage')} and requires reconciliation."
            elif state.get("stage") in ("review_sent","hold"):reason=f"The 3:00 PM deadline handler did not finalize stage {state.get('stage')}."
            if not reason:return {"action":"healthy_or_expected_terminal_state","stage":state.get("stage") if state else None}
            key=hashlib.sha256((date_key+reason).encode()).hexdigest();alerts=load(ALERTS_FILE,{})
            if alerts.get(key):return {"action":"alert_already_sent","reason":reason}
            if dry_run:return {"action":"would_alert_bobby","reason":reason}
            token=get_token();subject=f"[LHOS AUTOMATION ALERT] {date_display}";body=f"<p><strong>LifeHouse OS cloud automation needs attention.</strong></p><p>{html.escape(reason)}</p><p>Date: {date_display}<br>Stage: {html.escape(str(state.get('stage') if state else 'no_state'))}</p><p>No beta email was sent by this watchdog.</p>"
            send_email(token,"bobbyatf@gmail.com",subject,body,sender_email,sender_name);alerts[key]={"sent_at":now.isoformat(),"reason":reason};atomic_json_write(ALERTS_FILE,alerts);return {"action":"alert_sent_to_bobby","reason":reason}
    return router
