#!/usr/bin/env python3
"""Idempotently add the LHOS core watchdog schedule to the live n8n workflow."""
import json,os,urllib.error,urllib.request

TRIGGER_NAME="Core Watchdog Every 10 Minutes"
REQUEST_NAME="Run Core Watchdog"
WATCHDOG_URL="https://lhos-beta-email-production.up.railway.app/api/lhos/automation/watchdog?source=core"

def patch_workflow(workflow):
    out=dict(workflow);nodes=[dict(n) for n in workflow.get("nodes",[])];connections=dict(workflow.get("connections") or {})
    reference=next((n for n in nodes if n.get("type")=="n8n-nodes-base.httpRequest" and (n.get("credentials") or {}).get("httpHeaderAuth")),None)
    if not reference:raise ValueError("No existing authenticated HTTP node is available for credential reuse")
    trigger={"parameters":{"rule":{"interval":[{"field":"cronExpression","expression":"*/10 * * * *"}]}},"type":"n8n-nodes-base.scheduleTrigger","typeVersion":1.2,"position":[-200,650],"id":"core-watchdog-every-10-minutes","name":TRIGGER_NAME}
    request={"parameters":{"method":"POST","url":WATCHDOG_URL,"authentication":"genericCredentialType","genericAuthType":"httpHeaderAuth","options":{"timeout":60000}},"type":"n8n-nodes-base.httpRequest","typeVersion":4.2,"position":[100,650],"id":"core-watchdog-http","name":REQUEST_NAME,"credentials":{"httpHeaderAuth":dict(reference["credentials"]["httpHeaderAuth"])}}
    replacements={TRIGGER_NAME:trigger,REQUEST_NAME:request};seen=set();patched=[]
    for node in nodes:
        name=node.get("name")
        if name in replacements:
            if name not in seen:patched.append(replacements[name]);seen.add(name)
        else:patched.append(node)
    for name,node in replacements.items():
        if name not in seen:patched.append(node)
    connections[TRIGGER_NAME]={"main":[[{"node":REQUEST_NAME,"type":"main","index":0}]]}
    out["nodes"]=patched;out["connections"]=connections;return out

def api(base,key,path,method="GET",body=None):
    req=urllib.request.Request(base.rstrip("/")+"/api/v1"+path,method=method,data=(json.dumps(body).encode() if body is not None else None),headers={"X-N8N-API-KEY":key,"accept":"application/json","content-type":"application/json"})
    try:
        with urllib.request.urlopen(req,timeout=45) as response:return response.status,json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raw=exc.read().decode(errors="replace")[:500]
        try:detail=json.loads(raw).get("message") or json.loads(raw).get("error") or raw
        except Exception:detail=raw
        raise RuntimeError(f"n8n API {method} {path} failed with HTTP {exc.code}: {str(detail)[:300]}") from None

WRITABLE_SETTINGS={"saveExecutionProgress","saveManualExecutions","saveDataErrorExecution","saveDataSuccessExecution","executionTimeout","errorWorkflow","timezone","executionOrder"}
def update_payload(workflow):
    payload={key:workflow[key] for key in ("name","nodes","connections") if key in workflow}
    payload["settings"]={key:value for key,value in (workflow.get("settings") or {}).items() if key in WRITABLE_SETTINGS}
    return payload

def main():
    base=os.environ["N8N_BASE_URL"];key=os.environ["N8N_API_KEY"];workflow_id=os.environ["LHOS_WORKFLOW_ID"]
    _,current=api(base,key,f"/workflows/{workflow_id}");was_active=bool(current.get("active"));patched=patch_workflow(current)
    api(base,key,f"/workflows/{workflow_id}","PUT",update_payload(patched))
    _,verified=api(base,key,f"/workflows/{workflow_id}")
    if was_active and not verified.get("active"):
        api(base,key,f"/workflows/{workflow_id}/activate","POST",{});_,verified=api(base,key,f"/workflows/{workflow_id}")
    by_name={n.get("name"):n for n in verified.get("nodes",[])}
    trigger=by_name.get(TRIGGER_NAME,{});request=by_name.get(REQUEST_NAME,{})
    cron=(((trigger.get("parameters") or {}).get("rule") or {}).get("interval") or [{}])[0].get("expression")
    ok=cron=="*/10 * * * *" and (request.get("parameters") or {}).get("url")==WATCHDOG_URL and (not was_active or verified.get("active"))
    if not ok:raise RuntimeError("n8n watchdog verification failed after update")
    print(json.dumps({"workflow_id":workflow_id,"active":bool(verified.get("active")),"core_watchdog_cron":cron,"verified":True}))

if __name__=="__main__":main()
