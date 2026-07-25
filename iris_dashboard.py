# Truthful, read-only IRIS operations dashboard model and self-contained UI.
from __future__ import annotations
from datetime import datetime
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


def _overall(systems):
    light=max((x["light"] for x in systems.values()),key=lambda x:SEVERITY[x])
    if light=="green":return _item("green","IRIS is healthy","Core services are responding and no manual action is needed.")
    if light=="orange":return _item("orange","IRIS is watching","Nothing is confirmed broken. One signal needs observation.")
    return _item("red","IRIS needs attention","A verified health signal is broken or stale. Self-healing is already attempting recovery.")


def _scheduler(now,heartbeat,state):
    prepare=heartbeat.get("prepare");replies=heartbeat.get("check_replies")
    if now.hour<7:
        return _item("green","Scheduler resting","The daily polling window is scheduled for 7:00 AM–3:00 PM ET.",prepare)
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
        if now.hour<7:light,label,detail="green","Day has not started","The first content check is scheduled for 7:00 AM ET."
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
    elif wd["light"]=="orange":awareness.append(_item("orange","Observe the next cloud check","No repair is requested yet. The dashboard will turn red only after the watchdog is genuinely stale."))
    else:awareness.append(_item("red","Cloud self-healing evidence is stale","The independent watchdog has not checked in within its safe window."))
    latest_report=None
    if reports:
        key=max(reports);r=reports.get(key) or {};latest_report={"date":key,"stage":r.get("stage"),"terminal":r.get("terminal"),"reported_at":r.get("reported_at")}
    return {"generated_at":now.isoformat(),"timezone":"America/New_York","overall":_overall(systems),"systems":systems,"edition":edition,"awareness":awareness,"last_outcome":latest_report,"policy":{"cutoff":"3:00 PM ET","content_polling":"Every minute, 7:00 AM–2:59 PM ET","reply_polling":"Every minute, 7:00 AM–2:59 PM ET","send_policy":"Immediately after valid approval","alerts":"Bobby only","imessage":"Disabled"}}

