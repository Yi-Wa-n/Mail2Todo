"""SQLite is the only authority for emails, notes and individual step completion."""
import datetime as dt
import json
import sqlite3
from contextlib import nullcontext
from backend.storage import mail_schedule

from backend.mail.core import records,action_items,inactive_ids,get_meta,set_meta,canonical,digest,now
from backend.mail.steps import project
from backend.storage.vault import HOME,crypt
from backend.storage.vault import account_home


def connect():
    db=sqlite3.connect(account_home()/'ledger.sqlite3',timeout=20)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA busy_timeout=20000')
    return db


def timestamp(value):
    if not value:return None
    try:
        text=str(value)
        if text.isdigit() and len(text)>=12:
            parsed=dt.datetime.fromtimestamp(int(text)/1000,dt.timezone.utc)
        else:
            parsed=dt.datetime.fromisoformat(text)
            if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=dt.timezone(dt.timedelta(hours=8)))
        return parsed.astimezone(dt.timezone(dt.timedelta(hours=8))).isoformat()
    except (ValueError,OverflowError,OSError):return None


def values(item):
    value={'公司':item.get('manual_company') or item['company'],
           '岗位':item.get('manual_role') or item['role'],'通知类型':item['category'],
           '待办事项':item['next_action'],'开始时间':item.get('event_at') or '',
           '截止时间':item.get('deadline') or '', '已完成':bool(item.get('manual_completed'))}
    value.update(item.get('manual_values',{}))
    return value


def revision(db):return get_meta(db,'local_revision',0)


