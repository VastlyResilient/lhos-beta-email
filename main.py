#!/usr/bin/env python3
"""
LifeHouse OS Beta Daily Email — Approval & Send API
Deployed on Railway. Handles:
  1. Draft storage (POST /api/lhos/drafts)
  2. Approval page (GET /lhos/approve/{draft_id})
  3. Approve + send to all beta users (POST /api/lhos/approve/{draft_id})
  4. Draft listing/status (GET /api/lhos/drafts, GET /api/lhos/drafts/{id})
  5. Send logging
"""

import json
import os
import uuid
import base64
import html
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email import message_from_bytes
from email.header import decode_header, make_header
from pathlib import Path
from typing import Optional

import httpx
from delivery import atomic_json_write, deliver_once
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

APPROVERS = json.loads(os.getenv("LHOS_APPROVERS", '["kristina@lifehouseos.app","thomas@lifehouseos.app","bobby@lifehouseos.app"]'))
CONTACT_GROUP_NAME = os.getenv("LHOS_CONTACT_GROUP", "LifeHouse OS Beta - Active")
SENDER_EMAIL = os.getenv("LHOS_SENDER_EMAIL", "iris@lifehouseos.com")
SENDER_NAME = os.getenv("LHOS_SENDER_NAME", "LifeHouse OS")
FEEDBACK_LINK = os.getenv("LHOS_FEEDBACK_LINK", "https://lifehouseos.app/feedback")
UNSUBSCRIBE_BASE_URL = os.getenv("UNSUBSCRIBE_BASE_URL", "https://lhos-unsubscribe-production.up.railway.app")
SUPPRESSION_LIST_URL = "https://raw.githubusercontent.com/VastlyResilient/lhos-unsubscribe-data/main/suppression_list.json"


def get_suppression_list() -> list:
    """Fetch suppression data from the live unsubscribe service. Fail closed."""
    resp = httpx.get(f"{UNSUBSCRIBE_BASE_URL}/api/list", timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"Suppression service unavailable: {resp.status_code}")
    data = resp.json()
    if not isinstance(data.get("unsubscribed", []), list):
        raise RuntimeError("Suppression service returned invalid data")
    return data.get("unsubscribed", [])
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DRAFTS_FILE = DATA_DIR / "drafts.json"
LOG_FILE = DATA_DIR / "send_log.json"
LEDGER_DIR = DATA_DIR / "send_ledgers"
LEDGER_DIR.mkdir(parents=True, exist_ok=True)

# Google credentials (from OAuth token, set as Railway env vars)
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN", "")

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/contacts.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

# LifeHouse OS brand colors
BRAND_NAVY = "#0E1B33"
BRAND_AQUA = "#4BC0C4"
BRAND_SAND = "#E6B35B"

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def load_drafts() -> dict:
    if DRAFTS_FILE.exists():
        try:
            return json.loads(DRAFTS_FILE.read_text())
        except Exception:
            pass
    return {}

def save_drafts(drafts: dict):
    atomic_json_write(DRAFTS_FILE, drafts)

def load_log() -> list:
    if LOG_FILE.exists():
        try:
            return json.loads(LOG_FILE.read_text())
        except Exception:
            pass
    return []

def save_log(log: list):
    atomic_json_write(LOG_FILE, log)

# ---------------------------------------------------------------------------
# Google API helpers
# ---------------------------------------------------------------------------

def get_google_access_token() -> str:
    """Refresh and return a valid Google access token using the refresh token."""
    if not GOOGLE_REFRESH_TOKEN or not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=500, detail="Google credentials not configured.")
    
    resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "refresh_token": GOOGLE_REFRESH_TOKEN,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=500, detail=f"Google token refresh failed: {resp.text}")
    return resp.json()["access_token"]

def _decode_header(value: str) -> str:
    try: return str(make_header(decode_header(value or "")))
    except Exception: return value or ""

