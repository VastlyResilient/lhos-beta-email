"""Cloud orchestration endpoints invoked by n8n. All actions are fail-closed and idempotent."""
import base64, hashlib, html, hmac, json, os, re, tempfile, zipfile, fcntl
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta
from email import message_from_bytes
from email.header import decode_header, make_header
from email.utils import parseaddr, getaddresses
from email.mime.text import MIMEText
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo
import httpx
from fastapi import APIRouter, HTTPException, Request
from content_guard import validate_daily_content, validate_composed_sections, plain_text
from delivery import atomic_json_write
from iris_fallback import generate_for_date, usable_reference
from email_template import build_beta_email, build_varied_email

ET=ZoneInfo("America/New_York")
DRIVE_FOLDER_ID=os.getenv("LHOS_DRIVE_FOLDER_ID","1_u-jU56xvMCYO-yNmyAuZxkFuPVn-LHF")
AUTOMATION_TOKEN=os.getenv("LHOS_AUTOMATION_TOKEN","")
END_DATE=os.getenv("LHOS_END_DATE","").strip()
SEND_POLICY=os.getenv("SEND_POLICY","ON_APPROVAL").strip().upper()
if SEND_POLICY not in ("ON_APPROVAL","AT_GATE"):SEND_POLICY="ON_APPROVAL"
GLM_API_KEY=os.getenv("GLM_API_KEY","")
GLM_BASE_URL=os.getenv("GLM_BASE_URL","https://api.z.ai/api/paas/v4")
IRIS_CREATIVE_MODEL=os.getenv("IRIS_CREATIVE_MODEL","glm-4.7-flash")
DATA_DIR=Path(os.getenv("DATA_DIR","/data"));STATE_FILE=DATA_DIR/"automation_state.json";PROCESSED_FILE=DATA_DIR/"processed_messages.json";INBOX_FILE=DATA_DIR/"approver_inbox.json";ALERTS_FILE=DATA_DIR/"watchdog_alerts.json";REPORTS_FILE=DATA_DIR/"daily_reports.json";HEARTBEAT_FILE=DATA_DIR/"automation_heartbeat.json";AUTOMATION_LOCK=DATA_DIR/"automation.lock"
INBOX_AGENT_START_DATE=os.getenv("LHOS_INBOX_AGENT_START_DATE","2026-08-01").strip()
INBOX_CONTEXT_SINCE=os.getenv("LHOS_INBOX_CONTEXT_SINCE","2026-07-30").strip()
KRISTINA="kristina@freedomforgeai.com"
ALERT_EMAIL="bobbyatf@gmail.com"  # Policy invariant: ALL operational/failure alerts go only to Bobby.
IMESSAGE_ENABLED=False  # Policy invariant: LHOS iMessage intake/outbound is disabled in code.

def generate_fallback_bundle(date_key, reference=""):
    """Public seam for tests; provider failures fall back to curated copy when no reference exists."""
    return generate_for_date(date_key, reference, GLM_API_KEY, GLM_BASE_URL, IRIS_CREATIVE_MODEL)
APPROVAL_PATTERNS=(
    r"^(?:approved|approve|confirmed|lgtm|i approve|this is approved|the (?:draft|email|review) is approved|looks good(?: to me)?|this looks good)[.!]*$",
    r"^(?:approved|confirmed|looks good(?: to me)?)[,;: .—-]+(?:thanks|thank you)[.!]*$",
    r"^(?:approved|looks good(?: to me)?|good)[,;: .—-]+(?:please )?(?:send it(?: to (?:the )?beta testers)?|send the email|good to send|go ahead|ship it|release it)[.!]*$",
    r"^(?:please )?(?:send it(?: to (?:the )?beta testers)?|send the email|good to send|go ahead|ship it|release it|ready to send)[.!]*$",
)
REVISION_WORDS=("change","revise","revision","edit","replace","remove","add","fix","correct","update","rewrite","adjust")
HOLD_PATTERNS=(r"\bdo not send\b",r"\bdon[’']?t send\b",r"\bnot approved\b",r"\bhold (?:off|this|the email)\b",r"\bwait\b",r"\bnot ready\b",r"\bstop(?:ping)? (?:these|the|all)? ?emails\b",r"\bneed to stop\b",r"\bturn (?:it|this|the automation) off\b")
UNCERTAIN_PATTERNS=(r"\bhaven[’']?t confirm",r"\bnot confirm",r"\bplease confirm\b",r"\bmaybe\b",r"\bif .*ready\b",r"\bwhen ready\b")

def classify_instruction(text):
    raw=(text or "").strip();raw=re.split(r"\n(?:On .+ wrote:|From:)",raw,maxsplit=1,flags=re.I)[0]
    low=re.sub(r"\s+"," ",raw.lower()).strip()
    if any(re.search(p,low) for p in HOLD_PATTERNS):return "hold"
    if any(re.search(p,low) for p in UNCERTAIN_PATTERNS):return "ambiguous"
    if any(re.search(rf"\b{re.escape(w)}\b",low) for w in REVISION_WORDS):return "revise"
    if any(re.fullmatch(p,low) for p in APPROVAL_PATTERNS):return "approve"
    return "ambiguous"

RESUME_PATTERNS=(r"\bresume (?:the )?(?:emails|briefings|automation)\b",r"\brestart (?:the )?(?:emails|briefings|automation)\b",r"\bcontinue (?:the )?(?:emails|briefings|updates)\b")
STANDING_HOLD_PATTERNS=(r"\bstop (?:these|the|all)? ?emails\b",r"\bturn (?:it|this|the automation) off\b",r"\buntil (?:the )?next beta sprint\b")
EDITORIAL_HINTS=("beta","sprint","survey","briefing","lifehouse","content","email","tester","resident","waitlist","one-on-one","recruit")

def classify_direct_message(subject, body, *, thread_bound=False):
    """Classify authored text. This never grants approval by itself."""
    text=clean_reply(body or "");low=re.sub(r"\s+"," ",(text or "").lower()).strip()
    base=classify_instruction(text)
    if base=="hold":return "standing_hold" if any(re.search(p,low) for p in STANDING_HOLD_PATTERNS) or re.search(r"\bneed to stop\b|\bstopping these emails\b",low) else "hold"
    if any(re.search(p,low) for p in RESUME_PATTERNS):return "resume"
    ok,_=validate_daily_content(text)
    if ok:return "content"
    if base=="approve":return "approve" if thread_bound else "approval_unbound"
    if base=="revise" or any(x in low for x in ("only thing to share","make them more relevant","correct messages","please use","focus on")):return "revision" if thread_bound else "context"
    relevant=any(x in (subject+" "+low).lower() for x in EDITORIAL_HINTS)
    if "?" in text or re.search(r"\bplease respond\b",low):return "question"
    return "context" if relevant else "other"

def message_received_date(message):
    try:return datetime.fromtimestamp(int(message.get("internalDate",0))/1000,ET).date().isoformat()
    except Exception:return ""


_MONTHS={name.lower():i for i,name in enumerate(("January","February","March","April","May","June","July","August","September","October","November","December"),1)}
def message_target_date(subject,body,received_date):
    """Return an explicitly addressed edition date, else the received date."""
    text=plain_text((subject or "")+"\n"+clean_reply(body or ""));low=text.lower()
    try:received=datetime.strptime(received_date,"%Y-%m-%d").date()
    except Exception:return ""
    targeted=re.search(r"\b(?:daily update|briefing|beta email|email|content)\b.{0,60}?\b(?:for|dated)\s+([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,)?\s+(\d{4})",text,re.I)
    if targeted:
        month=_MONTHS.get(targeted.group(1).lower())
        if month:
            try:return datetime(int(targeted.group(3)),month,int(targeted.group(2))).date().isoformat()
            except ValueError:pass
    iso=re.search(r"\b(?:briefing|email|content)(?:\s+for)?\s+(\d{4}-\d{2}-\d{2})\b",low)
    if iso:
        try:return datetime.strptime(iso.group(1),"%Y-%m-%d").date().isoformat()
        except ValueError:pass
    if re.search(r"\b(?:tomorrow(?:’|'s)?|for tomorrow)\b",low):return (received+timedelta(days=1)).isoformat()
    if re.search(r"\b(?:today(?:’|'s)?|for today)\b",low):return received.isoformat()
    return received.isoformat()

def directly_addressed(payload, mailbox):
    values=_header_list(payload,"to")+_header_list(payload,"cc")
    return mailbox.strip().lower() in {addr.strip().lower() for _,addr in getaddresses(values)}

def load_inbox_state():
    if not INBOX_FILE.exists():return {"version":1,"messages":{},"context":[],"standing_hold":None}
    try:data=json.loads(INBOX_FILE.read_text())
    except Exception:raise RuntimeError("Approver inbox state is unreadable; inbox actions are blocked pending recovery")
    if not isinstance(data,dict) or not isinstance(data.get("messages"),dict) or not isinstance(data.get("context"),list):raise RuntimeError("Approver inbox state has an invalid shape; inbox actions are blocked pending recovery")
    return data

