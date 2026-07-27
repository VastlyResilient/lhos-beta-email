#!/usr/bin/env python3
"""Idempotently add the LHOS core watchdog schedule to the live n8n workflow."""
import json,os,urllib.error,urllib.request

TRIGGER_NAME="Core Watchdog Every 10 Minutes"
REQUEST_NAME="Run Core Watchdog"
BACKEND_AUTOMATION_PREFIX="https://lhos-beta-email-production.up.railway.app/api/lhos/automation/"
WATCHDOG_URL=BACKEND_AUTOMATION_PREFIX+"watchdog?source=core"

def credential_id(credential):return str((credential or {}).get("id") or "")
def select_trusted_credential(workflow,expected_id=None):
    candidates={}
    for node in workflow.get("nodes",[]):
        url=str((node.get("parameters") or {}).get("url") or "")
        credential=(node.get("credentials") or {}).get("httpHeaderAuth")
        cid=credential_id(credential)
        if url.startswith(BACKEND_AUTOMATION_PREFIX) and cid:candidates[cid]=dict(credential)
    if expected_id:
        if expected_id not in candidates:raise ValueError("Expected LHOS header credential is not attached to a trusted backend node")
        return candidates[expected_id]
    if len(candidates)!=1:raise ValueError("Expected exactly one LHOS header credential across trusted backend nodes")
    return next(iter(candidates.values()))

def patch_workflow(workflow,credential=None):
    out=dict(workflow);nodes=[dict(n) for n in workflow.get("nodes",[])];connections=dict(workflow.get("connections") or {})
    credential=dict(credential or select_trusted_credential(workflow))
    trigger={"parameters":{"rule":{"interval":[{"field":"cronExpression","expression":"*/10 * * * *"}]}},"type":"n8n-nodes-base.scheduleTrigger","typeVersion":1.2,"position":[-200,650],"id":"core-watchdog-every-10-minutes","name":TRIGGER_NAME}
    request={"parameters":{"method":"POST","url":WATCHDOG_URL,"authentication":"genericCredentialType","genericAuthType":"httpHeaderAuth","options":{"timeout":60000}},"type":"n8n-nodes-base.httpRequest","typeVersion":4.2,"position":[100,650],"id":"core-watchdog-http","name":REQUEST_NAME,"credentials":{"httpHeaderAuth":credential}}
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

def verify_workflow(workflow,expected_credential_id,was_active):
    by_name={n.get("name"):n for n in workflow.get("nodes",[])};trigger=by_name.get(TRIGGER_NAME,{});request=by_name.get(REQUEST_NAME,{})
    cron=(((trigger.get("parameters") or {}).get("rule") or {}).get("interval") or [{}])[0].get("expression")
    actual_credential=credential_id((request.get("credentials") or {}).get("httpHeaderAuth"))
    ok=cron=="*/10 * * * *" and (request.get("parameters") or {}).get("url")==WATCHDOG_URL and actual_credential==expected_credential_id and (not was_active or workflow.get("active"))
    if not ok:raise RuntimeError("n8n watchdog verification failed after update")
    return cron

def sync_workflow(base,key,workflow_id,expected_credential_id=None):
    _,current=api(base,key,f"/workflows/{workflow_id}");was_active=bool(current.get("active"))
    credential=select_trusted_credential(current,expected_credential_id);expected_id=credential_id(credential);patched=patch_workflow(current,credential)
    update_attempted=False
    try:
        update_attempted=True;api(base,key,f"/workflows/{workflow_id}","PUT",update_payload(patched))
        if was_active:api(base,key,f"/workflows/{workflow_id}/activate","POST",{})
        _,verified=api(base,key,f"/workflows/{workflow_id}");cron=verify_workflow(verified,expected_id,was_active)
    except Exception as primary:
        if was_active and update_attempted:
            try:api(base,key,f"/workflows/{workflow_id}/activate","POST",{})
            except Exception as rollback:raise RuntimeError("n8n update failed and workflow reactivation also failed") from primary
        raise
    return {"workflow_id":workflow_id,"active":bool(verified.get("active")),"core_watchdog_cron":cron,"credential_id_verified":True,"verified":True}

def main():
    result=sync_workflow(os.environ["N8N_BASE_URL"],os.environ["N8N_API_KEY"],os.environ["LHOS_WORKFLOW_ID"],os.environ.get("LHOS_HEADER_AUTH_CREDENTIAL_ID") or None)
    print(json.dumps(result))

if __name__=="__main__":main()
