"""Manual schedule corrections kept alongside immutable extraction snapshots."""
import datetime as dt
import json
from backend.mail.core import canonical, digest, now, inactive_ids
from backend.mail.deadlines import date_only

FIELDS = {'category':'通知类型', 'start':'开始时间', 'deadline':'截止时间', 'action':'待办事项'}
CATEGORIES = ('测评', '笔试', 'AI面试', '面试', '补充材料', '录用', '其他招聘通知')
META = ('manual_values', 'manual_schedule_original', 'manual_schedule_source',
        'manual_schedule_history', 'manual_schedule_checked_source')


def extracted(item):
    return {'category':item['category'], 'start':item.get('event_at') or None,
            'deadline':item.get('deadline') or None, 'action':item['next_action']}


def effective(item):
    result = extracted(item)
    patches = item.get('manual_values', {})
    for key,label in FIELDS.items():
        if label in patches:
            result[key] = (patches[label] or None) if key in ('start','deadline') else patches[label]
    return result


def pending(item):
    source = extracted(item)
    checked = item.get('manual_schedule_checked_source', source)
    return [key for key,label in FIELDS.items()
            if label in item.get('manual_values', {}) and source[key] != checked.get(key)]


def persist(db, item):
    row = db.execute('SELECT payload FROM records WHERE mail_id=?', (item['id'],)).fetchone()
    payload = json.loads(row['payload'])
    for key in META:
        if key in item:payload[key] = item[key]
    encoded = canonical(payload)
    db.execute('UPDATE records SET payload=?,synced_hash=? WHERE mail_id=?',
               (encoded, digest(encoded), item['id']))


def prepare(db, items):
    """Initialize snapshots once and inherit only explicitly corrected fields."""
    by_id = {item['id']:item for item in items}
    visited = set()
    def visit(item, chain):
        key = item['id']
        if key in visited or key in chain:return
        previous = by_id.get(item.get('supersedes'))
        if previous:visit(previous, chain | {key})
        source = extracted(item)
        changed = False
        if 'manual_schedule_original' not in item:
            item['manual_schedule_original'] = dict(source)
            item['manual_schedule_history'] = []
            item['manual_schedule_checked_source'] = dict(source)
            if previous:item['manual_schedule_history'] = list(previous.get('manual_schedule_history', []))
            if previous and not item.get('manual_values'):
                patches = {label:value for label,value in previous.get('manual_values', {}).items() if label in FIELDS.values()}
                if patches:
                    item['manual_values'] = patches
                    item['manual_schedule_checked_source'] = dict(previous.get('manual_schedule_checked_source', extracted(previous)))
                    item['manual_schedule_history'] = list(previous.get('manual_schedule_history', [])) + [{
                        'at':now().isoformat(), 'kind':'inherited', 'source_mail_id':previous['id'],
                        'before':source, 'after':effective(item),
                        'fields':[key for key,label in FIELDS.items() if label in patches]}]
            if not item['manual_schedule_history'] and any(label in item.get('manual_values', {}) for label in FIELDS.values()):
                item['manual_schedule_history'].append({
                    'at':now().isoformat(), 'kind':'imported', 'before':source,
                    'after':effective(item), 'fields':[name for name,label in FIELDS.items() if label in item.get('manual_values', {})]})
            changed = True
        old_source = item.get('manual_schedule_source', source)
        if old_source != source:
            item.setdefault('manual_schedule_history', []).append({
                'at':now().isoformat(), 'kind':'notification', 'before':old_source,
                'after':source, 'fields':[key for key in FIELDS if old_source.get(key) != source[key]]})
            db.execute('UPDATE local_overrides SET version=version+1 WHERE mail_id=?', (key,))
            changed = True
        if item.get('manual_schedule_source') != source:
            item['manual_schedule_source'] = dict(source)
            changed = True
        if changed:persist(db, item)
        visited.add(key)
    for item in items:visit(item, set())


def normalize(value):
    result = {key:value.get(key) for key in FIELDS}
    if result['category'] not in CATEGORIES:raise ValueError('请选择有效的通知类型')
    if not isinstance(result['action'], str) or not result['action'].strip() or len(result['action']) > 10000:
        raise ValueError('请填写待办事项，最多10000字')
    result['action'] = result['action'].strip()
    for key in ('start', 'deadline'):
        raw = result[key]
        if raw in (None, ''):result[key] = None;continue
        try:
            if not isinstance(raw, str):raise ValueError()
            if key=='deadline' and date_only(raw):
                result[key]=dt.date.fromisoformat(raw).isoformat()
                continue
            parsed = dt.datetime.fromisoformat(raw)
            if parsed.tzinfo is None or parsed.utcoffset() is None:raise ValueError()
            result[key] = parsed.astimezone(dt.timezone(dt.timedelta(hours=8))).isoformat()
        except (ValueError, TypeError, OverflowError):raise ValueError('请输入有效的北京时间') from None
    return result


def edit(db, value, sync):
    if not isinstance(value, dict) or value.get('op') not in ('save','use-notification'):
        raise ValueError('无效修订操作')
    key = value.get('id')
    if not isinstance(key, str) or len(key) > 100:raise ValueError('无效邮件记录')
    desired = normalize(value) if value['op'] == 'save' else None
    with db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT version FROM local_overrides WHERE mail_id=?', (key,)).fetchone()
        if not row:raise ValueError('邮件记录不存在')
        if type(value.get('version')) is not int or row['version'] != value['version']:
            raise ValueError('记录已变化，请刷新后重试')
        items = [json.loads(r['payload']) | {'id':r['mail_id']} for r in db.execute('SELECT mail_id,payload FROM records')]
        item = next((item for item in items if item['id'] == key), None)
        if item is None or key in inactive_ids(items):raise ValueError('旧通知已被覆盖，请编辑最新安排')
        if item.get('same_task_as'):raise ValueError('这封提醒与原通知共用日程，请在原通知或日历中修改')
        if not item.get('requires_action', item.get('category') in CATEGORIES):raise ValueError('这封邮件没有可编辑的日程')
        before = effective(item)
        patches = dict(item.get('manual_values', {}))
        if desired is None:
            desired = extracted(item)
            fields = [name for name,label in FIELDS.items() if label in patches]
            for label in FIELDS.values():patches.pop(label, None)
        else:
            fields = [name for name in FIELDS if desired[name] != before[name]]
            for name in fields:patches[FIELDS[name]] = desired[name]
        if not fields and not pending(item):return
        item['manual_values'] = patches
        item.setdefault('manual_schedule_original', extracted(item))
        item['manual_schedule_checked_source'] = extracted(item)
        item.setdefault('manual_schedule_history', []).append({
            'at':now().isoformat(), 'kind':value['op'], 'before':before,
            'after':desired, 'fields':fields})
        persist(db, item)
        db.execute('UPDATE local_overrides SET version=version+1 WHERE mail_id=?', (key,))
        sync(db, commit=False)