def record_context(data, rec, intent):
    if intent in ("approve","approval_unbound","other") or not rec.get("body"):return
    if any(x.get("message_id")==rec["id"] for x in data["context"]):return
    data["context"].append({"message_id":rec["id"],"received_date":rec.get("received_date"),"target_date":(rec.get("target_date") if intent=="content" else None),"from":rec["addr"],"subject":rec["subject"][:300],"intent":intent,"body":rec["body"][:6000]})
    data["context"]=data["context"][-40:]

def editorial_context(data, date_key, include_message_ids=False):
    cutoff=(datetime.strptime(date_key,"%Y-%m-%d").date()-timedelta(days=14)).isoformat()
    rows=[x for x in data.get("context",[]) if x.get("received_date","")>=cutoff and x.get("intent") not in ("approve","approval_unbound","other") and (not x.get("target_date") or x.get("target_date")<=date_key)][-12:]
    selected=[];remaining=24000
    for row in reversed(rows):
        piece=f"Authenticated message from {row.get('from')} on {row.get('received_date')} ({row.get('intent')}):\n{row.get('body','')}"
        separator=2 if selected else 0
        if remaining<=separator:break
        piece=piece[:remaining-separator];selected.append((row,piece));remaining-=len(piece)+separator
        if len(piece)==0 or remaining<=0:break
    selected.reverse();rendered="\n\n".join(piece for _,piece in selected);ids=[row.get("message_id") for row,_ in selected if row.get("message_id")]
    return (rendered,ids) if include_message_ids else rendered


def targeted_human_content(data,date_key):
    rows=[x for x in data.get("context",[]) if x.get("intent")=="content" and x.get("target_date")==date_key]
    for item in reversed(rows):
        ok,_=validate_daily_content(item.get("body",""))
        if ok:return item
    return None

def direct_reply_html(text):
    safe=html.escape(plain_text(text)[:3000]).replace("\n","<br>")
    return f"<p>Hi,</p><p>{safe}</p><p>Warm regards,<br>Iris</p>"

def direct_reply_copy(intent, result, current_subject=""):
    action=(result or {}).get("action","")
    if action in ("approved_and_sent","approval_recorded"):return "Thank you. Your authenticated approval was applied to the exact current review. The approved briefing has been released under the configured send policy."
    if action=="processing_deferred":return "I read your email and saved the instruction. I could not safely finish the requested content action yet, so no new beta delivery was authorized. I will retry during the running window; you do not need to resend it."
    if action=="dated_content_recorded":return "Thank you. I read and saved the complete content for the date named in your email. I will use it for that edition and send the resulting draft to all authorized reviewers before any beta delivery."
    if intent=="approval_unbound":return f"I read your approval instruction, but it was not tied to the exact current review. Please reply to the review email{(' “'+current_subject+'”') if current_subject else ''} so I can safely apply it."
    if intent in ("hold","standing_hold"):return "I read your instruction and have held delivery. I will not treat this message as approval. Future content will follow the guidance you provided and will return to the reviewers before any beta send."
    if intent=="resume":return "I read your instruction and cleared the standing hold. I will continue preparing relevant briefings and send each draft to the reviewers for approval before any beta delivery."
    if action=="review_sent" or intent in ("content","revision"):return "Thank you. I applied your content or editorial direction and sent the resulting draft to all authorized reviewers for approval. Nothing will go to beta recipients until an authenticated approval is received."
    if intent=="question":return "I read your message and recorded its guidance. If you intended a change to a specific briefing, please identify the date or reply directly to that review; otherwise I will use this context in the next relevant draft and return it for approval."
    if intent=="context":return "I read your message and recorded it as editorial guidance for upcoming briefings. I will use it to keep the content aligned with the current beta reality and send the next draft to all reviewers for approval."
    return "I received and read your email. I did not find a briefing instruction or approval that I can safely act on. If you intended a content change, please name the briefing date and the change you want."


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
    if r.status_code!=200:raise RuntimeError(f"Gmail search failed with HTTP {r.status_code}")
    return r.json().get("messages",[])

def gmail_get(token,msg_id,fmt="full"):
    r=httpx.get(f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}",headers=google_headers(token),params={"format":fmt},timeout=30)
    if r.status_code!=200:raise RuntimeError(f"Gmail message fetch failed: {r.status_code}")
    return r.json()


def gmail_thread_reply_sent(token,thread_id,reply_for):
    if not thread_id or not reply_for:return False
    r=httpx.get(f"https://gmail.googleapis.com/gmail/v1/users/me/threads/{thread_id}",headers=google_headers(token),params={"format":"metadata","metadataHeaders":["X-LHOS-Reply-For"]},timeout=30)
    if r.status_code!=200:raise RuntimeError(f"Gmail thread check failed: {r.status_code}")
    for msg in r.json().get("messages",[]):
        if reply_for in _header_list(msg.get("payload",{}),"x-lhos-reply-for"):return True
    return False

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
        # Remove known quoted/forwarded containers before flattening tags; quoted
        # approvals or holds are never authored authority.
        text=re.sub(r'<blockquote\b[^>]*>.*?</blockquote\s*>','',text,flags=re.I|re.S)
        text=re.sub(r'<(?:div|section)\b[^>]*(?:class=["\'][^"\']*(?:gmail_quote|yahoo_quoted)[^"\']*["\']|id=["\'](?:divRplyFwdMsg|appendonsend)["\'])[^>]*>.*$','',text,flags=re.I|re.S)
        text=re.sub(r'<hr\b[^>]*(?:id=["\']stopSpelling["\']|class=["\'][^"\']*msocomoff[^"\']*["\'])[^>]*>.*$','',text,flags=re.I|re.S)
        text=re.sub(r'<(?:br|/p|/div|/li|hr)[^>]*>','\n',text,flags=re.I);text=html.unescape(re.sub(r'<[^>]+>',' ',text))
    text=re.sub(r'[ \t]+',' ',text);return re.sub(r'\n\s*\n+','\n',text).strip()

def gmail_sent_evidence(token,subject,date_key,expected_recipients=None):
    day=datetime.strptime(date_key,"%Y-%m-%d").date();nxt=day+timedelta(days=1);expected={x.strip().lower() for x in (expected_recipients or [])}
    for item in gmail_search(token,f'in:sent subject:"{subject}" after:{day:%Y/%m/%d} before:{nxt:%Y/%m/%d}',20):
        msg=gmail_get(token,item['id'],"metadata");payload=msg.get("payload",{});h=headers_map(payload)
        actual={addr.strip().lower() for _,addr in getaddresses(_header_list(payload,"to"))}
        if dec_header(h.get("subject"))==subject and (not expected or actual==expected):
            return {"id":msg.get("id") or item.get("id"),"threadId":msg.get("threadId"),"rfc_message_id":h.get("message-id"),"recipients":sorted(actual)}
    return None

def gmail_subject_sent_any(token,subject,date_key):return bool(gmail_sent_evidence(token,subject,date_key))

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
    if r.status_code!=200:raise RuntimeError(f"Drive lookup failed with HTTP {r.status_code}")
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
        if s.startswith('>') or re.match(r'^On .+wrote:$',s) or re.match(r'^(?:From|Sent|Date|To|Cc|Subject):\s+.+',s,re.I) or re.match(r'^-{2,}\s*(?:Original Message|Forwarded message)\s*-{2,}$',s,re.I) or re.match(r'^Begin forwarded message:?$',s,re.I):break
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
        else:last=f"provider HTTP {r.status_code}"
    raise RuntimeError("Revision failed: "+last)


AUTH_REQUIRED=True  # Production invariant: authenticated approver mail cannot be disabled by configuration.
TRUSTED_AUTHSERV="mx.google.com"  # Gmail intake trust root; not configurable or optional.

def _header_list(payload,name):
    """Return ALL values for a header, in original order (top to bottom)."""
    n=name.lower();return [x.get("value","") for x in (payload.get("headers") or []) if x.get("name","").lower()==n]

def auth_verdicts(payload):
    """Parse Gmail's Authentication-Results.

    RFC 8601 hardening (this matters):
      * A sender composes their own message, so they CAN inject a forged
        Authentication-Results header. Google PREPENDS its own at the top, so we
        must take the TOPMOST instance only and ignore anything below it.
      * We additionally pin the authserv-id to our trusted border MTA
        (mx.google.com); a header claiming any other authserv-id is not evidence.
      * Never read auth results out of a forwarded/quoted body -- only top-level
        headers of the message Gmail itself received.
    """
    vals=_header_list(payload,"authentication-results")
    source="authentication-results"
    if not vals:
        return {"raw":"","dkim":None,"spf":None,"dmarc":None,"dkim_domain":None,"authserv":None,"source":None,"instances":0}
    flat=" ".join(vals[0].split())          # TOPMOST only
    authserv=flat.split(";",1)[0].strip().lower() if ";" in flat else flat.strip().lower()
    def grab(name):
        m=re.search(name+r"=(\w+)",flat);return m.group(1).lower() if m else None
    dom=re.search(r"header\.i=@([\w.\-]+)",flat) or re.search(r"header\.d=([\w.\-]+)",flat)
    return {"raw":flat[:400],"dkim":grab("dkim"),"spf":grab("spf"),"dmarc":grab("dmarc"),
            "dkim_domain":(dom.group(1).lower() if dom else None),"authserv":authserv,
            "source":source,"instances":len(vals)}

