"""Pure, fail-open provider status parsers for the LHOS watchdog.

Invalid or changed schemas return no confirmed outage; they must never suppress an alert.
"""

def google_workspace_outage(payload):
    if not isinstance(payload,list):return None
    for incident in payload:
        if not isinstance(incident,dict) or incident.get("end"):continue
        update=incident.get("most_recent_update") or {}
        status=(update.get("status") if isinstance(update,dict) else None) or incident.get("status_impact")
        if status in (None,"AVAILABLE"):continue
        products=incident.get("affected_products") or []
        names=[incident.get("service_name") or ""]
        for product in products:
            if isinstance(product,dict):names.append(str(product.get("title") or product.get("name") or product.get("id") or ""))
            else:names.append(str(product))
        name=" / ".join(x for x in names if x) or "Google Workspace"
        if any(key in name.lower() for key in ("gmail","drive","calendar","workspace")):
            return f"Google Workspace outage ({name}: {status})"
    return None

def instatus_outage(payload):
    if not isinstance(payload,dict) or not isinstance(payload.get("page"),dict):return None
    page=payload["page"];status=str(page.get("status") or "").upper()
    if not status or status=="UP":return None
    return f"{page.get('name') or 'Provider'} outage (status: {status})"

def github_statuspage_outage(payload):
    if not isinstance(payload,dict) or not isinstance(payload.get("status"),dict):return None
    indicator=str(payload["status"].get("indicator") or "none").lower()
    if indicator in ("major","critical"):return f"GitHub outage (statuspage: {indicator})"
    return None
