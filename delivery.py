"""Recipient-level idempotent delivery with atomic persistent ledger."""
import fcntl, json, os, tempfile
from datetime import datetime, timezone
from pathlib import Path

def atomic_json_write(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name+'.', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def load_json(path: Path, default):
    try: return json.loads(path.read_text())
    except Exception: return default

def deliver_once(*, date_key, subject, html_body, contacts, suppressed, ledger_file, already_sent, send_one, unsubscribe_base):
    """Deliver at most once per address. Safe to call repeatedly after partial failure."""
    ledger_file=Path(ledger_file); lock_file=ledger_file.with_suffix('.lock'); lock_file.parent.mkdir(parents=True,exist_ok=True)
    with open(lock_file,'w') as lf:
        fcntl.flock(lf,fcntl.LOCK_EX)
        ledger=load_json(ledger_file, {'date':date_key,'subject':subject,'recipients':{}})
        if ledger.get('date')!=date_key or ledger.get('subject')!=subject:
            raise RuntimeError('ledger identity mismatch')
        dedup={}
        for c in contacts:
            addr=(c.get('email') or '').strip().lower()
            if addr and addr not in dedup: dedup[addr]=c.get('name') or addr.split('@')[0].title()
        suppressed={x.strip().lower() for x in suppressed}
        errors=[]; newly_sent=[]; existing=[]; skipped=[]
        for addr,name in dedup.items():
            entry=ledger['recipients'].setdefault(addr, {'name':name,'status':'pending'})
            if addr in suppressed:
                entry.update({'status':'suppressed','updated_at':datetime.now(timezone.utc).isoformat()}); skipped.append(addr); atomic_json_write(ledger_file,ledger); continue
            if entry.get('status') in ('sent','existing'):
                existing.append(addr); continue
            try:
                found=already_sent(addr,subject)
            except Exception as exc:
                entry.update({'status':'blocked_precheck','error':str(exc),'updated_at':datetime.now(timezone.utc).isoformat()});errors.append({'email':addr,'error':'sent-mail precheck failed: '+str(exc)});atomic_json_write(ledger_file,ledger);continue
            if found:
                entry.update({'status':'existing','updated_at':datetime.now(timezone.utc).isoformat()});existing.append(addr);atomic_json_write(ledger_file,ledger);continue
            personalized=html_body.replace('RECIPIENT_NAME_PLACEHOLDER',f'Hello {name}!').replace('UNSUB_URL_PLACEHOLDER',f'{unsubscribe_base}/?email={addr}')
            if 'RECIPIENT_NAME_PLACEHOLDER' in personalized or 'UNSUB_URL_PLACEHOLDER' in personalized:
                entry.update({'status':'error','error':'personalization failed'});errors.append({'email':addr,'error':'personalization failed'});atomic_json_write(ledger_file,ledger);continue
            try:
                result=send_one(addr,subject,personalized)
                entry.update({'status':'sent','message_id':(result or {}).get('id'),'sent_at':datetime.now(timezone.utc).isoformat()});newly_sent.append(addr)
            except Exception as exc:
                # Ambiguous transport failure: recheck Sent Mail before recording failure.
                try: confirmed=already_sent(addr,subject)
                except Exception: confirmed=False
                if confirmed:
                    entry.update({'status':'existing','updated_at':datetime.now(timezone.utc).isoformat()});existing.append(addr)
                else:
                    entry.update({'status':'error','error':str(exc),'updated_at':datetime.now(timezone.utc).isoformat()});errors.append({'email':addr,'error':str(exc)})
            atomic_json_write(ledger_file,ledger)
        delivered=[a for a,e in ledger['recipients'].items() if e.get('status') in ('sent','existing')]
        pending=[a for a,e in ledger['recipients'].items() if e.get('status') not in ('sent','existing','suppressed')]
        ledger.update({'updated_at':datetime.now(timezone.utc).isoformat(),'delivered_count':len(delivered),'suppressed_count':len(skipped),'pending_count':len(pending),'complete':not pending})
        atomic_json_write(ledger_file,ledger)
        return {'delivered_count':len(delivered),'newly_sent_count':len(newly_sent),'existing_count':len(existing),'suppressed_count':len(skipped),'pending_count':len(pending),'errors':errors,'complete':not pending}