def is_auto_submitted(payload):
    """RFC 3834: auto-responders stamp Auto-Submitted with a keyword other than 'no'.
    Anything not explicitly 'no' is treated as machine-generated and cannot approve."""
    for v in _header_list(payload,"auto-submitted"):
        if v.strip().lower() not in ("","no"):return True
    for h in ("x-autoreply","x-autorespond","x-auto-response-suppress"):
        if _header_list(payload,h):return True
    for v in _header_list(payload,"precedence"):
        if v.strip().lower() in ("bulk","junk","auto_reply","list"):return True
    return False

def sender_authenticated(addr,payload):
    """Fail closed: an approver's message must be cryptographically attributable
    to the From domain, as judged by OUR border MTA."""
    v=auth_verdicts(payload)
    if not v.get("source"):return False,{**v,"basis":"no_auth_results"}
    if v.get("authserv")!=TRUSTED_AUTHSERV:
        return False,{**v,"basis":"untrusted_authserv"}
    from_domain=(addr.rsplit("@",1)[-1] or "").lower()
    aligned=bool(v["dkim_domain"] and from_domain and (v["dkim_domain"]==from_domain or from_domain.endswith("."+v["dkim_domain"])))
    if v["dmarc"]=="pass" and (aligned or not v["dkim_domain"]):
        return True,{**v,"basis":"dmarc_pass"}
    if v["dkim"]=="pass" and aligned:
        return True,{**v,"basis":"dkim_aligned"}
    return False,{**v,"basis":"unauthenticated"}