def bump(db):set_meta(db,'local_revision',revision(db)+1)


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS mail_index(mail_id TEXT PRIMARY KEY,subject TEXT,received TEXT);
    CREATE TABLE IF NOT EXISTS local_steps(id TEXT PRIMARY KEY,mail_id TEXT,payload TEXT,done INTEGER NOT NULL,version INTEGER NOT NULL DEFAULT 0);
    CREATE INDEX IF NOT EXISTS step_mail ON local_steps(mail_id);
    CREATE TABLE IF NOT EXISTS local_overrides(mail_id TEXT PRIMARY KEY,notes TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS manual_events(id TEXT PRIMARY KEY,payload TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 0,deleted INTEGER NOT NULL DEFAULT 0);
    ''')
    if not get_meta(db,'local_only_schema'):
        sync_local(db,initial=True)
        set_meta(db,'local_only_schema',1)
        set_meta(db,'local_imported_at',now().isoformat())
        set_meta(db,'pending_review_audits',[])
        # Remove live online bindings from this database. The import snapshot is
        # retained privately as provenance and is never opened by the service.
        with db:
            for key in ('binding','detail_record_map','schedule_record_map','schedule_snapshots',
                        'step_completion_states','pending_detail_add','schedule_pending_add'):
                db.execute('DELETE FROM meta WHERE key=?',(key,))
    if not get_meta(db,'local_scan_namespace'):
        set_meta(db,'import_source_last_scan',get_meta(db,'local_gate_last_success'))
        set_meta(db,'local_gate_last_success',None)
        set_meta(db,'local_gate_last_result',{})
        set_meta(db,'local_scan_namespace',1)

    if not get_meta(db,'mail_schedule_edit_schema'):
        sync_local(db)
        set_meta(db,'mail_schedule_edit_schema',1)


def sync_local(db,initial=False,commit=True):
    with (db if commit else nullcontext()):
        items=records(db)
        byid={i['id']:i for i in items}
        snapshots=get_meta(db,'schedule_snapshots',{}) if initial else {}
        completion=get_meta(db,'step_completion_states',{}) if initial else {}
        remote_to_mail={v:k for k,v in get_meta(db,'detail_record_map',{}).items()} if initial else {}
        existing={r['id']:r for r in db.execute('SELECT * FROM local_steps')}
        mail_schedule.prepare(db,items)
        desired=project(items,values)
        for item in items:
            db.execute('INSERT OR REPLACE INTO mail_index VALUES (?,?,?)',(item['id'],item['subject'],item['received']))
            db.execute('INSERT OR IGNORE INTO local_overrides(mail_id,notes) VALUES (?,?)',(item['id'],item.get('notes','')))
        for key in set(desired)|set(snapshots):
            v=desired.get(key) or {**snapshots[key],'已失效':True}
            mail_id=v.get('mail_id') or remote_to_mail.get(v.get('原邮件')) or key.rsplit(':',1)[0]
            item=byid.get(mail_id)
            if not item:continue
            old=existing.get(key)
            payload={'id':key,'mail_id':mail_id,'company':v['公司'],'role':v['岗位'],
                     'category':v['通知类型'],'label':v['节点类型'],'action':v['待办事项'],
                     'at':timestamp(v.get('节点时间')),'active':not v.get('已失效',False),
                     'estimated':'估算' in v.get('时间依据',''),'basis':v.get('时间依据',''),
                     'received':item['received']}
            done=bool(old['done']) if old else bool(completion.get(key,v.get('已完成',False)))
            db.execute('INSERT INTO local_steps(id,mail_id,payload,done,version) VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET mail_id=excluded.mail_id,payload=excluded.payload,version=excluded.version',
                       (key,mail_id,canonical(payload),int(done),old['version']+int(json.loads(old['payload'])!=payload) if old else 0))
        for key,row in existing.items():
            if key not in desired and key not in snapshots:
                value=json.loads(row['payload']);value['active']=False
                db.execute('UPDATE local_steps SET payload=? WHERE id=?',(canonical(value),key))
        # Every persisted local record is acknowledged locally, never as an
        # external document write. New audit sync checks use this hash.
        for row in db.execute('SELECT mail_id,payload FROM records').fetchall():
            db.execute('UPDATE records SET synced_hash=? WHERE mail_id=?',(digest(row['payload']),row['mail_id']))
        db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", ("local_revision",canonical(revision(db)+1)))
        return {'local_records_verified':len(items),'steps_verified':db.execute('SELECT count(*) FROM local_steps').fetchone()[0]}


def state(db):
    steps=[]
    by_mail={}
    for row in db.execute('SELECT * FROM local_steps'):
        value=json.loads(row['payload']);value.update(done=bool(row['done']),version=row['version'])
        steps.append(value)
        if value['active']:by_mail.setdefault(value['mail_id'],[]).append(value)
    raw=[]
    for row in db.execute('SELECT r.mail_id,r.payload,m.subject,m.received,o.notes,o.version FROM records r JOIN mail_index m ON m.mail_id=r.mail_id JOIN local_overrides o ON o.mail_id=r.mail_id'):
        item=json.loads(row['payload']);item.update(id=row['mail_id'],subject=row['subject'],received=row['received'])
        raw.append((item,row))
    inactive=inactive_ids([v[0] for v in raw])
    emails=[]
    for item,row in raw:
        if not item.get('requires_action',item.get('category') in ('测评','笔试','面试','AI面试','补充材料','录用')):continue
        v=values(item);related=by_mail.get(item['id'],[])
        emails.append({'id':item['id'],'company':v['公司'],'role':v['岗位'],'category':v['通知类型'],
                       'action':v['待办事项'],'start':timestamp(v.get('开始时间')),'deadline':timestamp(v.get('截止时间')),
                       'received':row['received'],'subject':row['subject'],'notes':row['notes'],
                       'done':all(s['done'] for s in related) if related else bool(item.get('manual_completed')),
                       'active':item['id'] not in inactive,'version':row['version'],
                       'supersedes':item.get('supersedes'),
                       'original':item.get('manual_schedule_original',mail_schedule.extracted(item)),
                       'notification':mail_schedule.extracted(item),
                       'manual_fields':[key for key,label in mail_schedule.FIELDS.items() if label in item.get('manual_values',{})],
                       'edit_history':item.get('manual_schedule_history',[]),
                       'pending_manual_review':mail_schedule.pending(item)})
    emails.sort(key=lambda v:v['received'],reverse=True)
    steps.sort(key=lambda v:v['received'],reverse=True)
    manual=[{**json.loads(r['payload']),'id':r['id'],'version':r['version']} for r in db.execute('SELECT * FROM manual_events WHERE deleted=0')]
    return {'revision':revision(db),'emails':emails,'steps':steps,'manual_events':manual,'imported_at':get_meta(db,'local_imported_at'),
            'last_scan':get_meta(db,'local_gate_last_success'),'result':get_meta(db,'local_gate_last_result',{})}


def edit_mail_schedule(db,value):
    return mail_schedule.edit(db,value,sync_local)


def mutate(db,kind,value):
    if not isinstance(value,dict):raise ValueError('无效请求')
    key=value.get('id')
    if not isinstance(key,str) or len(key)>100:raise ValueError('无效记录')
    version=value.get('version')
    if type(version) is not int:raise ValueError('缺少记录版本，请刷新')
    if kind in ('step','email') and type(value.get('done')) is not bool:raise ValueError('完成状态须为勾选值')
    if kind=='notes' and (not isinstance(value.get('notes'),str) or len(value['notes'])>10000):raise ValueError('备注最多10000字')
    if kind not in ('step','email','notes'):raise ValueError('未知操作')
    with db:
        if kind=='step':
            row=db.execute('SELECT * FROM local_steps WHERE id=?',(key,)).fetchone()
            if not row or not json.loads(row['payload'])['active']:raise ValueError('步骤不存在或已失效')
            if row['version']!=version:raise ValueError('记录已变化，请刷新后重试')
            mail_id=row['mail_id']
            db.execute('UPDATE local_steps SET done=?,version=version+1 WHERE id=?',(int(value['done']),key))
        else:
            row=db.execute('SELECT * FROM local_overrides WHERE mail_id=?',(key,)).fetchone()
            if not row:raise ValueError('邮件记录不存在')
            if row['version']!=version:raise ValueError('记录已变化，请刷新后重试')
            mail_id=key
            if kind=='notes':db.execute('UPDATE local_overrides SET notes=? WHERE mail_id=?',(value['notes'],key))
            else:
                for step in db.execute('SELECT id,payload FROM local_steps WHERE mail_id=?',(key,)).fetchall():
                    if json.loads(step['payload'])['active']:
                        db.execute('UPDATE local_steps SET done=?,version=version+1 WHERE id=?',(int(value['done']),step['id']))
        record=db.execute('SELECT payload FROM records WHERE mail_id=?',(mail_id,)).fetchone()
        payload=json.loads(record['payload'])
        if kind=='notes':payload['notes']=value['notes']
        else:
            active=[s for s in db.execute('SELECT payload,done FROM local_steps WHERE mail_id=?',(mail_id,)) if json.loads(s['payload'])['active']]
            payload['manual_completed']=all(s['done'] for s in active) if active else value['done']
        encoded=canonical(payload)
        db.execute('UPDATE records SET payload=?,synced_hash=? WHERE mail_id=?',(encoded,digest(encoded),mail_id))
        db.execute('UPDATE local_overrides SET version=version+1 WHERE mail_id=?',(mail_id,))
    bump(db)


def mail_preview(db,key):
    from backend.mail.core import safe_text
    row=db.execute('SELECT payload FROM mails WHERE id=?',(key,)).fetchone()
    if not row:raise ValueError('原邮件不存在')
    mail=json.loads(crypt(row['payload'],decrypt=True))
    return {'subject':safe_text(mail['subject']),'received':mail['received'],'body':safe_text(mail['body'])}


def edit_manual(db,value):
    """Manual schedules never write any mail/step/override table. Delete is recoverable."""
    import uuid
    if not isinstance(value,dict):raise ValueError('无效日程')
    try:key=str(uuid.UUID(value.get('id','')))
    except (ValueError,TypeError,AttributeError):raise ValueError('无效日程标识') from None
    op=value.get('op')
    if op not in ('create','update','complete','delete','restore'):raise ValueError('无效操作')
    row=db.execute('SELECT * FROM manual_events WHERE id=?',(key,)).fetchone()
    if op=='create' and row:return  # Same client request is never inserted twice.
    if op!='create':
        if not row:raise ValueError('日程不存在')
        if type(value.get('version')) is not int or value['version']!=row['version']:raise ValueError('日程已变化，请刷新后重试')
        if row['deleted'] and op!='restore':raise ValueError('日程已删除')
    payload=json.loads(row['payload']) if row else {'done':False,'created_at':now().isoformat()}
    if op in ('create','update'):
        title=value.get('title');notes=value.get('notes','')
        if not isinstance(title,str) or not title.strip() or len(title)>200:raise ValueError('请填写日程标题，最多200字')
        if not isinstance(notes,str) or len(notes)>10000:raise ValueError('备注最多10000字')
        times={}
        for name in ('start','deadline'):
            raw=value.get(name)
            if raw in (None,''):times[name]=None;continue
            try:
                if not isinstance(raw,str):raise ValueError()
                parsed=dt.datetime.fromisoformat(raw)
                if parsed.tzinfo is None or parsed.utcoffset() is None:raise ValueError()
                times[name]=parsed.astimezone(dt.timezone(dt.timedelta(hours=8))).isoformat()
            except (ValueError,TypeError):raise ValueError('时间必须包含时区') from None
        if not any(times.values()):raise ValueError('至少填写开始时间或截止时间')
        payload.update(title=title.strip(),notes=notes,**times)
    if op=='complete':
        if type(value.get('done')) is not bool:raise ValueError('无效完成状态')
        payload['done']=value['done']
    payload['updated_at']=now().isoformat()
    with db:
        if op=='create':db.execute('INSERT INTO manual_events(id,payload) VALUES (?,?)',(key,canonical(payload)))
        else:db.execute('UPDATE manual_events SET payload=?,version=version+1,deleted=? WHERE id=?',(canonical(payload),int(op=='delete'),key))
    bump(db)