def gmail_exact_sent(access_token: str, to_email: str, subject: str, date_key: str) -> bool:
    """Exact Sent-Mail recipient+subject check. Any API failure blocks delivery."""
    day = datetime.strptime(date_key, "%Y-%m-%d").date()
    nxt = day + timedelta(days=1)
    q = f'in:sent to:{to_email} subject:"{subject}" after:{day:%Y/%m/%d} before:{nxt:%Y/%m/%d}'
    resp = httpx.get("https://gmail.googleapis.com/gmail/v1/users/me/messages", headers={"Authorization": f"Bearer {access_token}"}, params={"q": q, "maxResults": 20}, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"Gmail Sent precheck failed: {resp.status_code} {resp.text}")
    for item in resp.json().get("messages", []):
        meta = httpx.get(f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{item['id']}", headers={"Authorization": f"Bearer {access_token}"}, params={"format":"metadata","metadataHeaders":["To","Subject"]}, timeout=30)
        if meta.status_code != 200: raise RuntimeError(f"Gmail metadata check failed: {meta.status_code}")
        headers = {h.get("name","").lower(): h.get("value","") for h in meta.json().get("payload",{}).get("headers",[])}
        if _decode_header(headers.get("subject")) == subject and to_email.lower() in headers.get("to","").lower(): return True
    return False


def get_contact_group_id(access_token: str, group_name: str) -> Optional[str]:
    """Find the contact group resource name by display name."""
    resp = httpx.get(
        "https://people.googleapis.com/v1/contactGroups",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=500, detail=f"Failed to list contact groups: {resp.text}")
    
    groups = resp.json().get("contactGroups", [])
    for g in groups:
        if g.get("name", "").lower() == group_name.lower():
            return g["resourceName"]
    return None

def get_contacts_in_group(access_token: str, group_resource_name: str) -> list:
    """List all contacts that are members of a specific contact group."""
    resp = httpx.get(
        f"https://people.googleapis.com/v1/{group_resource_name}",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"maxMembers": 1000},
        timeout=30,
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=500, detail=f"Failed to get contact group: {resp.text}")
    
    member_resource_names = resp.json().get("memberResourceNames", [])
    
    contacts = []
    for resource_name in member_resource_names:
        person_resp = httpx.get(
            f"https://people.googleapis.com/v1/{resource_name}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"personFields": "names,emailAddresses"},
            timeout=30,
        )
        if person_resp.status_code == 200:
            person = person_resp.json()
            names = person.get("names", [{}])
            emails = person.get("emailAddresses", [])
            name = names[0].get("displayName", "") if names else ""
            email_list = [e["value"] for e in emails if "value" in e]
            if email_list:
                contacts.append({"name": name, "email": email_list[0]})
    
    return contacts

def send_gmail(access_token: str, to: str, subject: str, html_body: str, sender_email: str, sender_name: str):
    """Send an email via Gmail API."""
    message = MIMEText(html_body, "html")
    message["To"] = to
    message["Subject"] = subject
    message["From"] = f'"{sender_name}" <{sender_email}>'
    
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    
    resp = httpx.post(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"raw": raw},
        timeout=60,
    )
    
    if resp.status_code not in (200, 201):
        raise Exception(f"Gmail send failed for {to}: {resp.status_code} {resp.text}")
    
    return resp.json()

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="LifeHouse OS Beta Email", version="1.0.0")

class DraftCreate(BaseModel):
    subject: str
    html_body: str
    text_body: str = ""
    date: str = ""

@app.get("/")
async def root():
    return {"service": "LifeHouse OS Beta Email", "status": "running"}

def create_draft_record(subject: str, html_body: str, text_body: str, date_value: str):
    draft_id = uuid.uuid4().hex
    drafts = load_drafts()
    drafts[draft_id] = {"id":draft_id,"subject":subject,"html_body":html_body,"text_body":text_body,"date":date_value or datetime.now(timezone.utc).strftime("%Y-%m-%d"),"status":"pending_approval","created_at":datetime.now(timezone.utc).isoformat(),"approved_by":None,"approved_at":None,"sent_at":None,"recipient_count":0,"send_errors":[]}
    save_drafts(drafts)
    return {"draft_id":draft_id,"approval_url":f"/lhos/approve/{draft_id}","status":"pending_approval"}

@app.post("/api/lhos/drafts")
async def create_draft(draft: DraftCreate):
    """Register a new draft for approval."""
    return create_draft_record(draft.subject,draft.html_body,draft.text_body,draft.date)

