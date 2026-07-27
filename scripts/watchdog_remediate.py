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
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from watchdog_status import google_workspace_outage,instatus_outage,github_statuspage_outage,partition_watchdog_alerts

N8N=os.environ["N8N_BASE_URL"].rstrip("/")
N8N_KEY=os.environ["N8N_API_KEY"]
WF_ID=os.environ["LHOS_WORKFLOW_ID"]
BACKEND=os.environ["LHOS_BACKEND_URL"].rstrip("/")
TOKEN=os.environ["LHOS_AUTOMATION_TOKEN"]
HEARTBEAT_URL=os.environ.get("HEARTBEAT_URL","").strip()          # coarse liveness ping
HEARTBEAT_FAIL_URL=os.environ.get("HEARTBEAT_FAIL_URL","").strip() # explicit /fail signal
DAILY_OUTCOME_URL=os.environ.get("DAILY_OUTCOME_URL","").strip()   # once/day business outcome

RAILWAY_TOKEN=os.environ.get("RAILWAY_TOKEN","").strip()
# Railway topology (resolved 2026-07-25). Services restart via deploymentRestart
# on the latest deployment. These IDs are stable across redeploys.
RW_GQL="https://backboard.railway.app/graphql/v2"
RW_SERVICES={
    "backend":{"project":"03624831-628a-4ade-8544-13e86c4389bb","service":"b6668301-a78a-45a7-8acf-3273a34b034c","env":"d1831663-ea6f-4e7f-bd8f-09bc0b09084c"},
    "n8n":{"project":"7e034a23-6b1a-43d7-859c-648522d4419d","service":"94e0fd8e-0abe-47e5-9115-e176d181cbf6","env":"6cfb7ae1-9402-49c8-b127-a3bc7c2be823"},
}

def rw_gql(query,variables=None):
    if not RAILWAY_TOKEN:return None,"no RAILWAY_TOKEN"
    # Railway/Cloudflare rejects urllib's default Python-urllib User-Agent with 403/1010.
    return http(RW_GQL,method="POST",headers={"Authorization":f"Bearer {RAILWAY_TOKEN}",
                "User-Agent":"lhos-cloud-watchdog/1.0"},
                body={"query":query,"variables":variables or {}},timeout=30)

def rw_restart(which):
    """Restart a Railway service by restarting its latest deployment. Idempotent-ish:
    Railway serializes restarts; calling on an already-restarting deployment is a no-op."""
    svc=RW_SERVICES.get(which)
    if not svc:return False,f"unknown service {which}"
    q="query($in:DeploymentListInput!){ deployments(first:1, input:$in){ edges { node { id status } } } }"
    st,res=rw_gql(q,{"in":{"serviceId":svc["service"],"environmentId":svc["env"],"projectId":svc["project"]}})
    edges=(((res or {}).get("data") or {}).get("deployments") or {}).get("edges") or []
    if st!=200 or not edges:return False,f"cannot find deployment for {which} (gql {st})"
    dep=edges[0]["node"]["id"]
    st2,res2=rw_gql("mutation($id:String!){ deploymentRestart(id:$id) }",{"id":dep})
    ok=st2==200 and not (res2 or {}).get("errors")
    return ok,(f"deploymentRestart({which}/{dep[:8]}) -> gql {st2} "+("" if ok else str(res2)[:150]))

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


# ---------- 0. Predictive canaries (catch "unfixable" failures BEFORE they bite) ----------
GOOGLE_CLIENT_ID=os.environ.get("GOOGLE_CLIENT_ID","").strip()
GOOGLE_CLIENT_SECRET=os.environ.get("GOOGLE_CLIENT_SECRET","").strip()
GOOGLE_REFRESH_TOKEN=os.environ.get("GOOGLE_REFRESH_TOKEN","").strip()
GH_BILLING_TOKEN=os.environ.get("GH_BILLING_TOKEN","").strip()

