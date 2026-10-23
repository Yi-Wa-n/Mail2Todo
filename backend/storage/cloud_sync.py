"""Publish a sanitized read-only snapshot; never scans mail or sends model data."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
import urllib.request
from backend.storage.vault import HOME, account_home, account_id, crypt

_guard = threading.Lock()
_started = False


def clean(value):
    if isinstance(value, str):
        value = re.sub(r'https?://\S+', '[链接仅本机可见]', value)
        value = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[邮箱已隐藏]', value)
        return re.sub(r'(?<!\d)1[3-9]\d{9}(?!\d)', '[电话已隐藏]', value)
    if isinstance(value, list): return [clean(v) for v in value]
    if isinstance(value, dict): return {k: clean(v) for k, v in value.items()}
    return value


def snapshot(home=None, config_home=None, account=None):
    from backend.storage.repository import state
    home = Path(home) if home is not None else account_home()
    config_home = Path(config_home) if config_home is not None else HOME
    # Read-only transaction yields one consistent database snapshot; no initialization.
    db = sqlite3.connect((home/'ledger.sqlite3').resolve().as_uri()+'?mode=ro', uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute('BEGIN')
        current = state(db)
        fields = ('revision','emails','steps','manual_events','imported_at','last_scan')
        current = {k:current[k] for k in fields}
        current['account'] = account if account is not None else account_id()
        # State contains only extracted records, notes and schedule history, never mail payloads.
        current['result'] = {}
        filtered = []
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='local_filter_log'").fetchone():
            for r in db.execute('SELECT mail_id,reason,at FROM local_filter_log ORDER BY at DESC,mail_id DESC LIMIT 3000'):
                filtered.append({'id':r['mail_id'],'reason':r['reason'],'filtered_at':r['at'],
                                 'subject':'邮件标题仅本机可见','received':None,'available':False})
    finally: db.close()
    config = json.loads((config_home/'local-gate.json').read_text(encoding='utf-8'))
    # Task status may include host paths; export only public scheduling values.
    from backend.jobs.scheduler import status
    schedule = {k:v for k,v in status().items() if k in ('enabled','installed','interval_hours','logon_delay_minutes','retry_minutes','retry_count','next_run','last_run','last_result','pending','error')}
    current.update(schedule=schedule,job={'running':False,'message':'在线只读副本'})
    settings = {'model':config.get('codebuddy_model'), 'fallback':config.get('codebuddy_fallback_model'),
                'effort':config.get('reasoning_effort','low'), 'schedule':schedule,
                'automatic_enabled':schedule.get('enabled'), 'credentials_saved':False}
    audits = []
    for p in sorted((home/'local-gate/audits').glob('*/manifest.json'),reverse=True)[:200]:
        try:
            v=json.loads(p.read_text(encoding='utf-8'))
            audits.append({k:v.get(k) for k in ('audit_id','started_at','actual_model','model_status','sync_status','provider')})
        except (OSError,ValueError): pass
    return clean({'state':current,'settings':settings,'filtered':filtered,'audits':audits})


def request(config, method, body=None):
    # Do not follow redirects with credentials.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs): return None
    req=urllib.request.Request(config['url']+'/api/snapshot',data=body,method=method,
        headers={'OAI-Sites-Authorization':'Bearer '+config['service_token'],
                 'X-Upload-Key':config['upload_key'],'Content-Type':'application/json'})
    with urllib.request.build_opener(NoRedirect).open(req,timeout=25) as response:
        return json.load(response)


def sync_once(force=False):
    path=HOME/'cloud-sync.dpapi'
    if not path.exists(): return {'skipped':'not_configured'}
    if not _guard.acquire(blocking=False): return {'skipped':'busy'}
    status_path=HOME/'cloud-sync-status.json'
    try:
        config=json.loads(crypt(path.read_bytes(),decrypt=True))
        if not re.fullmatch(r'https://[a-z0-9.-]+\.chatgpt\.site',config['url']): raise ValueError('Invalid Site')
        body=snapshot()
        canonical=json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        fingerprint=hashlib.sha256(canonical.encode()).hexdigest()
        try: old=json.loads(status_path.read_text(encoding='utf-8'))
        except (OSError,ValueError): old={}
        if not force and old.get('ok') and old.get('fingerprint')==fingerprint: return {'skipped':'unchanged'}
        stamp=dt.datetime.now(dt.timezone.utc)
        payload={**body,'schema':1,'revision':time.time_ns()//1_000_000,'generated_at':stamp.isoformat()}
        raw=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()
        if len(raw)>4_000_000: raise ValueError('Snapshot too large')
        expected=hashlib.sha256(raw).hexdigest()
        written=request(config,'PUT',raw)
        checked=request(config,'GET')
        if checked!=written or checked.get('hash')!=expected or checked.get('revision')!=payload['revision']:
            raise RuntimeError('Cloud readback mismatch')
        result={'ok':True,'at':stamp.isoformat(),'fingerprint':fingerprint,'hash':expected,
                'emails':len(body['state']['emails']),'steps':len(body['state']['steps']),
                'manual_events':len(body['state']['manual_events'])}
    except Exception as error:
        result={'ok':False,'at':dt.datetime.now(dt.timezone.utc).isoformat(),'error_type':type(error).__name__}
    finally: _guard.release()
    temporary=status_path.with_suffix('.new')
    temporary.write_text(json.dumps(result),encoding='utf-8');temporary.replace(status_path)
    return result


def start_background():
    global _started
    if _started or not (HOME/'cloud-sync.dpapi').exists(): return
    _started=True
    def run():
        while True:
            try: sync_once()
            except Exception: pass
            time.sleep(60)
    threading.Thread(target=run,daemon=True,name='cloud-snapshot-sync').start()


if __name__=='__main__':
    import sys
    if '--configure-stdin' in sys.argv:
        if sys.stdin.isatty():
            import ctypes
            handle=ctypes.windll.kernel32.GetStdHandle(-10)
            mode=ctypes.c_uint()
            if not ctypes.windll.kernel32.GetConsoleMode(handle,ctypes.byref(mode)):
                raise RuntimeError('Cannot disable console echo')
            if not ctypes.windll.kernel32.SetConsoleMode(handle,mode.value & ~4):
                raise RuntimeError('Cannot disable console echo')
        print('Ready for hidden cloud configuration on stdin.',flush=True)
        value=json.loads(sys.stdin.readline())
        if not isinstance(value,dict) or not isinstance(value.get('url'),str) or not re.fullmatch(r'https://[a-z0-9.-]+\.chatgpt\.site',value['url']):
            raise ValueError('A verified HTTPS Site URL is required')
        if any(not isinstance(value.get(k),str) or not value[k] for k in ('service_token','upload_key')):
            raise ValueError('Service and upload credentials are required')
        path=HOME/'cloud-sync.dpapi';tmp=path.with_suffix('.new')
        tmp.write_bytes(crypt(json.dumps(value).encode()));tmp.replace(path)
        print('Cloud configuration saved with DPAPI.')
    else:
        result=sync_once(force='--force' in sys.argv)
        print(json.dumps(result,ensure_ascii=False))
        sys.exit(1 if result.get('ok') is False else 0)