DASHBOARD_HTML=r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>IRIS Health · LifeHouse OS</title>
<style>
:root{--bg:#0a0e12;--surface:#10161c;--ink:#f2ede4;--muted:#99a2a8;--faint:#687178;--line:rgba(242,237,228,.11);--gold:#c9ab73;--green:#62d6a4;--orange:#e5ab61;--red:#ef7778;--serif:Baskerville,"Iowan Old Style","Palatino Linotype",serif;--sans:-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",sans-serif}*{box-sizing:border-box}html{background:var(--bg);color:var(--ink);font-family:var(--sans)}body{margin:0;min-height:100vh;background:radial-gradient(900px 500px at 78% -10%,rgba(201,171,115,.10),transparent 62%),linear-gradient(180deg,#0b1015 0%,#090d11 100%)}button{font:inherit}main{width:min(1240px,calc(100% - 56px));margin:auto;padding:34px 0 72px}.topbar{display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line);padding:0 0 24px}.brand{display:flex;align-items:center;gap:14px}.monogram{font-family:var(--serif);font-size:24px;letter-spacing:.16em}.brand-copy{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted)}.live{display:flex;align-items:center;gap:10px;color:var(--muted);font-size:12px}.live-dot{width:7px;height:7px;border-radius:50%;background:var(--green);box-shadow:0 0 0 5px rgba(98,214,164,.08)}.refresh{height:40px;padding:0 16px;color:var(--ink);background:transparent;border:1px solid var(--line);border-radius:8px;cursor:pointer}.refresh:hover{border-color:rgba(242,237,228,.28);background:rgba(255,255,255,.025)}.hero{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(280px,.6fr);gap:56px;padding:74px 0 62px;border-bottom:1px solid var(--line)}.eyebrow,.section-kicker{font-size:11px;letter-spacing:.19em;text-transform:uppercase;color:var(--gold)}h1{font-family:var(--serif);font-weight:400;font-size:clamp(52px,7vw,92px);line-height:.92;letter-spacing:-.045em;margin:18px 0 22px;max-width:780px}.hero-detail{max-width:650px;font-size:18px;line-height:1.58;color:var(--muted);margin:0}.hero-status{display:flex;flex-direction:column;justify-content:center;align-items:flex-start;padding-left:18px}.orb{width:112px;height:112px;border-radius:50%;background:var(--light,var(--green));box-shadow:0 0 0 1px color-mix(in srgb,var(--light) 45%,transparent),0 0 64px color-mix(in srgb,var(--light) 24%,transparent),inset 0 -18px 35px rgba(0,0,0,.17);position:relative;margin-bottom:28px}.orb:after{content:"";position:absolute;inset:15px;border-radius:50%;border:1px solid rgba(255,255,255,.24)}.status-label{font-family:var(--serif);font-size:28px}.status-meta{font-size:12px;color:var(--muted);margin-top:8px}.section{padding:52px 0;border-bottom:1px solid var(--line)}.section-head{display:flex;align-items:end;justify-content:space-between;gap:24px;margin-bottom:28px}.section h2{font-family:var(--serif);font-weight:400;font-size:40px;letter-spacing:-.025em;margin:8px 0 0}.section-note{max-width:430px;color:var(--muted);font-size:14px;line-height:1.55}.system-grid{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--line);border-radius:16px;overflow:hidden}.system{min-height:190px;padding:24px;border-right:1px solid var(--line);background:rgba(255,255,255,.018)}.system:last-child{border:0}.light-row{display:flex;align-items:center;gap:9px;margin-bottom:38px}.dot{width:9px;height:9px;border-radius:50%;background:var(--light);box-shadow:0 0 18px color-mix(in srgb,var(--light) 45%,transparent)}.light-name{font-size:10px;text-transform:uppercase;letter-spacing:.15em;color:var(--muted)}.system h3,.attention h3{font-size:15px;font-weight:520;margin:0 0 9px}.system p,.attention p{font-size:13px;line-height:1.55;color:var(--muted);margin:0}.edition-wrap{display:grid;grid-template-columns:minmax(0,1.08fr) minmax(300px,.92fr);gap:24px}.edition-main,.edition-side{background:var(--surface);border:1px solid var(--line);border-radius:18px;padding:30px}.edition-title{display:flex;align-items:center;gap:12px}.edition-title h3{font-family:var(--serif);font-weight:400;font-size:32px;margin:0}.edition-detail{color:var(--muted);font-size:15px;line-height:1.6;margin:18px 0 34px;max-width:680px}.timeline{display:grid;grid-template-columns:repeat(4,1fr);position:relative}.timeline:before{content:"";position:absolute;left:7%;right:7%;top:10px;height:1px;background:var(--line)}.step{position:relative}.step-mark{width:21px;height:21px;border-radius:50%;border:1px solid var(--line);background:var(--surface);position:relative;z-index:1;margin-bottom:13px}.step.done .step-mark{background:var(--green);border-color:var(--green)}.step.current .step-mark{border-color:var(--orange);box-shadow:0 0 0 5px rgba(229,171,97,.09)}.step-name{font-size:12px;color:var(--muted)}.edition-side dl{margin:0}.edition-side div{padding:15px 0;border-bottom:1px solid var(--line)}.edition-side div:last-child{border:0}.edition-side dt{font-size:10px;text-transform:uppercase;letter-spacing:.14em;color:var(--faint);margin-bottom:7px}.edition-side dd{font-size:14px;margin:0}.attention-list{border-top:1px solid var(--line)}.attention{display:grid;grid-template-columns:18px minmax(190px,.7fr) minmax(0,1.3fr);gap:18px;padding:22px 0;border-bottom:1px solid var(--line);align-items:start}.attention .dot{margin-top:5px}.footer{display:flex;justify-content:space-between;gap:24px;padding-top:28px;color:var(--faint);font-size:11px;line-height:1.5}.error{padding:28px;border:1px solid rgba(239,119,120,.4);color:#ffc1c1;border-radius:12px;margin-top:40px}.skeleton{opacity:.45;animation:pulse 1.5s ease-in-out infinite}@keyframes pulse{50%{opacity:.7}}@media(max-width:900px){main{width:min(100% - 32px,760px)}.hero{grid-template-columns:1fr;padding:52px 0}.hero-status{padding:0;flex-direction:row;align-items:center;gap:22px}.orb{width:74px;height:74px;margin:0}.system-grid{grid-template-columns:1fr 1fr}.system:nth-child(2){border-right:0}.system:nth-child(-n+2){border-bottom:1px solid var(--line)}.edition-wrap{grid-template-columns:1fr}.section-head{align-items:start;flex-direction:column}}@media(max-width:560px){main{width:calc(100% - 24px);padding-top:20px}.brand-copy{display:none}.refresh{padding:0 12px}.topbar{padding-bottom:18px}.hero{padding:42px 0}.hero-detail{font-size:16px}.system-grid{grid-template-columns:1fr}.system{border-right:0;border-bottom:1px solid var(--line)!important;min-height:155px}.system:last-child{border-bottom:0!important}.system .light-row{margin-bottom:25px}.section{padding:42px 0}.section h2{font-size:34px}.edition-main,.edition-side{padding:23px}.timeline{gap:8px}.attention{grid-template-columns:16px 1fr}.attention p{grid-column:2}.footer{flex-direction:column}.live span:last-child{display:none}}@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style></head><body><main><header class="topbar"><div class="brand"><div class="monogram">IRIS</div><div class="brand-copy">LifeHouse OS · Operations</div></div><div class="live"><span class="live-dot"></span><span id="liveLabel">Live read-only status</span><button id="refresh" class="refresh" type="button">Refresh</button></div></header><section class="hero"><div><div class="eyebrow">System health</div><h1 id="heroTitle" class="skeleton">Reading live signals…</h1><p id="heroDetail" class="hero-detail">Checking IRIS without guessing.</p></div><div class="hero-status"><div id="orb" class="orb" style="--light:var(--green)"></div><div><div id="statusLabel" class="status-label">Connecting</div><div id="checkedAt" class="status-meta">One moment</div></div></div></section><section class="section"><div class="section-head"><div><div class="section-kicker">Core systems</div><h2>What is working now</h2></div><div class="section-note">A green system is verified. Orange means observe, not panic. Red appears only for a broken or stale signal.</div></div><div id="systems" class="system-grid"></div></section><section class="section"><div class="section-head"><div><div class="section-kicker">Today</div><h2>Edition progress</h2></div><div class="section-note">Business progress is separate from system health. Waiting for content can be orange while IRIS remains healthy.</div></div><div class="edition-wrap"><div class="edition-main"><div class="edition-title"><span id="editionDot" class="dot"></span><h3 id="editionLabel">Loading today</h3></div><p id="editionDetail" class="edition-detail"></p><div id="timeline" class="timeline"></div></div><aside class="edition-side"><dl><div><dt>Source</dt><dd id="source">—</dd></div><div><dt>Polling window</dt><dd id="window">—</dd></div><div><dt>Approval policy</dt><dd id="sendPolicy">—</dd></div><div><dt>Operational alerts</dt><dd id="alerts">—</dd></div></dl></aside></div></section><section class="section"><div class="section-head"><div><div class="section-kicker">Ahead of time</div><h2>What to be aware of</h2></div><div class="section-note">No invented deadlines. No urgency language unless a live check proves something is wrong.</div></div><div id="awareness" class="attention-list"></div></section><footer class="footer"><span>Private, read-only operations view · America/New_York</span><span>IRIS refreshes this view every 30 seconds. Self-healing runs independently.</span></footer><div id="error" class="error" hidden></div></main><script>
const colors={green:'var(--green)',orange:'var(--orange)',red:'var(--red)'},names={green:'Verified',orange:'Observe',red:'Attention'};const esc=(v)=>String(v??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));const lightStyle=(v)=>`--light:${colors[v]||colors.red}`;
function render(d){const o=d.overall;document.getElementById('heroTitle').textContent=o.label;document.getElementById('heroTitle').classList.remove('skeleton');document.getElementById('heroDetail').textContent=o.detail;document.getElementById('statusLabel').textContent=names[o.light];document.getElementById('orb').style.setProperty('--light',colors[o.light]);const checked=new Date(d.generated_at);document.getElementById('checkedAt').textContent='Checked '+checked.toLocaleTimeString([],{hour:'numeric',minute:'2-digit',second:'2-digit',timeZone:'America/New_York'})+' ET';const order=['api','google','scheduler','watchdog'];document.getElementById('systems').innerHTML=order.map(k=>{const x=d.systems[k];return `<article class="system" style="${lightStyle(x.light)}"><div class="light-row"><span class="dot"></span><span class="light-name">${names[x.light]}</span></div><h3>${esc(x.label)}</h3><p>${esc(x.detail)}</p></article>`}).join('');const e=d.edition;document.getElementById('editionDot').style.setProperty('--light',colors[e.light]);document.getElementById('editionLabel').textContent=e.label;document.getElementById('editionDetail').textContent=e.detail;document.getElementById('source').textContent=e.source;document.getElementById('window').textContent=e.window;document.getElementById('sendPolicy').textContent=d.policy.send_policy;document.getElementById('alerts').textContent=d.policy.alerts+' · iMessage '+d.policy.imessage.toLowerCase();document.getElementById('timeline').innerHTML=e.steps.map(s=>`<div class="step ${esc(s.state)}"><div class="step-mark"></div><div class="step-name">${esc(s.label)}</div></div>`).join('');document.getElementById('awareness').innerHTML=d.awareness.map(x=>`<article class="attention" style="${lightStyle(x.light)}"><span class="dot"></span><h3>${esc(x.label)}</h3><p>${esc(x.detail)}</p></article>`).join('');document.getElementById('error').hidden=true}
async function load(){const b=document.getElementById('refresh');b.disabled=true;b.textContent='Refreshing…';try{const r=await fetch('/api/iris-health',{cache:'no-store',credentials:'same-origin'});if(!r.ok)throw new Error('Health API returned '+r.status);render(await r.json())}catch(e){const x=document.getElementById('error');x.hidden=false;x.textContent='Live status could not be refreshed: '+e.message}finally{b.disabled=false;b.textContent='Refresh'}}document.getElementById('refresh').addEventListener('click',load);load();setInterval(load,30000);
</script></body></html>'''