@app.get("/api/lhos/drafts")
async def list_drafts():
    """List all drafts."""
    drafts = load_drafts()
    summary = []
    for d in drafts.values():
        summary.append({
            "id": d["id"],
            "subject": d["subject"],
            "date": d["date"],
            "status": d["status"],
            "approved_by": d.get("approved_by"),
            "sent_at": d.get("sent_at"),
            "recipient_count": d.get("recipient_count", 0),
        })
    return summary

@app.get("/api/lhos/drafts/{draft_id}")
async def get_draft(draft_id: str):
    """Get full draft details."""
    drafts = load_drafts()
    if draft_id not in drafts:
        raise HTTPException(status_code=404, detail="Draft not found")
    return drafts[draft_id]

@app.get("/lhos/approve/{draft_id}", response_class=HTMLResponse)
async def approval_page(draft_id: str):
    """Show the approval page with draft preview."""
    drafts = load_drafts()
    if draft_id not in drafts:
        return HTMLResponse(content="<h1>Draft not found</h1>", status_code=404)
    
    draft = drafts[draft_id]
    
    if draft["status"] == "sent":
        approver = draft.get("approved_by", "someone")
        count = draft.get("recipient_count", 0)
        return HTMLResponse(content=f"""
        <html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
          * {{ margin:0; padding:0; box-sizing:border-box; }}
          body {{ font-family:'Nunito','Segoe UI',Arial,sans-serif; background:#f8f9fa; display:flex; justify-content:center; align-items:center; min-height:100vh; }}
          .card {{ max-width:500px; width:90%; text-align:center; padding:48px 40px; background:#fff; border-radius:16px; box-shadow:0 4px 24px rgba(14,27,51,0.08); }}
          .icon {{ font-size:48px; margin-bottom:16px; }}
          h1 {{ color:{BRAND_NAVY}; font-size:24px; margin-bottom:8px; }}
          p {{ color:#6b7c8d; font-size:15px; line-height:1.6; }}
        </style></head><body>
        <div class="card">
          <div class="icon">✅</div>
          <h1>Email Already Sent</h1>
          <p>This draft was approved by {approver} and sent to {count} beta users.</p>
        </div>
        </body></html>
        """)
    
    if draft["status"] == "approved":
        return HTMLResponse(content=f"""
        <html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
          * {{ margin:0; padding:0; box-sizing:border-box; }}
          body {{ font-family:'Nunito','Segoe UI',Arial,sans-serif; background:#f8f9fa; display:flex; justify-content:center; align-items:center; min-height:100vh; }}
          .card {{ max-width:500px; width:90%; text-align:center; padding:48px 40px; background:#fff; border-radius:16px; box-shadow:0 4px 24px rgba(14,27,51,0.08); }}
          h1 {{ color:{BRAND_NAVY}; font-size:24px; margin-bottom:8px; }}
          p {{ color:#6b7c8d; font-size:15px; line-height:1.6; }}
        </style></head><body>
        <div class="card">
          <h1>Approved — Sending...</h1>
          <p>This draft has been approved and emails are being sent.</p>
        </div>
        </body></html>
        """)
    
    return HTMLResponse(content=f"""
    <html>
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>Approve Beta Email — LifeHouse OS</title>
      <link href="https://fonts.googleapis.com/css2?family=Nunito:wght@400;600;700;800&display=swap" rel="stylesheet">
      <style>
        * {{ margin:0; padding:0; box-sizing:border-box; }}
        body {{ font-family:'Nunito','Segoe UI',Arial,sans-serif; background:#f8f9fa; color:#2c3e50; }}
        .header {{ background:{BRAND_NAVY}; padding:20px 0; text-align:center; }}
        .header img {{ height:40px; }}
        .container {{ max-width:680px; margin:24px auto; padding:0 16px; }}
        .info-card {{ background:#fff; border-radius:12px; padding:24px; margin-bottom:20px; box-shadow:0 2px 8px rgba(0,0,0,0.06); }}
        .info-card h2 {{ color:{BRAND_NAVY}; font-size:18px; margin-bottom:12px; }}
        .info-row {{ display:flex; justify-content:space-between; padding:6px 0; border-bottom:1px solid #eee; font-size:14px; }}
        .info-row:last-child {{ border-bottom:none; }}
        .info-label {{ color:#6b7c8d; }}
        .info-value {{ font-weight:600; color:{BRAND_NAVY}; }}
        .preview {{ background:#fff; border-radius:12px; overflow:hidden; box-shadow:0 2px 8px rgba(0,0,0,0.06); margin-bottom:20px; }}
        .preview-header {{ background:{BRAND_NAVY}; color:#fff; padding:12px 20px; font-size:14px; font-weight:600; }}
        .preview-body {{ padding:20px; }}
        .actions {{ text-align:center; padding:24px; }}
        .btn {{ display:inline-block; padding:16px 48px; font-size:18px; font-weight:700; border:none; border-radius:8px; cursor:pointer; text-decoration:none; }}
        .btn-approve {{ background:{BRAND_AQUA}; color:#fff; }}
        .btn-approve:hover {{ background:#3aa8ac; }}
        .btn-cancel {{ background:#e74c3c; color:#fff; margin-left:12px; }}
        .warning {{ background:#fff3cd; border:1px solid #ffeaa7; border-radius:8px; padding:12px 16px; margin-bottom:16px; font-size:14px; color:#856404; }}
      </style>
    </head>
    <body>
      <div class="header">
        <img src="https://files.catbox.moe/1nlat9.png" alt="LifeHouse OS">
      </div>
      <div class="container">
        <div class="info-card">
          <h2>Draft Summary</h2>
          <div class="info-row"><span class="info-label">Date</span><span class="info-value">{draft["date"]}</span></div>
          <div class="info-row"><span class="info-label">Subject</span><span class="info-value">{draft["subject"]}</span></div>
          <div class="info-row"><span class="info-label">Status</span><span class="info-value">Pending Approval</span></div>
          <div class="info-row"><span class="info-label">Recipients</span><span class="info-value">Active Beta Users (Google Contacts)</span></div>
        </div>
        
        <div class="warning">
          ⚠️ Clicking "Approve & Send" will immediately send this email to ALL active beta users.
          This action cannot be undone.
        </div>
        
        <div class="preview">
          <div class="preview-header">📧 Email Preview</div>
          <div class="preview-body">
            {draft["html_body"]}
          </div>
        </div>
        
        <div class="actions">
          <a href="#" onclick="approveDraft('{draft_id}'); return false;" class="btn btn-approve">✓ Approve & Send</a>
          <a href="#" onclick="showEditor(); return false;" class="btn btn-cancel">✏️ Request Changes</a>
        </div>
        
        <!-- Edit Mode (hidden by default) -->
        <div id="editSection" style="display:none; margin-top:20px;">
          <div class="info-card" style="padding:20px;">
            <h2 style="color:{BRAND_NAVY}; font-size:18px; margin-bottom:12px;">Edit Email Content</h2>
            <p style="font-size:14px; color:#6b7c8d; margin-bottom:16px;">Edit the email subject and HTML content below. When you submit, the revised draft will be sent back to all approvers for review.</p>
            <label style="display:block; font-size:13px; font-weight:600; color:{BRAND_NAVY}; margin-bottom:4px;">Subject:</label>
            <input type="text" id="editSubject" value="{draft["subject"]}" style="width:100%; padding:10px 12px; border:1px solid #ddd; border-radius:6px; font-size:14px; font-family:Nunito,sans-serif; margin-bottom:16px; box-sizing:border-box;">
            <label style="display:block; font-size:13px; font-weight:600; color:{BRAND_NAVY}; margin-bottom:4px;">Email HTML:</label>
            <textarea id="editHtml" style="width:100%; min-height:500px; padding:12px; border:1px solid #ddd; border-radius:6px; font-size:12px; font-family:monospace; line-height:1.5; box-sizing:border-box; resize:vertical;">{html.escape(draft["html_body"])}</textarea>
            <div style="text-align:center; margin-top:16px;">
              <a href="#" onclick="submitChanges('{draft_id}'); return false;" class="btn btn-approve">Submit Revised Draft</a>
              <a href="#" onclick="cancelEdit(); return false;" class="btn btn-cancel" style="background:#95a5a6;">Cancel</a>
            </div>
          </div>
        </div>
      </div>
      
      <script>
        // Store original draft ID for reference
        var currentDraftId = '{draft_id}';
        
        function showEditor() {{
          document.getElementById('editSection').style.display = 'block';
          document.getElementById('editSection').scrollIntoView({{ behavior: 'smooth' }});
        }}
        
        function cancelEdit() {{
          document.getElementById('editSection').style.display = 'none';
        }}
        
        async function submitChanges(id) {{
          var subject = document.getElementById('editSubject').value;
          var html = document.getElementById('editHtml').value;
          if (!subject.trim() || !html.trim()) {{
            alert('Subject and email content cannot be empty.');
            return;
          }}
          if (!confirm('Submit this revised draft? It will be sent back to Kristina, Thomas, and Bobby for re-review.')) return;
          var btn = event.target;
          btn.textContent = 'Submitting...';
          btn.style.opacity = '0.6';
          try {{
            var resp = await fetch('/api/lhos/drafts/' + id + '/edit', {{
              method: 'POST',
              headers: {{'Content-Type': 'application/json'}},
              body: JSON.stringify({{ subject: subject, html_body: html }})
            }});
            var data = await resp.json();
            if (resp.ok) {{
              document.body.innerHTML = '<div style="font-family:Nunito,sans-serif;display:flex;justify-content:center;align-items:center;min-height:100vh;background:#f8f9fa;"><div style="text-align:center;padding:48px 40px;background:#fff;border-radius:16px;box-shadow:0 4px 24px rgba(14,27,51,0.08);max-width:500px;width:90%;"><div style="font-size:48px;margin-bottom:8px;">📝</div><h1 style="color:{BRAND_NAVY};font-size:24px;margin:0 0 8px 0;">Changes Submitted!</h1><p style="color:#6b7c8d;font-size:15px;margin:8px 0;">Your revised draft has been created and sent to all approvers for re-review.</p><p style="color:#a0aec0;font-size:12px;margin-top:16px;">New draft ID: ' + data.new_draft_id + '</p><a href="/lhos/approve/' + data.new_draft_id + '" style="display:inline-block;margin-top:20px;color:{BRAND_AQUA};text-decoration:none;font-size:14px;font-weight:600;">View Revised Draft</a></div></div>';
            }} else {{
              alert('Error: ' + (data.detail || 'Unknown error'));
              btn.textContent = 'Submit Revised Draft';
              btn.style.opacity = '1';
            }}
          }} catch(e) {{
            alert('Error: ' + e.message);
            btn.textContent = 'Submit Revised Draft';
            btn.style.opacity = '1';
          }}
        }}
        
        async function approveDraft(id) {{
          if (!confirm('Are you sure? This will send the email to ALL active beta users immediately.')) return;
          const btn = event.target;
          btn.textContent = 'Sending...';
          btn.style.opacity = '0.6';
          try {{
            const resp = await fetch('/api/lhos/approve/' + id, {{
              method: 'POST',
              headers: {{'Content-Type': 'application/json'}},
              body: JSON.stringify({{}})
            }});
            const data = await resp.json();
            if (resp.ok) {{
              var errNote = data.errors.length > 0 ? '<p style="color:#e74c3c;font-size:13px;margin-top:8px;">' + data.errors.length + ' error(s) occurred during sending.</p>' : '';
              var skipNote = data.skipped_unsubscribed > 0 ? '<p style="color:#E6B35B;font-size:13px;margin-top:4px;">' + data.skipped_unsubscribed + ' recipient(s) skipped (unsubscribed).</p>' : '';
              document.body.innerHTML = '<div style="font-family:Nunito,sans-serif;display:flex;justify-content:center;align-items:center;min-height:100vh;background:#f8f9fa;"><div style="text-align:center;padding:48px 40px;background:#fff;border-radius:16px;box-shadow:0 4px 24px rgba(14,27,51,0.08);max-width:500px;width:90%;"><div style="font-size:48px;margin-bottom:8px;">✅</div><h1 style="color:{BRAND_NAVY};font-size:24px;margin:0 0 8px 0;">Email Sent Successfully!</h1><p style="color:#6b7c8d;font-size:15px;margin:8px 0;">The beta email has been sent to <strong style="color:{BRAND_NAVY};">' + data.recipient_count + '</strong> out of <strong style="color:{BRAND_NAVY};">' + data.total_recipients + '</strong> active beta testers.</p>' + skipNote + errNote + '<p style="color:#a0aec0;font-size:12px;margin-top:24px;">This confirmation was generated on ' + new Date().toLocaleString() + '</p><a href="/" style="display:inline-block;margin-top:20px;color:{BRAND_AQUA};text-decoration:none;font-size:14px;font-weight:600;">Return to Home</a></div></div>';
            }} else {{
              alert('Error: ' + (data.detail || 'Unknown error'));
              btn.textContent = '✓ Approve & Send';
              btn.style.opacity = '1';
            }}
          }} catch(e) {{
            alert('Error: ' + e.message);
            btn.textContent = '✓ Approve & Send';
            btn.style.opacity = '1';
          }}
        }}
      </script>
    </body>
    </html>
    """)