def configure_router(*,get_token,send_email,create_draft,load_drafts,save_drafts,send_draft,approve_draft,approvers,approval_senders,public_url,sender_email,sender_name,reply_email=None):
    router=APIRouter(prefix="/api/lhos/automation")
    reply_email=reply_email or send_email
    def state_all():
        if not STATE_FILE.exists():return {}
        try:data=json.loads(STATE_FILE.read_text())
        except Exception:raise HTTPException(status_code=503,detail={"category":"state_corrupt","message":"Automation state could not be read; writes and sends are blocked pending recovery."})
        if not isinstance(data,dict):raise HTTPException(status_code=503,detail={"category":"state_corrupt","message":"Automation state has an invalid shape; writes and sends are blocked pending recovery."})
        return data
    def heartbeat(name):
        h=load(HEARTBEAT_FILE,{});h[name]=now_et().isoformat();atomic_json_write(HEARTBEAT_FILE,h);return h
    def save_state(d):atomic_json_write(STATE_FILE,d)
    def current():
        now=now_et();return now.strftime("%Y-%m-%d"),now.strftime("%B %d, %Y")
    def make_review(date_display,approval_path,email_html,subtitle="Daily content validated"):
        url=approval_path if approval_path.startswith("http") else public_url + approval_path
        preview=email_html.replace("RECIPIENT_NAME_PLACEHOLDER","Hello Beta Tester!").replace("UNSUB_URL_PLACEHOLDER","#")
        return f'<html><body><div style="background:#0E1B33;color:white;padding:20px;text-align:center;font-family:Nunito,Arial,sans-serif"><h2>{html.escape(subtitle)}</h2><p>Review the validated email below. Approve, edit, or request changes.</p><a style="display:inline-block;background:#4BC0C4;color:white;padding:14px 30px;text-decoration:none;font-weight:700" href="{url}">Review, Edit, or Approve for 3 PM</a></div>{preview}</body></html>'
    def pending_editorial_message(token,date_key,exclude_message_id=None):
        inbox_state=load_inbox_state();standing=inbox_state.get("standing_hold") or {}
        if standing.get("active") and standing.get("message_id")!=exclude_message_id:
            return {"message_id":standing.get("message_id"),"intent":"standing_hold","transaction_status":"active_standing_hold"}
        for mid,entry in inbox_state.get("messages",{}).items():
            if mid!=exclude_message_id and entry.get("status") in ("processing","replied_action_pending"):
                return {"message_id":mid,"intent":entry.get("intent"),"transaction_status":entry.get("status")}
        allowed={a.strip().lower() for a in approval_senders};processed=set(load(PROCESSED_FILE,[]))
        query=f'in:inbox after:{date_key.replace("-","/")} '+'{'+(' '.join('from:'+a for a in sorted(allowed)))+'}'
        for item in gmail_search(token,query,100):
            if item.get("id")==exclude_message_id or item.get("id") in processed:continue
            msg=gmail_get(token,item["id"]);payload=msg.get("payload",{});h=headers_map(payload);addr=parseaddr(h.get("from",''))[1].strip().lower()
            authed,_=sender_authenticated(addr,payload)
            if addr not in allowed or not authed or is_auto_submitted(payload) or not directly_addressed(payload,sender_email):continue
            subj=dec_header(h.get("subject",''));authored_body=clean_reply(extract_gmail_body(payload))
            return {"message_id":item["id"],"from":addr,"intent":classify_direct_message(subj,authored_body,thread_bound=False),"subject":subj}
        return None

    def pending_inbox_supersession(token,date_key):
        pending=pending_editorial_message(token,date_key)
        return {"type":"pending_reference","pending":pending} if pending else None
    def generated_supersession(token,date_key,exclude_message_id=None):
        pending=pending_editorial_message(token,date_key,exclude_message_id)
        pending={"type":"pending_reference","pending":pending} if pending else None
        if pending:return pending
        file,raw,meta=drive_source(token,date_key);valid,_=validate_daily_content(raw)
        if valid:return {"type":"human_source","file":file,"raw":raw,"meta":meta}
        return None

    def revoke_recorded_approval(date_key,draft_id):
        drafts=load_drafts();draft=drafts.get(draft_id)
        if draft and draft.get("status")!="sent":draft.update({"status":"pending_approval","approved_by":None,"approved_at":None});save_drafts(drafts)
        st=state_all();state=st.get(date_key,{})
        if state.get("draft_id")==draft_id:state.update({"stage":"review_sent","approved_by":None,"approved_at":None,"updated_at":now_et().isoformat()});st[date_key]=state;save_state(st)

    def begin_delivery(date_key,draft_id,trigger):
        """Durably define delivery start before a final conservative source recheck.

        No recipient API is called until after that post-transition check passes.
        """
        st=state_all();state=st.get(date_key,{})
        if state.get("draft_id")!=draft_id:raise RuntimeError("Authorized draft changed before delivery start")
        if state.get("stage")=="sending" and state.get("delivery_started_at"):return state
        if state.get("stage")!="approved":raise RuntimeError("Draft is not approved for delivery start")
        stamp=now_et().isoformat();state.update({"stage":"sending","delivery_started_at":stamp,"source_authority_locked_at":stamp,"sent_trigger":trigger,"updated_at":stamp});st[date_key]=state;save_state(st);return state

    def finish_pending_review(date_key,state,token):
        drafts=load_drafts();draft=drafts.get(state.get("draft_id"))
        if not draft:raise RuntimeError("Pending review draft is missing")
        old_id=state.get("replaces_draft_id")
        if old_id:
            old=drafts.get(old_id)
            if old and old.get("status")!="sent":old["status"]="revised";old["revised_at"]=now_et().isoformat();save_drafts(drafts)
        review_subject=state["review_subject"];receipt=None;already_sent=gmail_subject_sent_any(token,review_subject,date_key)
        if not already_sent:
            receipt=send_email(token,','.join(approvers),review_subject,make_review(state["date_display"],state["approval_url"],draft["html_body"],state.get("review_subtitle","Daily content validated")),sender_email,sender_name)
        elif state.get("review_thread_binding_required"):
            receipt=gmail_sent_evidence(token,review_subject,date_key,approvers)
            if not receipt or not receipt.get("threadId"):raise RuntimeError("Sent review thread evidence is unavailable; review remains pending")
        st=state_all();current=st.get(date_key,state)
        if current.get("draft_id")!=state.get("draft_id"):raise RuntimeError("Pending review transaction changed during delivery")
        stamp=now_et().isoformat();review_evidence={"review_recipients":sorted({a.strip().lower() for a in approvers})}
        if isinstance(receipt,dict):
            if receipt.get("id"):review_evidence["review_gmail_id"]=receipt["id"]
            if receipt.get("threadId"):review_evidence["review_thread_id"]=receipt["threadId"]
        current.update({"stage":"review_sent","review_sent_at":current.get("review_sent_at") or stamp,"updated_at":stamp,**review_evidence});st[date_key]=current;save_state(st)
        return {"action":"review_sent","draft_id":current["draft_id"],"subject":current["subject"]}

    def prepare_from_raw(date_key,date_display,raw,source,token,dry_run=False,subtitle="Daily content validated",*,composed_sections=None,intro="",subject=None,review_subject=None,replaces_draft_id=None):
        ok,reasons=validate_daily_content(raw)
        if not ok:return {"action":"hold","valid":False,"reasons":reasons,"source":source}
        if composed_sections is None:
            sections=deterministic_sections(raw);email_html=build_beta_email(sections,date_display);section_names=list(sections)
        else:
            sections=composed_sections;email_html=build_varied_email(sections,date_display,intro);section_names=[x.get("title") for x in sections]
        subject=subject or f"LifeHouse OS Beta Update - {date_display}"
        review_subject=review_subject or f"[REVIEW] LifeHouse OS Beta Email Draft - {date_display}"
        if dry_run:return {"action":"would_send_review","valid":True,"sections":section_names,"subject":subject,"source":source}
        result=create_draft(subject,email_html,raw,date_display);did=result['draft_id'];review_subject=f"{review_subject} [draft:{did[:16]}]"
        stamp=now_et().isoformat();pending={"date":date_key,"date_display":date_display,"stage":"review_pending","content_valid":True,"draft_id":did,"subject":subject,"review_subject":review_subject,"review_subtitle":subtitle,"review_recipients":sorted({a.strip().lower() for a in approvers}),"review_thread_binding_required":True,"approval_url":result.get("approval_url",f"/lhos/approve/{did}"),"source":source,"raw_content":raw,"deadline":"15:00 America/New_York","updated_at":stamp}
        if replaces_draft_id:pending["replaces_draft_id"]=replaces_draft_id
        st=state_all();st[date_key]=pending;save_state(st)
        return finish_pending_review(date_key,pending,token)
    def prepare_impl(dry_run=False,force=False):
        date_key,date_display=current();now=now_et()
        if END_DATE and date_key>END_DATE:return {"action":"stopped","reason":"end_date","end_date":END_DATE}
        st=state_all();existing=st.get(date_key,{})
        inbox_data=load_inbox_state();standing=inbox_data.get("standing_hold")
        if standing and standing.get("active"):
            if dry_run:return {"action":"would_hold_for_standing_approver_instruction","instruction_from":standing.get("from")}
            held={**existing,"date":date_key,"date_display":date_display,"stage":"hold","content_valid":False,"standing_hold":True,"standing_hold_from":standing.get("from"),"standing_hold_message_id":standing.get("message_id"),"updated_at":now.isoformat()};st[date_key]=held;save_state(st)
            return {"action":"held_for_standing_approver_instruction","instruction_from":standing.get("from")}
        source_type=(existing.get("source") or {}).get("type")
        generated_pending=source_type=="iris_generated" and existing.get("stage")=="review_sent"
        if not force and existing.get("stage") in ("review_sent","approved","sending","partial","sent","sent_external") and not generated_pending:return {"action":"daily_complete" if existing.get("stage") in ("sent","sent_external") else "no_op","stage":existing.get("stage"),"draft_id":existing.get("draft_id")}
        standard_subject=f"LifeHouse OS Beta Update - {date_display}"
        if existing.get("stage")=="review_pending":
            if dry_run:return {"action":"would_finish_pending_review","draft_id":existing.get("draft_id"),"review_subject":existing.get("review_subject")}
            return finish_pending_review(date_key,existing,get_token())
        token=get_token()
        sent_subjects={standard_subject}
        if existing.get("subject"):sent_subjects.add(existing["subject"])
        if any(gmail_subject_sent_any(token,s,date_key) for s in sent_subjects):
            if not dry_run:
                st=state_all();st[date_key]={"date":date_key,"date_display":date_display,"stage":"sent_external","content_valid":True,"subject":existing.get("subject") or standard_subject,"updated_at":now.isoformat()};save_state(st)
            return {"action":"already_sent","subject":existing.get("subject") or standard_subject}
        targeted=targeted_human_content(inbox_data,date_key)
        if targeted:
            return prepare_from_raw(date_key,date_display,targeted["body"],{"type":"authorized_dated_direct_email","message_id":targeted.get("message_id"),"from":targeted.get("from")},token,dry_run,"Dated content received directly from an authenticated approver",review_subject=f"[REVIEW] LifeHouse OS Beta Email Draft - {date_display} (Dated Direct Content)")
        f,raw,meta=drive_source(token,date_key);ok,reasons=validate_daily_content(raw)
        if generated_pending:
            if not ok:return {"action":"no_op","stage":"review_sent","draft_id":existing.get("draft_id"),"source":"iris_generated"}
            return prepare_from_raw(date_key,date_display,raw,meta,token,dry_run,"Human source replaced Iris fallback",review_subject=f"[REVIEW] LifeHouse OS Beta Email Draft - {date_display} (Human Source Update)",replaces_draft_id=existing.get("draft_id"))
        st=state_all();existing=st.get(date_key,{})
        if not force and existing.get('stage') in ('review_sent','approved','sent','sending','partial','not_sent','sent_external'):return {"action":"no_op","stage":existing['stage'],"draft_id":existing.get('draft_id')}
        if ok:return prepare_from_raw(date_key,date_display,raw,meta,token,dry_run)
        durable_context,durable_context_ids=editorial_context(inbox_data,date_key,include_message_ids=True)
        reference=usable_reference(existing.get("reference_content", "")) or usable_reference(raw)
        due=now.replace(hour=7,minute=30,second=0,microsecond=0)
        if now<due:
            if dry_run:return {"action":"would_await_iris_fallback","valid":False,"fallback_due_at":due.isoformat(),"reasons":reasons,"source":meta}
            st=state_all();waiting={"date":date_key,"date_display":date_display,"stage":"hold","content_valid":False,"reasons":reasons,"source":meta,"reference_available":bool(reference),"fallback_due_at":due.isoformat(),"updated_at":now.isoformat()}
            for key in ("reference_content","reference_source","reference_received_at"):
                if existing.get(key):waiting[key]=existing[key]
            st[date_key]=waiting;save_state(st)
            return {"action":"awaiting_iris_fallback","fallback_due_at":due.isoformat(),"reference_available":bool(reference)}
        pending=pending_editorial_message(token,date_key)
        if pending:
            if not existing and not dry_run:
                st=state_all();st[date_key]={"date":date_key,"date_display":date_display,"stage":"hold","content_valid":False,"source":meta,"pending_reference_message_id":pending["message_id"],"pending_reference_from":pending["from"],"updated_at":now.isoformat()};save_state(st)
            return {"action":"awaiting_authenticated_reference_processing","message_id":pending["message_id"],"from":pending["from"]}
        if dry_run:return {"action":"would_generate_iris_fallback","valid":False,"reference_available":bool(reference),"source":meta}
        try:
            bundle=generate_fallback_bundle(date_key,reference)
        except Exception as exc:
            action_subject=f"[ACTION REQUIRED] Iris fallback generation failed - {date_display}"
            if not gmail_subject_sent_any(token,action_subject,date_key):
                body='<p>Hi Bobby,</p><p>Iris could not safely prepare today\'s autonomous fallback briefing.</p><p>No beta email was sent. The system will continue checking for valid dated content.</p><p>Warm regards,<br>Iris</p>'
                send_email(token,ALERT_EMAIL,action_subject,body,sender_email,sender_name)
            st=state_all();st[date_key]={"date":date_key,"date_display":date_display,"stage":"hold","content_valid":False,"reasons":reasons,"source":meta,"reference_available":bool(reference),"fallback_failed_at":now.isoformat(),"fallback_error":type(exc).__name__,"action_subject":action_subject,"updated_at":now.isoformat()};save_state(st)
            return {"action":"hold","reason":"iris_fallback_generation_failed","reference_available":bool(reference)}
        generated_source={"type":"iris_generated","topic_id":bundle.get("topic_id"),"generator":bundle.get("generator"),"creative_model":bundle.get("creative_model"),"creative_attempted":bool(bundle.get("creative_attempted")),"creative_attempt_count":bundle.get("creative_attempt_count"),"creative_fallback_reason":bundle.get("creative_fallback_reason"),"reference_used":bool(bundle.get("reference_used")),"dated_source":meta}
        if durable_context:
            instruction="Rewrite the complete briefing around the authenticated approver guidance below. Treat those statements as factual editorial authority. Bobby explicitly directed the reviewed daily workflow to continue beginning 2026-08-01, so pre-activation stop language is context about avoiding irrelevant generic emails, not an active operational hold. Resolve conflicts chronologically: later dated human guidance wins. Do not reuse a stale message date as today's date. Remove generic household material that is not relevant to the current beta reality. Do not invent any fact, date, status, link, or promise beyond the guidance. Preserve a warm Iris voice and include a thank-you.\n\n"+durable_context
            revised=revise_with_glm(bundle["raw"],instruction)
            generated_source={"type":"iris_generated","generation_mode":"authenticated_inbox_context","generator":"glm-context-revision-v1","creative_model":"glm-4.7-flash","context_message_ids":durable_context_ids,"dated_source":meta}
            return prepare_from_raw(date_key,date_display,revised,generated_source,token,False,"Authenticated approver guidance shaped this draft",subject=f"LifeHouse OS Beta Update - {date_display}",review_subject=f"[REVIEW] LifeHouse OS Beta Email Draft - {date_display} (Approver Context)")
        return prepare_from_raw(date_key,date_display,bundle["raw"],generated_source,token,False,"Iris-generated fallback — no complete dated content was available by 7:30 AM ET",composed_sections=bundle["sections"],intro=bundle["intro"],subject=bundle["subject"],review_subject=f"[REVIEW] LifeHouse OS Iris Fallback Draft - {date_display}")
    def apply_instruction(date_key,date_display,state,actor,text,token,channel):
        kind=classify_instruction(text);st=state_all();drafts=load_drafts();draft=drafts.get(state.get("draft_id"),{})
        if not draft:return {"action":"draft_missing","kind":kind}
        if kind=="approve":
            if (load_inbox_state().get("standing_hold") or {}).get("active"):
                return {"action":"approval_blocked_standing_hold","draft_id":state.get("draft_id")}
            approved_draft_id=state["draft_id"]
            current_message_id=channel.rsplit(":",1)[-1] if channel.startswith("authenticated_review_reply:") else None
            pending_update=pending_editorial_message(token,date_key,current_message_id)
            if pending_update:return {"action":"approval_deferred_pending_reference","message_id":pending_update["message_id"]}
            if (state.get("source") or {}).get("type")=="iris_generated":
                # Human content remains authoritative until the instant of approval.
                supersession=generated_supersession(token,date_key,current_message_id)
                if supersession and supersession["type"]=="pending_reference":return {"action":"approval_deferred_pending_reference","message_id":supersession["pending"]["message_id"]}
                if supersession and supersession["type"]=="human_source":
                    return prepare_from_raw(date_key,date_display,supersession["raw"],supersession["meta"],token,False,"Human source arrived before approval and replaced Iris fallback",review_subject=f"[REVIEW] LifeHouse OS Beta Email Draft - {date_display} (Human Source Update)",replaces_draft_id=approved_draft_id)
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
            generated=(fresh.get("source") or {}).get("type")=="iris_generated"
            pending_update=pending_editorial_message(token,date_key,current_message_id)
            if pending_update:
                revoke_recorded_approval(date_key,approved_draft_id);return {"action":"approval_revoked_pending_reference","message_id":pending_update["message_id"]}
            if generated:
                supersession=generated_supersession(token,date_key,current_message_id)
                if supersession:
                    revoke_recorded_approval(date_key,approved_draft_id)
                    if supersession["type"]=="human_source":return prepare_from_raw(date_key,date_display,supersession["raw"],supersession["meta"],token,False,"Human source arrived during approval and replaced Iris fallback",review_subject=f"[REVIEW] LifeHouse OS Beta Email Draft - {date_display} (Human Source Update)",replaces_draft_id=approved_draft_id)
                    return {"action":"approval_revoked_pending_reference","message_id":supersession["pending"]["message_id"]}
            begin_delivery(date_key,approved_draft_id,"on_approval")
            pending_update=pending_editorial_message(token,date_key,current_message_id)
            if pending_update:
                revoke_recorded_approval(date_key,approved_draft_id);return {"action":"approval_revoked_pending_reference","message_id":pending_update["message_id"]}
            if generated:
                supersession=generated_supersession(token,date_key,current_message_id)
                if supersession:
                    revoke_recorded_approval(date_key,approved_draft_id)
                    if supersession["type"]=="human_source":return prepare_from_raw(date_key,date_display,supersession["raw"],supersession["meta"],token,False,"Human source arrived at delivery start and replaced Iris fallback",review_subject=f"[REVIEW] LifeHouse OS Beta Email Draft - {date_display} (Human Source Update)",replaces_draft_id=approved_draft_id)
                    return {"action":"approval_revoked_pending_reference","message_id":supersession["pending"]["message_id"]}
            send_result=send_draft(approved_draft_id,f"{actor} via {channel}")
            st=state_all();state=st.get(date_key,state);state.update({"stage":send_result.get("status","partial"),"sent_trigger":"on_approval","updated_at":now_et().isoformat()});st[date_key]=state;save_state(st)
            return {"action":"approved_and_sent","send_policy":SEND_POLICY,"draft_id":approved_draft_id,"actor":actor,**send_result}
        if kind=="hold":
            draft.update({"status":"pending_approval","approved_by":None,"approved_at":None});save_drafts(drafts);state.update({"stage":"review_sent","approved_by":None,"approval_channel":None,"instruction_channel":channel,"updated_at":now_et().isoformat()});st[date_key]=state;save_state(st);return {"action":"send_held","draft_id":state["draft_id"],"actor":actor}
        if kind=="ambiguous":return {"action":"clarification_needed","draft_id":state["draft_id"],"actor":actor}
        revised=revise_with_glm(state.get("raw_content",draft.get("text_body","")),text);count=int(state.get("revision_count",0))+1
        return prepare_from_raw(date_key,date_display,revised,{"type":"authenticated_review_revision","actor":actor,"channel":channel,"revision_count":count},token,False,f"Revision {count} applied from {actor}",review_subject=f"[REVIEW] LifeHouse OS Beta Email Draft - {date_display} (Revision {count})",replaces_draft_id=state["draft_id"])
    @router.get("/connectors")
    async def connectors(req:Request):
        auth(req);token=get_token();checks={}
        for name,url,params in [
            ("gmail","https://gmail.googleapis.com/gmail/v1/users/me/profile",None),
            ("drive","https://www.googleapis.com/drive/v3/about",{"fields":"user(displayName)"}),
            ("contacts","https://people.googleapis.com/v1/contactGroups",{"pageSize":1,"groupFields":"name"})]:
            r=httpx.get(url,headers=google_headers(token),params=params,timeout=30);checks[name]=r.status_code
            if r.status_code!=200:raise HTTPException(status_code=503,detail={"connector":name,"status":r.status_code})
        return {"status":"ok","checks":checks}
    @router.get("/status")
    async def status(req:Request):
        auth(req);date_key,_=current();inbox=load_inbox_state();return {"date":date_key,"state":state_all().get(date_key),"heartbeat":load(HEARTBEAT_FILE,{}),"persistent_data":str(DATA_DIR),"end_date":END_DATE or None,"approver_inbox":{"active":date_key>=INBOX_AGENT_START_DATE,"start_date":INBOX_AGENT_START_DATE,"processed_count":len(inbox.get("messages",{})),"context_count":len(inbox.get("context",[])),"standing_hold":bool((inbox.get("standing_hold") or {}).get("active"))}}
    @router.post("/prepare")
    async def prepare(req:Request,dry_run:bool=False):
        auth(req)
        if not dry_run and not (7 <= now_et().hour < 15):return {"action":"outside_active_window","window":"07:00-15:00 America/New_York"}
        with (nullcontext() if dry_run else automation_lock()):
            if not dry_run:heartbeat("prepare")
            return prepare_impl(dry_run=dry_run)
    @router.post("/check-replies")
    async def check_replies(req:Request,dry_run:bool=False):
        auth(req)
        now=now_et()
        if not dry_run and not (7 <= now.hour < 15):return {"action":"outside_active_window","window":"07:00-15:00 America/New_York"}
        with (nullcontext() if dry_run else automation_lock()):
            if not dry_run:heartbeat("check_replies")
            date_key,date_display=current()
            if date_key<INBOX_AGENT_START_DATE:return {"action":"inbox_agent_not_active","starts":INBOX_AGENT_START_DATE}
            st=state_all();state=st.get(date_key)
            if not state:
                if dry_run:state={"date":date_key,"date_display":date_display,"stage":"no_state","content_valid":False}
                else:
                    expected=datetime.strptime(date_key,"%Y-%m-%d").strftime("%y%m%d")+".docx";state={"date":date_key,"date_display":date_display,"stage":"hold","content_valid":False,"source":{"name":expected,"missing":True},"updated_at":now.isoformat()};st[date_key]=state;save_state(st)
            token=get_token();processed=set(load(PROCESSED_FILE,[]));allowed={a.strip().lower() for a in approval_senders};inbox=load_inbox_state()
            query_day=(datetime.strptime(INBOX_CONTEXT_SINCE,"%Y-%m-%d").date()-timedelta(days=1)).strftime("%Y/%m/%d")
            auth_query=' '.join('from:'+a for a in sorted(allowed));queries=[f'in:anywhere after:{query_day} to:{sender_email} {{{auth_query}}}',f'in:inbox after:{date_key.replace("-","/")} {{subject:"LifeHouse OS" subject:"beta email" subject:LHOS}}']
            ids={}
            for q in queries:
                for item in gmail_search(token,q,100):ids[item['id']]=item
            records=[]
            for mid in ids:
                entry=inbox.get("messages",{}).get(mid,{})
                if entry.get("status") in ("replied","context_imported","ignored"):continue
                msg=gmail_get(token,mid);payload=msg.get('payload',{});h=headers_map(payload);addr=parseaddr(h.get('from',''))[1].strip().lower();subj=dec_header(h.get('subject',''));authored_body=clean_reply(extract_gmail_body(payload));body=clean_reply(authored_body+'\n'+gmail_docx_attachments(token,mid,payload));authed,verdict=sender_authenticated(addr,payload)
                is_auto=is_auto_submitted(payload) or 'out of office' in subj.lower() or subj.lower().startswith('automatic reply')
                records.append({"id":mid,"internal":int(msg.get("internalDate",0)),"thread_id":msg.get("threadId"),"rfc_message_id":h.get("message-id","").strip(),"references":h.get("references","").strip(),"addr":addr,"subject":subj,"body":body,"authored_body":authored_body,"payload":payload,"authenticated":authed,"auth":verdict,"is_auto":is_auto,"direct":directly_addressed(payload,sender_email),"received_date":message_received_date(msg)})
                records[-1]["target_date"]=message_target_date(subj,body,records[-1]["received_date"])
            records.sort(key=lambda x:(x["internal"],x["id"]));actions=[]
            for rec in records:
                mid=rec["id"];addr=rec["addr"];authorized=(addr in allowed) and bool(rec.get("authenticated")) and not rec.get("is_auto") and rec.get("direct")
                if not authorized:
                    why=("auto_reply" if rec.get("is_auto") else ("not_directly_addressed" if addr in allowed and not rec.get("direct") else ("failed_dmarc_authentication" if addr in allowed else "sender_not_allow_listed")))
                    if dry_run:actions.append({"action":"would_ignore_unauthorized","message_id":mid,"reason":why,"auth":rec.get("auth",{}).get("basis")});continue
                    current_state=state_all().get(date_key) or state;current_state["ignored_unauthorized_inbox_count"]=int(current_state.get("ignored_unauthorized_inbox_count",0))+1;current_state["last_ignored_unauthorized_at"]=now_et().isoformat();st=state_all();st[date_key]=current_state;save_state(st);processed.add(mid);inbox["messages"][mid]={"status":"ignored","reason":why,"at":now_et().isoformat()};atomic_json_write(INBOX_FILE,inbox);actions.append({"action":"ignored_unauthorized","message_id":mid,"reason":why,"auth":rec.get("auth",{}).get("basis")});continue
                current_state=state_all().get(date_key) or state;stage=current_state.get("stage");thread_bound=bool(current_state.get('review_subject') and current_state.get('review_subject').lower() in rec["subject"].lower() and ((current_state.get("review_thread_binding_required") and current_state.get("review_thread_id") and current_state.get("review_thread_id")==rec.get("thread_id")) or (not current_state.get("review_thread_binding_required") and (not current_state.get("review_thread_id") or current_state.get("review_thread_id")==rec.get("thread_id")))));intent=classify_direct_message(rec["subject"],rec["authored_body"],thread_bound=thread_bound)
                if intent in ("other","question","context") and validate_daily_content(rec["body"])[0]:intent="content"
                historical=bool(rec.get("received_date") and rec["received_date"]<INBOX_AGENT_START_DATE);prior_entry=inbox.get("messages",{}).get(mid,{})
                if dry_run:return {"action":"would_process_direct_approver_email","message_id":mid,"stage":stage,"from":addr,"classification":intent,"historical_context":historical,"would_reply":not historical or rec["received_date"]>=INBOX_CONTEXT_SINCE}
                record_context(inbox,rec,intent)
                if not prior_entry:
                    inbox["messages"][mid]={"status":"processing","received_date":rec.get("received_date"),"from":addr,"subject":rec["subject"][:300],"intent":intent,"started_at":now_et().isoformat()}
                atomic_json_write(INBOX_FILE,inbox)
                effect_state=state_all().get(date_key) or {}
                effect_source=effect_state.get("source") or {}
                effect_applied=(effect_source.get("message_id")==mid or str(effect_state.get("approval_channel") or "").endswith(mid) or str(effect_state.get("instruction_channel") or "").endswith(mid) or (inbox.get("standing_hold") or {}).get("message_id")==mid or inbox.get("last_resume_message_id")==mid)
                try:
                    result={"action":"context_recorded","intent":intent}
                    if prior_entry.get("status")=="action_applied":
                        result={"action":prior_entry.get("result_action") or "action_recovered","recovered":True}
                    elif prior_entry.get("status") in ("processing","replied_action_pending") and effect_applied:
                        if effect_state.get("stage")=="review_pending" and effect_source.get("message_id")==mid:result=finish_pending_review(date_key,effect_state,token)
                        else:result={"action":("review_sent" if effect_source.get("message_id")==mid else "action_recovered"),"recovered":True}
                    elif historical:
                        result={"action":"historical_context_imported","intent":intent}
                    elif intent=="approve":
                        if thread_bound and stage in ("review_sent","approved"):
                            result=apply_instruction(date_key,date_display,current_state,addr,rec["authored_body"],token,f"authenticated_review_reply:{mid}")
                        else:result={"action":"clarification_needed","reason":"approval_not_bound_to_current_review"}
                    elif intent=="approval_unbound":result={"action":"clarification_needed","reason":"approval_not_bound_to_current_review"}
                    elif intent in ("hold","standing_hold"):
                        if intent=="standing_hold":inbox["standing_hold"]={"active":True,"message_id":mid,"from":addr,"received_at":now_et().isoformat(),"instruction":rec["body"][:3000]};atomic_json_write(INBOX_FILE,inbox)
                        if current_state.get("draft_id") and stage in ("review_sent","approved"):result=apply_instruction(date_key,date_display,current_state,addr,rec["authored_body"],token,f"authenticated_review_reply:{mid}")
                        else:result={"action":"standing_hold_recorded" if intent=="standing_hold" else "hold_recorded"}
                    elif intent=="resume":
                        inbox["standing_hold"]=None;inbox["last_resume_message_id"]=mid;atomic_json_write(INBOX_FILE,inbox);result={"action":"standing_hold_cleared"}
                    elif intent=="content" and rec.get("target_date") and rec.get("target_date")!=date_key:
                        result={"action":"dated_content_recorded","target_date":rec.get("target_date")}
                    elif intent=="content" and stage not in ("sent","sent_external","sending","partial"):
                        result=prepare_from_raw(date_key,date_display,rec["body"],{"type":"authorized_direct_email","message_id":mid,"from":addr},token,False,f"Updated content received from {addr}",review_subject=f"[REVIEW] LifeHouse OS Beta Email Draft - {date_display} (Direct Approver Content)",replaces_draft_id=current_state.get("draft_id"))
                    elif intent=="context" and stage in ("hold","no_state") and usable_reference(rec["body"]):
                        due=now_et().replace(hour=7,minute=30,second=0,microsecond=0);current_state["reference_content"]=usable_reference(rec["body"]);current_state["reference_source"]={"type":"authorized_email_reference","message_id":mid,"from":addr};current_state["reference_received_at"]=now_et().isoformat();st=state_all();st[date_key]=current_state;save_state(st);result={"action":"reference_recorded","message_id":mid,"fallback_due_at":due.isoformat()}
                    elif intent in ("revision","context") and stage in ("review_sent","approved") and current_state.get("raw_content") and (thread_bound or rec.get("received_date")==date_key):
                        revised=revise_with_glm(current_state.get("raw_content",""),rec["body"]);count=int(current_state.get("revision_count",0))+1
                        result=prepare_from_raw(date_key,date_display,revised,{"type":"authenticated_review_revision","actor":addr,"channel":f"direct_email:{mid}","revision_count":count,"message_id":mid},token,False,f"Revision {count} applied from {addr}",review_subject=f"[REVIEW] LifeHouse OS Beta Email Draft - {date_display} (Revision {count})",replaces_draft_id=current_state.get("draft_id"))
                    elif intent in ("revision","context") and stage not in ("sent","sent_external","sending","partial") and now_et()>=now_et().replace(hour=7,minute=30,second=0,microsecond=0):
                        result=prepare_impl(force=True)
                except Exception:
                    result={"action":"processing_deferred","reason":"requested_action_temporarily_unavailable"}
                entry={"status":"action_applied","received_date":rec.get("received_date"),"from":addr,"subject":rec["subject"][:300],"intent":intent,"result_action":result.get("action"),"action_at":now_et().isoformat()};inbox["messages"][mid]=entry;atomic_json_write(INBOX_FILE,inbox)
                reply_needed=(not historical) or rec.get("received_date","")>=INBOX_CONTEXT_SINCE
                if reply_needed:
                    already=gmail_thread_reply_sent(token,rec.get("thread_id"),mid)
                    if not already:
                        reply_text=("I read your earlier message and recorded its beta-status and content constraints. Future drafts will stay aligned with the current beta reality, go to all authorized reviewers, and require approval before any beta delivery." if historical else direct_reply_copy(intent,result,current_state.get("review_subject","")))
                        reply_subject=rec["subject"] if rec["subject"].lower().startswith("re:") else "Re: "+rec["subject"]
                        reply_email(token,addr,reply_subject,direct_reply_html(reply_text),sender_email,sender_name,None,rec.get("thread_id"),rec.get("rfc_message_id"),rec.get("references"),mid)
                    entry.update({"status":("replied_action_pending" if result.get("action") in ("processing_deferred","approval_deferred_pending_reference") else "replied"),"reply_at":now_et().isoformat(),"reply_deduplicated":already})
                else:entry.update({"status":"context_imported","imported_at":now_et().isoformat()})
                inbox["messages"][mid]=entry;processed.add(mid);atomic_json_write(INBOX_FILE,inbox);atomic_json_write(PROCESSED_FILE,sorted(processed));actions.append({**result,"message_id":mid,"intent":intent,"reply_sent":reply_needed})
            if not dry_run:
                atomic_json_write(INBOX_FILE,inbox);atomic_json_write(PROCESSED_FILE,sorted(processed))
            if actions:return {"action":"inbox_processed","processed_count":len(actions),"results":actions}
            return {"action":"no_relevant_inbox","stage":state.get("stage"),"inbox_agent":True}
    @router.post("/decision")
    async def decision(req:Request,dry_run:bool=False):
        auth(req)
        raise HTTPException(status_code=410,detail="Machine-token decision intake is disabled; use an authenticated reply to the current bound review email")
    @router.post("/manual-send")
    async def manual_send(req:Request,dry_run:bool=False):
        auth(req)
        raise HTTPException(status_code=410,detail="Machine-token late-send override is disabled; create a new reviewed edition instead")
    def record_not_sent(date_key,date_display,state,reason):
        """Persist the failed-closed business outcome before touching Gmail.

        Connector failures must never erase the fact that no beta edition was sent.
        """
        st=state_all();base=dict(state or {"date":date_key,"date_display":date_display,"content_valid":False})
        if base.get("stage") in ("sent","sent_external","sending","partial"):
            return base
        stamp=now_et().isoformat();base.update({"stage":"not_sent","not_sent_reason":reason,"not_sent_at":base.get("not_sent_at") or stamp,"updated_at":stamp});st[date_key]=base;save_state(st);return base
    def notify_not_sent(date_key,date_display,state,reason,dry_run):
        subject=f"[NOT SENT] LifeHouse OS beta update - {date_display}"
        if dry_run:return {"action":"would_notify_not_sent","reason":reason,"stage":state.get("stage") if state else None}
        base=record_not_sent(date_key,date_display,state,reason);notification="already_sent"
        try:
            token=get_token()
            if not gmail_subject_sent_any(token,subject,date_key):
                body=f"<p>Hi Bobby,</p><p>Today's LifeHouse OS beta email was <strong>not sent</strong> at 3:00 PM Eastern.</p><p>{html.escape(reason)}</p><p>No beta tester received an email.</p><p>Warm regards,<br>Iris</p>";send_email(token,ALERT_EMAIL,subject,body,sender_email,sender_name);notification="sent"
        except Exception as exc:
            notification="failed";base["not_sent_notification_error"]=type(exc).__name__
        base["not_sent_notification"]=notification;base["updated_at"]=now_et().isoformat();st=state_all();st[date_key]=base;save_state(st)
        return {"action":"not_sent","reason":reason,"notification":notification}
    @router.post("/auto-send")
    async def auto_send(req:Request,dry_run:bool=False):
        auth(req)
        with (nullcontext() if dry_run else automation_lock()):
            if not dry_run:heartbeat("auto_send_started")
            if now_et().hour < 15:return {"action":"too_early","scheduled_for":"15:00 America/New_York"}
            if dry_run:
                date_key,date_display=current();state=state_all().get(date_key)
                if not state:return {"action":"would_notify_not_sent","reason":"No dated content or review state was available by the 3:00 PM deadline."}
                draft=load_drafts().get(state.get("draft_id"),{})
                if state.get("stage") in ("sending","partial"):return {"action":"would_reconcile","stage":state.get("stage")}
                if state.get("content_valid") and draft.get("status")=="approved":
                    try:pending_supersession=pending_inbox_supersession(get_token(),date_key)
                    except Exception:return {"action":"would_fail_closed","reason":"approver_inbox_recheck_failed"}
                    if pending_supersession:return {"action":"would_not_send_pending_approver_instruction","message_id":pending_supersession["pending"]["message_id"]}
                    return {"action":"would_send_approved","draft_id":state.get("draft_id"),"approved_by":draft.get("approved_by")}
                return {"action":"would_notify_not_sent","reason":"No authorized approved draft is available."}
            def complete(result):
                if not dry_run:heartbeat("auto_send")
                return result
            date_key,date_display=current();st=state_all();state=st.get(date_key)
            if not state:return complete(notify_not_sent(date_key,date_display,None,"No dated content or review state was available by the 3:00 PM deadline.",dry_run))
            if state.get("stage")=="not_sent":return complete({"action":"daily_complete","stage":"not_sent"})
            if state.get("stage") in ("sending","partial"):return complete({"action":"reconciliation_pending","stage":state.get("stage")})
            drafts=load_drafts();draft=drafts.get(state.get('draft_id'),{})
            if draft.get('status')=='sent':state['stage']='sent';state['updated_at']=now_et().isoformat();st[date_key]=state;save_state(st);return complete({"action":"already_sent"})
            if draft.get('status')=='approved':
                try:pending_supersession=pending_inbox_supersession(get_token(),date_key)
                except Exception:return complete(notify_not_sent(date_key,date_display,state,"The final approver-inbox recheck failed closed.",dry_run))
                if pending_supersession:return complete(notify_not_sent(date_key,date_display,state,"A newer authenticated approver instruction still requires processing and review.",dry_run))
            if draft.get('status')=='approved' and (state.get("source") or {}).get("type")=="iris_generated":
                try:supersession=generated_supersession(get_token(),date_key)
                except Exception:return complete(notify_not_sent(date_key,date_display,state,"Iris fallback was approved, but the final human-source recheck failed closed.",dry_run))
                if supersession:return complete(notify_not_sent(date_key,date_display,state,"Authenticated human content or reference arrived after Iris fallback approval and requires a new review.",dry_run))
            if draft.get('status')=='approved' and state.get('content_valid'):
                state['stage']='approved';st[date_key]=state;save_state(st)
            if state.get('stage')!='approved' or draft.get('status')!='approved' or not state.get('content_valid'):
                reason=("No authorized approver gave clear final approval." if state.get('content_valid') else "The dated source was missing or invalid.")+(f" Send policy in effect: {SEND_POLICY}." if SEND_POLICY=="ON_APPROVAL" else "")
                return complete(notify_not_sent(date_key,date_display,state,reason,dry_run))
            if dry_run:return {"action":"would_send_approved","draft_id":state.get('draft_id'),"approved_by":draft.get('approved_by')}
            generated=(state.get("source") or {}).get("type")=="iris_generated"
            try:pending_supersession=pending_inbox_supersession(get_token(),date_key)
            except Exception:return complete(notify_not_sent(date_key,date_display,state,"The final approver-inbox binding could not be verified and failed closed.",False))
            if pending_supersession:return complete(notify_not_sent(date_key,date_display,state,"A newer approver instruction requires a new review.",False))
            if generated:
                try:supersession=generated_supersession(get_token(),date_key)
                except Exception:return complete(notify_not_sent(date_key,date_display,state,"Iris fallback final source binding could not be verified and failed closed.",False))
                if supersession:return complete(notify_not_sent(date_key,date_display,state,"Human content changed during the final send transition and requires a new review.",False))
            begin_delivery(date_key,state['draft_id'],"three_pm_gate")
            try:pending_supersession=pending_inbox_supersession(get_token(),date_key)
            except Exception:return complete(notify_not_sent(date_key,date_display,state,"The post-transition approver-inbox binding could not be verified and failed closed.",False))
            if pending_supersession:return complete(notify_not_sent(date_key,date_display,state,"A newer approver instruction arrived at delivery start and requires a new review.",False))
            if generated:
                try:supersession=generated_supersession(get_token(),date_key)
                except Exception:return complete(notify_not_sent(date_key,date_display,state,"Iris fallback post-transition source binding could not be verified and failed closed.",False))
                if supersession:return complete(notify_not_sent(date_key,date_display,state,"Human content arrived at delivery start and requires a new review.",False))
            result=send_draft(state['draft_id'],draft.get('approved_by') or 'approved@n8n');st=state_all();state=st.get(date_key,state);state['stage']=result.get('status','partial');state['updated_at']=now_et().isoformat();st[date_key]=state;save_state(st);return complete(result)
    @router.post("/reconcile")
    async def reconcile(req:Request,dry_run:bool=False):
        auth(req)
        with (nullcontext() if dry_run else automation_lock()):
            if not dry_run:heartbeat("reconcile")
            date_key,date_display=current();st=state_all();state=st.get(date_key)
            if dry_run:
                if not state:return {"action":"would_notify_not_sent" if now_et().hour>=15 else "no_state"}
                draft=load_drafts().get(state.get("draft_id"),{})
                if draft.get("status")=="sent":return {"action":"already_sent"}
                if bool(draft.get("approved_by")) and draft.get("status") in ("sending","partial","approved"):
                    try:pending_supersession=pending_inbox_supersession(get_token(),date_key)
                    except Exception:return {"action":"would_fail_closed","reason":"approver_inbox_recheck_failed"}
                    if pending_supersession:return {"action":"would_not_reconcile_pending_approver_instruction","message_id":pending_supersession["pending"]["message_id"]}
                    return {"action":"would_reconcile","draft_id":state.get("draft_id"),"draft_status":draft.get("status")}
                return {"action":"no_op","stage":state.get("stage"),"draft_status":draft.get("status")}
            if not state:
                if now_et().hour>=15:return notify_not_sent(date_key,date_display,None,"No dated content or review state was available by the 3:00 PM deadline.",dry_run)
                return {"action":"no_state"}
            if state.get("stage")=="not_sent":return {"action":"daily_complete","stage":"not_sent"}
            drafts=load_drafts();draft=drafts.get(state.get("draft_id"),{})
            if draft.get("status")=="sent":
                state["stage"]="sent";state["updated_at"]=now_et().isoformat();st[date_key]=state;save_state(st);return {"action":"already_sent"}
            # Reconcile only a batch already authorized before the 3 PM gate.
            authorized=bool(draft.get("approved_by")) and not draft.get("authorization_revoked_at") and draft.get("status") in ("sending","partial","approved") and (draft.get("status") in ("sending","partial") or state.get("content_valid") is True)
            if not authorized:return {"action":"no_op","stage":state.get("stage"),"draft_status":draft.get("status")}
            if dry_run:return {"action":"would_reconcile","draft_id":state.get("draft_id"),"draft_status":draft.get("status")}
            generated=(state.get("source") or {}).get("type")=="iris_generated"
            in_flight=draft.get("status") in ("sending","partial")
            def pause_in_flight(action,message_id=None):
                stamp=now_et().isoformat();drafts_now=load_drafts();paused=drafts_now.get(state["draft_id"],draft);paused.update({"authorization_revoked_at":stamp,"delivery_paused_at":stamp,"delivery_pause_action":action,"delivery_pause_message_id":message_id});drafts_now[state["draft_id"]]=paused;save_drafts(drafts_now)
                latest=state_all().get(date_key,state);latest.update({"stage":"delivery_paused_pending_instruction","delivery_pause_action":action,"delivery_pause_message_id":message_id,"updated_at":stamp});all_state=state_all();all_state[date_key]=latest;save_state(all_state)
                return {"action":"delivery_paused_pending_approver_instruction","draft_id":state["draft_id"],"message_id":message_id,"draft_status":paused.get("status")}
            def stop_for_supersession(supersession):
                message_id=(supersession.get("pending") or {}).get("message_id")
                if in_flight:return pause_in_flight("supersession",message_id)
                revoke_recorded_approval(date_key,state["draft_id"]);fresh=state_all().get(date_key,state)
                if now_et().hour<15:
                    if supersession["type"]=="human_source":return prepare_from_raw(date_key,date_display,supersession["raw"],supersession["meta"],get_token(),False,"Human source arrived during delivery recovery and replaced Iris fallback",review_subject=f"[REVIEW] LifeHouse OS Beta Email Draft - {date_display} (Human Source Update)",replaces_draft_id=state["draft_id"])
                    return {"action":"approval_revoked_pending_reference","message_id":message_id}
                return notify_not_sent(date_key,date_display,fresh,"An authenticated approver instruction or human source arrived before recipient delivery; delivery was revoked after cutoff.",False)
            def fail_closed(reason):
                if in_flight:return pause_in_flight("source_verification_failed")
                revoke_recorded_approval(date_key,state["draft_id"])
                return notify_not_sent(date_key,date_display,state_all().get(date_key,state),reason,False) if now_et().hour>=15 else {"action":"approval_revoked_source_check_failed"}
            try:pending_supersession=pending_inbox_supersession(get_token(),date_key)
            except Exception:return fail_closed("Delivery recovery could not verify the approver inbox and failed closed.")
            if pending_supersession:return stop_for_supersession(pending_supersession)
            if generated:
                try:supersession=generated_supersession(get_token(),date_key)
                except Exception:return fail_closed("Generated delivery recovery could not verify source authority and failed closed.")
                if supersession:return stop_for_supersession(supersession)
            if draft.get("status")=="approved":
                if state.get("stage")!="sending":
                    state["stage"]="approved";state["approved_by"]=draft.get("approved_by");state["approved_at"]=draft.get("approved_at");st[date_key]=state;save_state(st)
                begin_delivery(date_key,state["draft_id"],"reconcile")
                try:pending_supersession=pending_inbox_supersession(get_token(),date_key)
                except Exception:return fail_closed("Delivery recovery post-transition inbox check failed closed.")
                if pending_supersession:return stop_for_supersession(pending_supersession)
                if generated:
                    try:supersession=generated_supersession(get_token(),date_key)
                    except Exception:return fail_closed("Generated delivery recovery post-transition check failed closed.")
                    if supersession:return stop_for_supersession(supersession)
            result=send_draft(state["draft_id"],draft.get("approved_by") or "reconcile@n8n");state=state_all().get(date_key,state);state["stage"]=result.get("status","partial");state["updated_at"]=now_et().isoformat();st=state_all();st[date_key]=state;save_state(st);return result
    @router.post("/close-out")
    async def close_out(req:Request,dry_run:bool=False):
        auth(req)
        with (nullcontext() if dry_run else automation_lock()):
            if not dry_run:heartbeat("close_out")
            date_key,date_display=current();state=state_all().get(date_key) or {};reports=load(REPORTS_FILE,{})
            if reports.get(date_key) and not dry_run:return {"action":"already_reported","date":date_key,"stage":reports[date_key].get("stage")}
            if not state and now_et().hour>=15:
                reason="No dated content or review state was available by the 3:00 PM deadline."
                state=({"date":date_key,"date_display":date_display,"content_valid":False,"stage":"not_sent","not_sent_reason":reason} if dry_run else record_not_sent(date_key,date_display,None,reason))
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
            terminal={"sent":"DELIVERED","sent_external":"DELIVERED","not_sent":"NOT SENT (failed closed)","hold":"HELD - content missing or invalid","approved":"APPROVED, DELIVERY INCOMPLETE","partial":"PARTIAL DELIVERY","review_sent":"AWAITING APPROVAL","review_pending":"REVIEW DELIVERY PENDING","no_state":"NO ACTIVITY RECORDED"}.get(stage,stage.upper())
            # A failed-closed day whose content WAS valid still needs human attention:
            # the edition did not reach beta testers even though usable content existed.
            incident=stage in ("approved","partial","sending","review_sent","review_pending","hold","no_state") or (stage=="not_sent" and bool(state.get("content_valid")))
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
    async def watchdog(req:Request,dry_run:bool=False,source:str="cloud"):
        auth(req)
        with (nullcontext() if dry_run else automation_lock()):
            source="core" if source.lower()=="core" else "cloud"
            if not dry_run:
                heartbeat(f"watchdog_{source}")
                if source=="cloud":heartbeat("watchdog")  # backward-compatible independent-cloud evidence
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
            elif state.get("stage") in ("review_sent","review_pending","hold"):reason=f"The 3:00 PM deadline handler did not finalize stage {state.get('stage')}."
            if not reason:return {"action":"healthy_or_expected_terminal_state","stage":state.get("stage") if state else None}
            key=hashlib.sha256((date_key+reason).encode()).hexdigest();alerts=load(ALERTS_FILE,{})
            if alerts.get(key):return {"action":"alert_already_sent","reason":reason}
            if dry_run:return {"action":"would_alert_bobby","reason":reason}
            token=get_token();subject=f"[LHOS AUTOMATION ALERT] {date_display}";body=f"<p><strong>LifeHouse OS cloud automation needs attention.</strong></p><p>{html.escape(reason)}</p><p>Date: {date_display}<br>Stage: {html.escape(str(state.get('stage') if state else 'no_state'))}</p><p>No beta email was sent by this watchdog.</p>"
            send_email(token,"bobbyatf@gmail.com",subject,body,sender_email,sender_name);alerts[key]={"sent_at":now.isoformat(),"reason":reason};atomic_json_write(ALERTS_FILE,alerts);return {"action":"alert_sent_to_bobby","reason":reason}
    return router