def provider_outage(symptom):
    """Return only schema-verified, confirmed provider outages.

    Unknown/HTML/changed responses fail open so a real LHOS alert is never suppressed.
    """
    domain={"n8n":"railway","backend":"railway","railway":"railway","gmail":"google","drive":"google","google":"google"}[symptom]
    if domain=="google":
        st,payload=http("https://www.google.com/appsstatus/dashboard/incidents.json",timeout=15)
        return google_workspace_outage(payload) if st==200 else None
    if domain=="railway":
        st,payload=http("https://railway.instatus.com/summary.json",timeout=15)
        return instatus_outage(payload) if st==200 else None
    st,payload=http("https://www.githubstatus.com/api/v2/status.json",timeout=15)
    return github_statuspage_outage(payload) if st==200 else None

def oauth_canary():
    """The refresh token is the one credential that dies SILENTLY (revocation,
    Testing-mode 7-day expiry, 6-month inactivity). Prove it is alive every run,
    so a dead token pages Bobby BEFORE the pipeline needs it - with a fix-it runbook."""
    if not (GOOGLE_CLIENT_ID and GOOGLE_REFRESH_TOKEN):return
    import urllib.parse
    data=urllib.parse.urlencode({"client_id":GOOGLE_CLIENT_ID,"client_secret":GOOGLE_CLIENT_SECRET,
        "refresh_token":GOOGLE_REFRESH_TOKEN,"grant_type":"refresh_token"}).encode()
    req=urllib.request.Request("https://oauth2.googleapis.com/token",data=data)
    try:
        with urllib.request.urlopen(req,timeout=20) as r:
            body=json.loads(r.read().decode())
            if body.get("access_token"):
                log("OK: Google OAuth refresh token is live (canary).");return
    except urllib.error.HTTPError as e:
        raw=e.read().decode()
        if "invalid_grant" in raw:
            escalate.append("GOOGLE OAUTH TOKEN DEAD (invalid_grant). The pipeline will fail at 7 AM. "
                "Fix: re-consent Iris Gmail (one click) - see runbook 'google-oauth-revoked' in skill lhos-self-healing-ops. "
                "Zilla will auto-attempt browser re-consent when the Mac is awake.")
        else:
            log(f"OAuth canary inconclusive (HTTP {e.code}): {raw[:120]}")
    except Exception as e:
        log(f"OAuth canary inconclusive (transient): {e}")

def weekly_canaries_due():
    """Exactly once per week (Sunday 15:00 UTC), plus manual dispatches for testing.
    Do NOT use `weekday()==6` alone: that would page every 10 minutes all Sunday."""
    import datetime
    now=datetime.datetime.now(datetime.timezone.utc)
    return os.environ.get("GITHUB_EVENT_NAME")=="workflow_dispatch" or (now.weekday()==6 and now.hour==15 and now.minute<10)