class DraftEdit(BaseModel):
    subject: str
    html_body: str

@app.post("/api/lhos/drafts/{draft_id}/edit")
async def edit_draft(draft_id: str, edit: DraftEdit):
    """Save edited draft, mark old one as revised, create new pending draft, and email approvers."""
    drafts = load_drafts()
    if draft_id not in drafts:
        raise HTTPException(status_code=404, detail="Draft not found")
    
    old_draft = drafts[draft_id]
    if old_draft["status"] in ("sent", "approved"):
        raise HTTPException(status_code=400, detail="Cannot edit a draft that is already " + old_draft["status"])
    
    # HARD GUARD: Block edit if any draft for this date was already sent
    draft_date = old_draft.get("date", "")
    for other_id, other_draft in drafts.items():
        if other_id != draft_id and other_draft.get("date") == draft_date and other_draft.get("status") == "sent":
            raise HTTPException(
                status_code=409,
                detail=f"Emails for {draft_date} have already been sent. Cannot create revised draft."
            )
    
    # Mark old draft as revised
    old_draft["status"] = "revised"
    old_draft["revised_at"] = datetime.now(timezone.utc).isoformat()
    save_drafts(drafts)
    
    # Create new draft with the edited content
    new_draft_id = str(uuid.uuid4())[:8]
    drafts = load_drafts()
    drafts[new_draft_id] = {
        "id": new_draft_id,
        "subject": edit.subject,
        "html_body": edit.html_body,
        "text_body": old_draft.get("text_body", ""),
        "date": old_draft["date"],
        "status": "pending_approval",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "approved_by": None,
        "approved_at": None,
        "sent_at": None,
        "recipient_count": 0,
        "send_errors": [],
        "revised_from": draft_id,
    }
    save_drafts(drafts)
    
    # Send the revised draft to approvers via Gmail
    try:
        access_token = get_google_access_token()
        approval_url = f"/lhos/approve/{new_draft_id}"
        
        # Build approver email with "revised" banner
        approver_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background-color:#f8f9fa;font-family:'Nunito','Segoe UI',Arial,sans-serif;">
