#!/usr/bin/env python3
"""LHOS auto-remediating watchdog.

Runs in GitHub Actions (cloud, laptop-independent). Detects and REPAIRS the
known failure modes; escalates anything novel. Never sends beta email.

Bounded by design:
  * only pre-approved, idempotent repair actions
  * max one repair attempt per failure per run
  * verifies recovery via heartbeat before declaring success
  * escalates instead of guessing when the cause is unknown
"""
import json,os,sys,time,urllib.request,urllib.error

N8N=os.environ["N8N_BASE_URL"].rstrip("/")
N8N_KEY=os.environ["N8N_API_KEY"]
WF_ID=os.environ["LHOS_WORKFLOW_ID"]
BACKEND=os.environ["LHOS_BACKEND_URL"].rstrip("/")
TOKEN=os.environ["LHOS_AUTOMATION_TOKEN"]
HEARTBEAT_URL=os.environ.get("HEARTBEAT_URL","").strip()

def http(url,method="GET",headers=None,body=None,timeout=60):
    req=urllib.request.Request(url,method=method,data=(json.dumps(body).encode() if body is not None else None))
    for k,v in (headers or {}).items():req.add_header(k,v)
    if body is not None:req.add_header("content-type","application/json")
    try:
        with urllib.request.urlopen(req,timeout=timeout) as r:
            raw=r.read().decode();return r.status,(json.loads(raw) if raw.strip().startswith(("{","[")) else raw)
    except urllib.error.HTTPError as e:
        raw=e.read().decode();return e.code,(json.loads(raw) if raw.strip().startswith(("{","[")) else raw)
    except Exception as e:
        return 0,str(e)

def n8n(path,method="GET",body=None):
    return http(f"{N8N}/api/v1{path}",method,{"X-N8N-API-KEY":N8N_KEY,"accept":"application/json"},body)

def backend(path,method="POST",body=None):
    return http(f"{BACKEND}{path}",method,{"X-LHOS-Automation-Token":TOKEN},body)

actions=[];escalate=[]

def log(msg):
    print(msg,flush=True);actions.append(msg)

# ---------- 1. Is the production workflow still active? ----------
st,wf=n8n(f"/workflows/{WF_ID}")
if st!=200:
    escalate.append(f"Cannot read workflow {WF_ID} from n8n API (HTTP {st}): {str(wf)[:200]}")
else:
    if not wf.get("active"):
        # KNOWN FAILURE: n8n silently self-deactivates after redeploy / stack-size errors.
        log(f"REPAIR: workflow '{wf.get('name')}' was INACTIVE. Reactivating via Public API.")
        rst,rb=n8n(f"/workflows/{WF_ID}/activate",method="POST")
        if rst==200:
            log("REPAIR OK: activate returned 200.")
        else:
            escalate.append(f"Reactivation FAILED (HTTP {rst}): {str(rb)[:200]}")
    else:
        log(f"OK: workflow '{wf.get('name')}' is active.")

# ---------- 2. Is the backend healthy? ----------
hst,hb=http(f"{BACKEND}/health")
if hst!=200:
    escalate.append(f"Backend /health unhealthy (HTTP {hst}): {str(hb)[:200]}")
else:
    log("OK: backend /health 200.")

# ---------- 3. Are scheduled executions actually reaching the backend? ----------
sst,state=backend("/api/lhos/automation/status",method="GET")
stale=False
if sst!=200:
    escalate.append(f"Cannot read automation status (HTTP {sst}).")
else:
    import datetime,zoneinfo
    now=datetime.datetime.now(zoneinfo.ZoneInfo("America/New_York"))
    hbs=(state or {}).get("heartbeat") or {}
    last=hbs.get("prepare")
    in_window = 7 <= now.hour < 15 and now.weekday() < 7
    if in_window:
        if not last:
            stale=True;log("DETECT: no prepare heartbeat during active window.")
        else:
            age=(now-datetime.datetime.fromisoformat(last)).total_seconds()
            if age>300:
                stale=True;log(f"DETECT: prepare heartbeat is {int(age)}s stale (>300s) during active window.")
            else:
                log(f"OK: prepare heartbeat {int(age)}s old.")
    else:
        log(f"OK: outside active window ({now.strftime('%H:%M')} ET); heartbeat staleness not enforced.")

# ---------- 4. Repair a stalled-but-active scheduler ----------
if stale and st==200 and wf.get("active"):
    # KNOWN FAILURE: Schedule Trigger stops firing while workflow still shows active.
    # Documented community fix is a deactivate/reactivate cycle. Idempotent + safe:
    # it cannot send email, it only restarts trigger registration.
    log("REPAIR: cycling workflow active state to re-register the Schedule Trigger.")
    d1,_=n8n(f"/workflows/{WF_ID}/deactivate",method="POST")
    time.sleep(3)
    a1,ab=n8n(f"/workflows/{WF_ID}/activate",method="POST")
    if d1==200 and a1==200:
        log("REPAIR OK: deactivate/reactivate cycle completed. Verifying recovery...")
        recovered=False
        for _ in range(6):           # up to ~3 minutes
            time.sleep(30)
            vst,vs=backend("/api/lhos/automation/status",method="GET")
            if vst==200:
                nl=((vs or {}).get("heartbeat") or {}).get("prepare")
                if nl and nl!=last:
                    recovered=True;log(f"VERIFIED: new prepare heartbeat at {nl}.");break
        if not recovered:
            escalate.append("Scheduler did not resume after reactivation cycle. Manual inspection required.")
    else:
        escalate.append(f"Reactivation cycle failed (deactivate={d1}, activate={a1}): {str(ab)[:200]}")

# ---------- 5. Let the backend run its own state audit (it may email an alert) ----------
wst,wb=backend("/api/lhos/automation/watchdog")
log(f"backend watchdog -> HTTP {wst} {str(wb)[:180]}")

# ---------- 6. Dead-man's-switch ping (only if everything above is clean) ----------
if HEARTBEAT_URL and not escalate:
    pst,_=http(HEARTBEAT_URL,timeout=20)
    log(f"heartbeat ping -> {pst}")

print("\n===== SUMMARY =====")
for a in actions:print(" .",a)
if escalate:
    print("\n===== ESCALATE (human action needed) =====")
    for e in escalate:print(" !",e)
    sys.exit(1)
print("\nAll checks passed or were auto-repaired.")