def billing_canary():
    """GitHub Actions free tier = 2,000 min/mo on private repos; the watchdog is the
    main consumer. The billing API needs scopes we may not have, so ESTIMATE from run
    history instead (no special scope): count runs in the last 24h, sample their
    durations, project the month. Weekly; warn above 80%."""
    import datetime
    if not weekly_canaries_due():return
    tok=os.environ.get("GITHUB_TOKEN","").strip()  # auto-provided by Actions
    repo=os.environ.get("GITHUB_REPOSITORY","")
    if not tok or not repo:return
    hdr={"Authorization":f"Bearer {tok}","Accept":"application/vnd.github+json"}
    since=(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    st,r=http(f"https://api.github.com/repos/{repo}/actions/runs?per_page=100&created=%3E{since}",headers=hdr,timeout=20)
    runs=(r.get("workflow_runs") if isinstance(r,dict) else None) or []
    if st!=200 or not runs:log(f"Billing canary skipped (runs API {st}).");return
    billable_ms=0;sampled=0
    for run in runs[:5]:
        st2,t=http(f"https://api.github.com/repos/{repo}/actions/runs/{run['id']}/timing",headers=hdr,timeout=20)
        if st2==200 and isinstance(t,dict):
            billable_ms+=sum(float(v.get("total_ms",0) or 0) for v in (t.get("billable") or {}).values())
            sampled+=1
    if not sampled:log("Billing canary skipped (no timing samples).");return
    if billable_ms==0:
        log("OK: GitHub Actions reports 0 billable compute (public repo/standard runner); no Actions-minutes billing risk.")
        return
    avg_min=(billable_ms/sampled)/60000
    monthly=avg_min*len(runs)*30
    log(f"OK: Actions billable burn ~{avg_min:.1f} min/run x {len(runs)} runs/day = ~{monthly:.0f} min/mo projected.")
    if monthly>1600:
        escalate.append(f"GitHub Actions projected at ~{monthly:.0f} billable min/mo (>80% of a 2,000-minute allowance). Watchdog may stop before month end - trim schedule or add payment method.")



def railway_cost_canary():
    """Forecast the WHOLE Railway workspace, not just LHOS. Railway exposes projected
    resource quantities but not the final dollar total through the public API, so apply
    the live Hobby rates shown on the Usage dashboard. Warn once/week above $10 projected
    resource usage (=$5 over the included $5 credit)."""
    if not weekly_canaries_due() or not RAILWAY_TOKEN:return
    workspace="5f927a47-babf-4e8d-8497-3b6b0e0b5f22"
    st,res=rw_gql("query($w:String!){ projects(workspaceId:$w){ edges { node { id name } } } }",{"w":workspace})
    projects=((((res or {}).get("data") or {}).get("projects") or {}).get("edges") or [])
    if st!=200 or not projects:
        log(f"Railway cost canary inconclusive (projects API {st}).");return
    rates={"CPU_USAGE":0.000463,"MEMORY_USAGE_GB":0.000231,
           "NETWORK_TX_GB":0.05,"DISK_USAGE_GB":0.000003}
    q="query($p:String!){ estimatedUsage(projectId:$p, measurements:[CPU_USAGE,MEMORY_USAGE_GB,NETWORK_TX_GB,DISK_USAGE_GB]){ measurement estimatedValue } }"
    costs=[]
    for edge in projects:
        node=edge["node"]
        st2,r=rw_gql(q,{"p":node["id"]})
        rows=(((r or {}).get("data") or {}).get("estimatedUsage") or [])
        if st2!=200:continue
        cost=sum(float(x.get("estimatedValue") or 0)*rates.get(x.get("measurement"),0) for x in rows)
        if cost>0:costs.append((cost,node["name"]))
    if not costs:
        log("Railway cost canary inconclusive (no projected-usage rows).");return
    total=sum(c for c,_ in costs);over=max(0,total-5.0)
    leaders=", ".join(f"{n} ${c:.2f}" for c,n in sorted(costs,reverse=True)[:4])
    log(f"OK: Railway workspace projected ${total:.2f} resource usage (${over:.2f} above included $5). Top: {leaders}.")
    warn=float(os.environ.get("RAILWAY_MONTHLY_WARN_DOLLARS","10.00"))
    if total>=warn:
        escalate.append(f"Railway workspace projected at ${total:.2f} this billing period (guardrail ${warn:.2f}; ${over:.2f} above included $5). Top projects: {leaders}. Review Railway Usage; Zilla should optimize or stop the nonessential cost driver before overage grows.")


oauth_canary()
billing_canary()
railway_cost_canary()

# ---------- 1. Is the production workflow still active? ----------
st,wf=n8n(f"/workflows/{WF_ID}")
if st!=200:
    # KNOWN FAILURE: n8n container wedged (scheduler dead, UI unresponsive). Restart it.
    log(f"DETECT: n8n API unreachable (HTTP {st}). Attempting Railway restart of n8n service.")
    ok,msg=rw_restart("n8n");log(f"REPAIR: {msg}")
    if ok:
        time.sleep(90)
        st,wf=n8n(f"/workflows/{WF_ID}")
    if st!=200:
        escalate.append(f"n8n unreachable (HTTP {st}) AND Railway restart did not recover it: {str(wf)[:200]}")
if st==200:
    if not wf.get("active"):
        # KNOWN FAILURE: n8n silently self-deactivates after redeploy / stack-size errors.
        log(f"REPAIR: workflow '{wf.get('name')}' was INACTIVE. Reactivating via Public API.")
        rst,rb=n8n(f"/workflows/{WF_ID}/activate",method="POST")
        if rst==200:
            log("REPAIR OK: activate returned 200.")
        else:
            log(f"DETECT: reactivation failed (HTTP {rst}). Attempting Railway restart of n8n.")
            ok,msg=rw_restart("n8n");log(f"REPAIR: {msg}")
            if ok:
                time.sleep(90)
                rst,rb=n8n(f"/workflows/{WF_ID}/activate",method="POST")
                st2,wf2=n8n(f"/workflows/{WF_ID}")
            if rst==200 and st2==200 and wf2.get("active"):
                log("REPAIR OK: workflow active after n8n restart + reactivate.")
            else:
                escalate.append(f"Workflow inactive and BOTH reactivate (HTTP {rst}) and n8n restart failed to restore it.")
    else:
        log(f"OK: workflow '{wf.get('name')}' is active.")

# ---------- 2. Is the backend healthy? ----------
hst,hb=http(f"{BACKEND}/health")
if hst!=200:
    log(f"DETECT: backend /health unhealthy (HTTP {hst}). Attempting Railway restart.")
    ok,msg=rw_restart("backend");log(f"REPAIR: {msg}")
    if ok:
        time.sleep(90)
        hst,hb=http(f"{BACKEND}/health")
    if hst==200:
        log("REPAIR OK: backend healthy after Railway restart.")
    else:
        escalate.append(f"Backend /health unhealthy (HTTP {hst}) AND Railway restart did not recover it: {str(hb)[:200]}")
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
            if age>720:
                stale=True;log(f"DETECT: prepare heartbeat is {int(age)}s stale (>720s) during active window.")
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
wst,wb=backend("/api/lhos/automation/watchdog?source=cloud")
log(f"backend watchdog -> HTTP {wst} {str(wb)[:180]}")

# ---------- 6. Dead-man's-switch signalling ----------
def ping(url,label):
    """Monitoring over the public internet is unreliable; retry before believing a miss."""
    for attempt in range(5):
        st,_=http(url,timeout=15)
        if 200<=st<300:
            log(f"{label} ping -> {st}");return True
        time.sleep(2*(attempt+1))
    log(f"{label} ping FAILED after retries (last={st})");return False

predictive_alerts,operational_alerts=partition_watchdog_alerts(escalate)
if operational_alerts:
    # Suppress the page when the operational symptom is a CONFIRMED provider outage:
    # nothing to fix, retry+resume is automatic, paging Bobby is pure noise.
    outage=None
    for e in operational_alerts:
        sym="google" if "oauth" in e.lower() or "gmail" in e.lower() else ("n8n" if ("n8n" in e.lower() or "workflow" in e.lower()) else "backend")
        outage=provider_outage(sym)
        if outage:break
    if outage:
        log(f"OUTAGE SUPPRESSION: {outage}. Not paging; level-triggered loop will auto-resume when provider recovers.")
    elif HEARTBEAT_FAIL_URL:
        ping(HEARTBEAT_FAIL_URL,"heartbeat /fail")
elif predictive_alerts:
    log("PREDICTIVE WARNING: recorded without paging; no operational recovery ladder failed.")
    if HEARTBEAT_URL:ping(HEARTBEAT_URL,"coarse liveness")
elif HEARTBEAT_URL:
    ping(HEARTBEAT_URL,"coarse liveness")

# Business-outcome heartbeat: only ping when the DAY actually reached a verified
# terminal state. A green liveness ping must never imply the edition shipped.
if DAILY_OUTCOME_URL and sst==200:
    stage=((state or {}).get("state") or {}).get("stage")
    if stage in ("sent","sent_external"):
        ping(DAILY_OUTCOME_URL,"daily outcome (delivered)")
    else:
        log(f"daily outcome NOT pinged (stage={stage}) - absence is the signal")

print("\n===== SUMMARY =====")
for a in actions:print(" .",a)
if escalate:
    print("\n===== ESCALATE (human action needed) =====")
    for e in escalate:print(" !",e)
    sys.exit(1)
print("\nAll checks passed or were auto-repaired.")