<div style="max-width:680px;margin:0 auto;padding:16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:{BRAND_NAVY};border-radius:10px;margin-bottom:16px;">
<tr><td style="padding:20px 24px;text-align:center;">
<p style="margin:0 0 8px 0;font-size:14px;color:{BRAND_SAND};font-weight:700;letter-spacing:1px;text-transform:uppercase;">Revised Draft - Pending Approval</p>
<p style="margin:0 0 14px 0;font-size:13px;color:#a0aec0;">Changes were made via the approval page. Review and approve to send to beta testers.</p>
<a href="https://lhos-beta-email-production.up.railway.app{approval_url}" style="display:inline-block;background-color:{BRAND_AQUA};color:#ffffff;font-size:16px;font-weight:700;text-decoration:none;padding:14px 40px;border-radius:8px;">Review Revised Draft</a>
</td></tr></table></div>
{edit.html_body.replace("UNSUB_URL_PLACEHOLDER", "https://lhos-unsubscribe-production.up.railway.app/?email=preview").replace("RECIPIENT_NAME_PLACEHOLDER", "Hello Beta Tester!")}
</body></html>"""
        
        approver_emails = ",".join(APPROVERS)
        send_gmail(
            access_token,
            to=approver_emails,
            subject=f"[REVIEW] {edit.subject} (Revised via Editor)",
            html_body=approver_html,
            sender_email=SENDER_EMAIL,
            sender_name=SENDER_NAME,
        )
    except Exception as e:
        # Draft was created but email failed - still return success for the draft
        pass
    
    return {"status": "revised", "old_draft_id": draft_id, "new_draft_id": new_draft_id}


def send_draft_safely(draft_id: str, approver: str):
    drafts = load_drafts()
    if draft_id not in drafts: raise HTTPException(status_code=404, detail="Draft not found")
    draft = drafts[draft_id]
    if draft.get("status") == "sent":
        return {"status":"sent","draft_id":draft_id,"recipient_count":draft.get("recipient_count",0),"newly_sent_count":0,"errors":[]}
    if draft.get("status") == "revised": raise HTTPException(status_code=409, detail="Draft was superseded")
    draft_date = draft.get("date", "")
    for oid, other in drafts.items():
        if oid != draft_id and other.get("date") == draft_date and other.get("status") == "sent":
            raise HTTPException(status_code=409, detail=f"Emails for {draft_date} already sent via draft {oid}")
    access_token = get_google_access_token()
    group_id = get_contact_group_id(access_token, CONTACT_GROUP_NAME)
    if not group_id: raise HTTPException(status_code=500, detail=f"Contact group '{CONTACT_GROUP_NAME}' not found")
    contacts = get_contacts_in_group(access_token, group_id)
    if not contacts: raise HTTPException(status_code=500, detail="No contacts found in beta group")
    try: suppressed = get_suppression_list()
    except Exception as exc: raise HTTPException(status_code=503, detail=str(exc))
    try: date_key = datetime.strptime(draft_date, "%B %d, %Y").strftime("%Y-%m-%d")
    except Exception: raise HTTPException(status_code=400, detail="Draft date is invalid")
    draft.update({"status":"sending","approved_by":approver,"approved_at":draft.get("approved_at") or datetime.now(timezone.utc).isoformat()}); save_drafts(drafts)
    ledger_file = LEDGER_DIR / f"{date_key}.json"
    def precheck(addr, subject): return gmail_exact_sent(access_token, addr, subject, date_key)
    def send_one(addr, subject, body): return send_gmail(access_token, addr, subject, body, SENDER_EMAIL, SENDER_NAME)
    result = deliver_once(date_key=date_key, subject=draft["subject"], html_body=draft["html_body"], contacts=contacts, suppressed=suppressed, ledger_file=ledger_file, already_sent=precheck, send_one=send_one, unsubscribe_base=UNSUBSCRIBE_BASE_URL)
    drafts = load_drafts(); draft = drafts[draft_id]
    draft.update({"status":"sent" if result["complete"] else "partial","sent_at":datetime.now(timezone.utc).isoformat() if result["complete"] else None,"recipient_count":result["delivered_count"],"newly_sent_count":result["newly_sent_count"],"send_errors":result["errors"],"ledger_file":str(ledger_file)}); save_drafts(drafts)
    log = load_log(); log.append({"draft_id":draft_id,"date":draft_date,"subject":draft["subject"],"approved_by":approver,"approved_at":draft.get("approved_at"),"completed_at":datetime.now(timezone.utc).isoformat(),**result}); save_log(log)
    return {"status":draft["status"],"draft_id":draft_id,"recipient_count":result["delivered_count"],"total_recipients":len(contacts),"skipped_unsubscribed":result["suppressed_count"],"newly_sent_count":result["newly_sent_count"],"errors":result["errors"]}

@app.post("/api/lhos/approve/{draft_id}")
async def approve_and_send(draft_id: str, request: Request):
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    return send_draft_safely(draft_id, body.get("approver", "unknown"))

# n8n cloud orchestration router (authenticated by X-LHOS-Automation-Token)
from cloud_automation import configure_router
_public_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "lhos-beta-email-production.up.railway.app")
PUBLIC_URL = os.getenv("LHOS_PUBLIC_URL", _public_domain if _public_domain.startswith("http") else "https://" + _public_domain)
app.include_router(configure_router(
    get_token=get_google_access_token,
    send_email=send_gmail,
    create_draft=create_draft_record,
    load_drafts=load_drafts,
    save_drafts=save_drafts,
    send_draft=send_draft_safely,
    approvers=APPROVERS,
    public_url=PUBLIC_URL,
    sender_email=SENDER_EMAIL,
    sender_name=SENDER_NAME,
))

@app.get("/api/lhos/log")
async def get_send_log():
    """Get the send log."""
    return load_log()

@app.get("/health")
async def health():
    return {"status": "ok", "google_configured": bool(GOOGLE_CLIENT_ID and GOOGLE_REFRESH_TOKEN)}
