# Truthful, read-only IRIS operations dashboard model and self-contained UI.
from __future__ import annotations
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET=ZoneInfo("America/New_York")
SEVERITY={"green":0,"orange":1,"red":2}


def _parse(value):
    if not value:return None
    try:
        dt=datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=ET)
    except (TypeError,ValueError):return None


def _age_minutes(value,now):
    dt=_parse(value)
    if not dt:return None
    return max(0,(now-dt.astimezone(ET)).total_seconds()/60)


def _age_label(value,now):
    age=_age_minutes(value,now)
    if age is None:return "not recorded"
    if age<1:return "less than a minute ago"
    if age<60:return f"{int(age)} minute{'s' if int(age)!=1 else ''} ago"
    hours=int(age//60);return f"{hours} hour{'s' if hours!=1 else ''} ago"


def _same_day(value,now):
    dt=_parse(value)
    return bool(dt and dt.astimezone(ET).date()==now.date())


def _item(light,label,detail,checked_at=None):
    return {"light":light,"label":label,"detail":detail,"checked_at":checked_at}


def _overall(systems,now):
    # Overnight, the daily pipeline is intentionally idle. Keep genuine core
    # failures red, but do not promote a delayed hosted watchdog schedule into
    # a false pipeline incident.
    if now.hour<7:
        core=(systems["api"],systems["google"],systems["scheduler"])
        core_light=max((x["light"] for x in core),key=lambda x:SEVERITY[x])
        if core_light=="green":
            start=now.replace(hour=7,minute=0,second=0,microsecond=0)
            item=_item("green","IRIS is idle · overnight monitoring","Core services remain available while the daily workflow rests.")
            item.update({"mode":"idle","next_start":start.isoformat()})
            return item
    light=max((x["light"] for x in systems.values()),key=lambda x:SEVERITY[x])
    if light=="green":return _item("green","IRIS is healthy","Core services are responding and no manual action is needed.")
    if light=="orange":return _item("orange","IRIS is watching","Nothing is confirmed broken. One signal needs observation.")
    return _item("red","IRIS needs attention","A verified health signal is broken or stale. Self-healing is already attempting recovery.")


def _scheduler(now,heartbeat,state):
    prepare=heartbeat.get("prepare");replies=heartbeat.get("check_replies")
    if now.hour<7:
        return _item("green","Scheduler resting","Scheduled activity is paused overnight.",prepare)
    if now.hour<15:
        ages=[x for x in (_age_minutes(prepare,now),_age_minutes(replies,now)) if x is not None]
        if len(ages)<2:
            light="orange" if now.hour==7 and now.minute<10 else "red"
            return _item(light,"Polling evidence incomplete","One or more minute-by-minute heartbeats have not been recorded.",prepare or replies)
        age=max(ages)
        if age<=4:return _item("green","Polling every minute",f"Drive and authenticated replies were checked {_age_label(min((prepare,replies)),now)}.",min((prepare,replies)))
        if age<=10:return _item("orange","Polling is delayed",f"The oldest active heartbeat is {int(age)} minutes old. The watchdog is observing it.",prepare)
        return _item("red","Polling heartbeat is stale",f"No complete polling cycle has been recorded for {int(age)} minutes during the active window.",prepare)
    gate=heartbeat.get("auto_send")
    if _same_day(gate,now):return _item("green","Daily gate completed",f"The 3:00 PM ET gate ran {_age_label(gate,now)}.",gate)
    if now.hour==15 and now.minute<10:return _item("orange","Daily gate is due","The 3:00 PM ET gate is within its normal execution grace period.",gate)
    return _item("red","Daily gate is missing","No 3:00 PM ET gate heartbeat is recorded for today.",gate)


def _watchdog(now,heartbeat):
    stamp=heartbeat.get("watchdog");age=_age_minutes(stamp,now)
    if age is None:return _item("orange","Awaiting cloud check evidence","The dashboard has not yet recorded a cloud-watchdog heartbeat. No failure is being inferred.")
    if age<=30:return _item("green","Cloud watchdog online",f"The independent watchdog checked in {_age_label(stamp,now)}.",stamp)
    if now.hour<7 and age>90:
        item=_item("orange","Overnight cloud check delayed",f"IRIS is idle. The last independent cloud check was {int(age)} minutes ago; core services are verified separately.",stamp)
        item["mode"]="overnight"
        return item
    if age<=90:return _item("orange","Cloud check is delayed",f"The last cloud check was {int(age)} minutes ago. Hosted schedules can drift; no outage is assumed.",stamp)
    return _item("red","Cloud watchdog is stale",f"No cloud-watchdog heartbeat has arrived for {int(age)} minutes.",stamp)


def _edition(now,state):
    stage=state.get("stage") or "no_state";valid=bool(state.get("content_valid"));source=(state.get("source") or {}).get("name") or now.strftime("%y%m%d")+".docx"
    labels={
        "hold":("orange","Waiting for content",f"{source} is missing or not yet valid. IRIS keeps checking every minute until 2:59 PM ET."),
        "review_sent":("orange","Review in progress","Valid content was prepared and the current draft is waiting for an authenticated decision."),
        "approved":("orange","Approved; delivery pending","The current draft is authorized and the delivery path is active."),
        "sending":("orange","Delivery in progress","IRIS is sending and reconciling the authorized recipient batch."),
        "partial":("red","Delivery incomplete","An authorized batch has unresolved recipients. Automated reconciliation is active."),
        "sent":("green","Delivered and verified","Today’s edition reached a verified terminal state."),
        "sent_external":("green","Delivery already verified","IRIS found today’s edition in Gmail Sent and prevented a duplicate send."),
    }
    if stage=="not_sent":
        light="red" if valid else "orange";label="Edition missed after valid content" if valid else "Safely not sent"
        detail=state.get("not_sent_reason") or ("Valid content did not reach a verified delivery state." if valid else "No valid dated content was available by the cutoff; IRIS failed closed.")
    elif stage=="no_state":
        if now.hour<7:light,label,detail="green","Day has not started","No edition action is expected before the polling window."
        else:light,label,detail="orange","Waiting for today’s first state","No edition state has been recorded yet. System health is evaluated separately."
    else:light,label,detail=labels.get(stage,("orange",stage.replace('_',' ').title(),"IRIS recorded this state without inventing an interpretation."))
    steps=[];order=[("content","Content"),("review","Review"),("approval","Approval"),("delivery","Delivery")];done=set();current=None
    if stage=="hold":current="content"
    elif stage=="review_sent":done={"content"};current="review"
    elif stage=="approved":done={"content","review","approval"};current="delivery"
    elif stage in ("sending","partial"):done={"content","review","approval"};current="delivery"
    elif stage in ("sent","sent_external"):done={x[0] for x in order}
    elif stage=="not_sent":
        if valid:done={"content","review"}
        current="delivery"
    for key,name in order:steps.append({"key":key,"label":name,"state":"done" if key in done else ("current" if key==current else "pending")})
    return {"light":light,"label":label,"detail":detail,"stage":stage,"source":source,"updated_at":state.get("updated_at"),"steps":steps,"cutoff":now.replace(hour=15,minute=0,second=0,microsecond=0).isoformat(),"window":"7:00 AM–3:00 PM ET"}


def build_snapshot(*,now,state,heartbeat,connectors,reports,alerts):
    now=now.astimezone(ET);google_light=connectors.get("google","red")
    if google_light not in SEVERITY:google_light="red"
    systems={
        "api":_item("green","Pipeline API online","The live status service is responding.",now.isoformat()),
        "google":_item(google_light,"Google connection verified" if google_light=="green" else "Google connection failed",connectors.get("detail") or "No connector evidence is available.",connectors.get("checked_at")),
        "scheduler":_scheduler(now,heartbeat,state),
        "watchdog":_watchdog(now,heartbeat),
    }
    edition=_edition(now,state);awareness=[]
    if google_light=="green":
        awareness.append(_item("green","Google token refresh is automatic","A live refresh succeeded. Do not add a replacement token unless this check turns red and automated recovery cannot restore it."))
        awareness.append(_item("green","Token timing is evidence-based","Google does not expose a reliable refresh-token expiration date here. IRIS reports real refresh success instead of inventing a countdown."))
    else:awareness.append(_item("red","Google authorization needs attention","The live refresh or connector check failed. Self-healing is already retrying; manual re-consent is needed only if recovery remains red."))
    if edition["stage"]=="hold":awareness.append(_item("orange","Today’s source is still pending",f"IRIS is looking for {edition['source']} every minute until 2:59 PM ET. No system repair is needed."))
    wd=systems["watchdog"]
    if wd["light"]=="green":awareness.append(_item("green","Self-healing loop is armed","The independent cloud watchdog is checking health. It repairs known n8n and Railway failures before escalating."))
    elif wd.get("mode")=="overnight":awareness.append(_item("orange","Overnight monitoring continues","A hosted cloud check is delayed; the API and Google connection remain independently verified."))
    elif wd["light"]=="orange":awareness.append(_item("orange","Observe the next cloud check","No repair is requested yet. The dashboard will turn red only after the watchdog is genuinely stale."))
    else:awareness.append(_item("red","Cloud self-healing evidence is stale","The independent watchdog has not checked in within its safe window."))
    latest_report=None
    if reports:
        key=max(reports);r=reports.get(key) or {};latest_report={"date":key,"stage":r.get("stage"),"terminal":r.get("terminal"),"reported_at":r.get("reported_at")}
    return {"generated_at":now.isoformat(),"timezone":"America/New_York","overall":_overall(systems,now),"systems":systems,"edition":edition,"awareness":awareness,"last_outcome":latest_report,"policy":{"cutoff":"3:00 PM ET","content_polling":"Every minute, 7:00 AM–2:59 PM ET","reply_polling":"Every minute, 7:00 AM–2:59 PM ET","send_policy":"Immediately after valid approval","alerts":"Bobby only","imessage":"Disabled"}}

DASHBOARD_HTML=Path(__file__).with_name("iris_dashboard.html").read_text(encoding="utf-8")
