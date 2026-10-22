"""Persistent mail ledger. Remote mail is untrusted data, never executable input."""
import datetime as dt
import email
from email import policy
from email.utils import parsedate_to_datetime
import hashlib
import html
from html.parser import HTMLParser
import imaplib
import json
from pathlib import Path
import re
import sqlite3
import ssl
import time
from backend.storage.vault import HOME, crypt, account_home

TZ = dt.timezone(dt.timedelta(hours=8))
CATEGORIES = ['投递回执', '测评', '笔试', 'AI面试', '面试', '补充材料', '录用', '未通过', '其他招聘通知']
RECRUIT = re.compile(r'校园招聘|校招|秋招|春招|应聘|投递|简历|笔试|面试|录用|测评|招聘|offer|assessment|interview|application\s+(?:received|status)', re.I)
IMPORTANT = {'测评', '笔试', 'AI面试', '面试', '补充材料', '录用'}
STATUS = {'待处理', '已处理', '待核实'}


def now():
    return dt.datetime.now(TZ)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


class TextHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'head'):
            self.hidden += 1
        if tag in ('br', 'p', 'div', 'tr'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'head'):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def parse_mail(raw, received=None):
    message = email.message_from_bytes(raw, policy=policy.default)
    part = message.get_body(preferencelist=('plain', 'html')) if message.is_multipart() else message
    body = ''
    if part and part.get_content_type() in ('text/plain', 'text/html'):
        try:
            body = part.get_content()
        except (LookupError, UnicodeError):
            body = (part.get_payload(decode=True) or b'').decode('utf-8', errors='replace')
        if part.get_content_type() == 'text/html':
            parser = TextHTML()
            parser.feed(body)
            body = html.unescape(''.join(parser.parts))
    # Some ATS mail has a placeholder plain part ("新面试") and the actual
    # invitation only in HTML. Prefer the meaningful alternative in that case.
    if message.is_multipart() and len(body.strip()) < 80:
        richer = message.get_body(preferencelist=('html',))
        if richer:
            try:
                parser = TextHTML()
                parser.feed(richer.get_content())
                candidate = html.unescape(''.join(parser.parts)).strip()
                if len(candidate) > len(body.strip()) + 80:
                    body = candidate
            except (LookupError, UnicodeError):
                pass
    try:
        timestamp = parsedate_to_datetime(str(message.get('Date', '')))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=TZ)
    except (ValueError, TypeError):
        timestamp = received or now()
    attachments = [str(p.get_filename()) for p in message.walk() if p.get_filename()]
    return {'message_id': str(message.get('Message-ID', '')).strip(), 'subject': str(message.get('Subject', '')), 'sender': str(message.get('From', '')), 'sent_date_header': str(message.get('Date', '')), 'received': (received or timestamp).astimezone(TZ).isoformat(), 'body': body.strip(), 'attachments': attachments}


def safe_text(text):
    """Limit prompt/log exposure; exact bodies remain DPAPI encrypted locally."""
    text = re.sub(r'https?://[^\s<>]+', '[链接保留在原邮件]', text, flags=re.I)
    text = re.sub(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', '[邮箱已隐藏]', text)
    text = re.sub(r'(?<!\d)1[3-9]\d{9}(?!\d)', '[电话已隐藏]', text)
    text = re.sub(r'(?<!\d)\d{17}[\dXx](?!\d)', '[证件号已隐藏]', text)
    text = re.sub(r'((?:验证码|密码|口令|授权码|通行证|会议号|token)\s*[:：=]?\s*)\S+', r'\1[已隐藏]', text, flags=re.I)
    return text[:18000]


def open_db(path=None):
    path = Path(path) if path else account_home() / 'ledger.sqlite3'
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.executescript('''
      PRAGMA journal_mode=WAL;
      CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS mails(id TEXT PRIMARY KEY, payload BLOB NOT NULL, candidate INTEGER NOT NULL, reviewed INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE IF NOT EXISTS sources(mail_id TEXT,folder TEXT,validity TEXT,uid TEXT,starred INTEGER DEFAULT 0, PRIMARY KEY(folder,validity,uid));
      CREATE TABLE IF NOT EXISTS records(mail_id TEXT PRIMARY KEY,payload TEXT NOT NULL,synced_hash TEXT);
      CREATE TABLE IF NOT EXISTS delivered(id TEXT PRIMARY KEY,at TEXT NOT NULL);
    ''')
    return db


def get_meta(db, key, default=None):
    row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    return json.loads(row['value']) if row else default


def set_meta(db, key, value):
    db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, canonical(value)))
    db.commit()


def store_message(db, raw, folder, validity, uid, encrypt=crypt, received=None):
    mail = parse_mail(raw, received)
    # Message-ID alone is insufficient: distinct revisions can reuse an ID.
    key = digest(canonical([mail['message_id'], mail['subject'], mail['sender'], mail['sent_date_header'], mail['body'], mail['attachments']]))
    candidate = bool(RECRUIT.search(mail['subject'] + '\n' + mail['body']))
    added = db.execute('INSERT OR IGNORE INTO mails(id,payload,candidate) VALUES (?,?,?)', (key, encrypt(canonical(mail).encode()), int(candidate))).rowcount
    db.execute('INSERT OR IGNORE INTO sources(mail_id,folder,validity,uid) VALUES (?,?,?,?)', (key, folder, validity, str(uid)))
    db.commit()
    return bool(added), candidate


def connect_mail(credentials):
    address = credentials['email']
    domain = address.rsplit('@', 1)[-1].lower()
    if domain not in ('163.com', '126.com', 'yeah.net'):
        raise RuntimeError('只允许连接已选定的网易邮箱服务')
    client = imaplib.IMAP4_SSL('imap.' + domain, 993, ssl_context=ssl.create_default_context(), timeout=35)
    try:
        client.login(address, credentials['mail_code'])
        if any((x.decode() if isinstance(x, bytes) else x).upper() == 'ID' for x in client.capabilities):
            imaplib.Commands.setdefault('ID', ('AUTH', 'SELECTED'))
            client._simple_command('ID', '("name" "AutumnRecruitMail" "version" "1.0" "vendor" "Personal")')
        return client
    except Exception:
        try:
            client.logout()
        except Exception:
            pass
        raise RuntimeError('网易邮箱登录失败；请检查 IMAP 服务、授权码或安全验证') from None


def folders(client):
    status, items = client.list()
    if status != 'OK':
        raise RuntimeError('无法读取邮箱文件夹，本次不推进检查时间')
    result = []
    for item in items or []:
        if not isinstance(item, bytes):
            raise RuntimeError('邮箱文件夹格式暂不支持，需核实后继续')
        match = re.match(rb'\(([^)]*)\)\s+(?:"[^"]*"|NIL)\s+(.+)', item)
        if not match:
            raise RuntimeError('无法解析邮箱文件夹，本次不推进检查时间')
        flags, mailbox = match.groups()
        if any(flag in flags.lower() for flag in (b'\\noselect', b'\\trash', b'\\junk', b'\\sent', b'\\drafts')):
            continue
        name = mailbox.decode('ascii')
        if name.startswith('"') and name.endswith('"'):
            name = re.sub(r'\\(["\\])', r'\1', name[1:-1])
        if name.lower() in ('sent', 'drafts', 'trash', 'junk', 'deleted messages', 'sent messages'):
            continue
        result.append(name)
    if 'INBOX' not in result:
        result.insert(0, 'INBOX')
    return result


def select_folder(client, folder, readonly=True):
    status, _ = client.select('"' + folder.replace('\\', '\\\\').replace('"', '\\"') + '"', readonly=readonly)
    if status != 'OK':
        raise RuntimeError('无法读取某个邮箱文件夹，本次不推进检查时间')
    _, validity = client.response('UIDVALIDITY')
    if not validity or not validity[0]:
        raise RuntimeError('邮箱未提供 UIDVALIDITY，无法安全定位邮件')
    return validity[0].decode()


def scan(db, credentials, client_factory=connect_mail, clock=now):
    account = digest(credentials['email'].lower())
    previous_account = get_meta(db, 'account')
    if previous_account and previous_account != account:
        raise RuntimeError('本台账已绑定另一邮箱；更换账户需建立独立台账')
    start = clock()
    previous_scan = get_meta(db, 'last_scan')
    since = dt.datetime.fromisoformat(previous_scan or (start - dt.timedelta(days=30)).isoformat())
    # Overlap handles IMAP day resolution, timezone boundaries and arrival delay.
    date = (since - dt.timedelta(days=2)).strftime('%d-%b-%Y')
    client = client_factory(credentials)
    added = candidates = 0
    try:
        for folder in folders(client):
            validity = select_folder(client, folder)
            status, data = client.uid('SEARCH', None, 'SINCE', date)
            if status != 'OK':
                raise RuntimeError('邮箱搜索失败，本次不推进检查时间')
            for uid in (data[0] or b'').split():
                uid = uid.decode()
                if db.execute('SELECT 1 FROM sources WHERE folder=? AND validity=? AND uid=?', (folder, validity, uid)).fetchone():
                    continue
                status, parts = client.uid('FETCH', uid, '(BODY.PEEK[] INTERNALDATE)')
                if status != 'OK':
                    raise RuntimeError('读取邮件失败，本次不推进检查时间')
                tuples = [part for part in parts if isinstance(part, tuple)]
                if len(tuples) != 1:
                    raise RuntimeError('邮件读取不完整，本次不推进检查时间')
                raw = tuples[0][1]
                internal = imaplib.Internaldate2tuple(tuples[0][0])
                arrival = dt.datetime.fromtimestamp(time.mktime(internal), TZ) if internal else start
                cutoff = since - dt.timedelta(days=2) if previous_scan else since
                if arrival < cutoff:
                    continue
                fresh, candidate = store_message(db, raw, folder, validity, uid, received=arrival)
                added += int(fresh)
                candidates += int(fresh and candidate)
        set_meta(db, 'account', account)
        set_meta(db, 'last_scan', start.isoformat())
    finally:
        try:
            client.logout()
        except Exception:
            pass
    return {'new_messages': added, 'recruitment_candidates': candidates}


def pending(db, limit=30, decrypt=lambda x: crypt(x, decrypt=True)):
    result = []
    for row in db.execute('SELECT id,payload FROM mails WHERE candidate=1 AND reviewed=0 ORDER BY rowid LIMIT ?', (limit,)):
        mail = json.loads(decrypt(row['payload']))
        result.append({'id': row['id'], **{k: safe_text(str(mail[k])) for k in ('subject', 'sender', 'received', 'body')}, 'attachments': [safe_text(x) for x in mail['attachments']]})
    return result


def ingest(db, decisions, audit_id=None):
    # Validate the entire batch before making any change.
    for decision in decisions:
        if not db.execute('SELECT 1 FROM mails WHERE id=?', (decision.get('id'),)).fetchone():
            raise ValueError('记录不存在')
        if not isinstance(decision.get('relevant'), bool):
            raise ValueError('必须明确判断是否与招聘有关')
        if not decision['relevant']:
            continue
        for field in ('company', 'role', 'category', 'next_action', 'status', 'evidence'):
            if not isinstance(decision.get(field), str) or not decision[field].strip():
                raise ValueError('缺少必要字段：' + field)
        if decision['category'] not in CATEGORIES or decision['status'] not in STATUS:
            raise ValueError('分类或处理状态无效')
        if not isinstance(decision.get('important', False), bool):
            raise ValueError('重要待办必须明确判断，不能使用文字代替布尔值')
        for field in ('event_at', 'deadline'):
            value = decision.get(field)
            if value:
                parsed = dt.datetime.fromisoformat(value)
                if parsed.tzinfo is None:
                    raise ValueError('时间必须包含时区；不确定时应留空')
        for value in decision.get('event_options', []):
            parsed = dt.datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                raise ValueError('候选场次时间必须包含时区')
        for target in decision.get('resolves', []):
            if target == decision['id'] or not (db.execute('SELECT 1 FROM records WHERE mail_id=?', (target,)).fetchone() or any(x.get('id') == target and x.get('relevant') for x in decisions)):
                raise ValueError('被后续通知覆盖的事项不存在')
        if decision.get('supersedes'):
            old = db.execute('SELECT payload FROM records WHERE mail_id=?', (decision['supersedes'],)).fetchone()
            if not old:
                raise ValueError('改期必须引用已有记录')
            old = json.loads(old['payload'])
            if (decision['company'], decision['role']) != (old['company'], old['role']) or '待核实' in (decision['company'], decision['role']):
                raise ValueError('公司和岗位未确认，不能自动合并改期事项')
    with db:
        for decision in decisions:
            key = decision['id']
            if decision['relevant']:
                item = dict(decision)
                row = db.execute('SELECT payload,synced_hash FROM records WHERE mail_id=?', (key,)).fetchone()
                previous = json.loads(row['payload']) if row else {}
                for field in ('manual_status', 'notes', 'manual_company', 'manual_role', 'manual_direct', 'manual_columns', 'manual_values', 'manual_completed', 'completion_previous_status', 'remote_record_id', 'manual_schedule_original', 'manual_schedule_source', 'manual_schedule_history', 'manual_schedule_checked_source'):
                    if field in previous:
                        item[field] = previous[field]
                db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)', (key, canonical(item), row['synced_hash'] if row else None))
            db.execute('UPDATE mails SET reviewed=1 WHERE id=?', (key,))
        if audit_id:
            waiting=get_meta(db,'pending_review_audits',[])
            if audit_id not in waiting:
                set_meta(db,'pending_review_audits',waiting+[audit_id])


def records(db, decrypt=lambda x: crypt(x, decrypt=True)):
    result = []
    for row in db.execute('SELECT r.*,m.payload AS mail FROM records r JOIN mails m ON r.mail_id=m.id'):
        item = json.loads(row['payload'])
        mail = json.loads(decrypt(row['mail']))
        item.update({'subject': mail['subject'], 'received': mail['received'], 'message_id': mail['message_id']})
        item['sources'] = [dict(x) for x in db.execute('SELECT folder,validity,uid,starred FROM sources WHERE mail_id=?', (row['mail_id'],))]
        item['needs_sync'] = row['synced_hash'] != digest(row['payload'])
        result.append(item)
    return result


def reminders(db, items, at=None):
    at = at or now()
    superseded = inactive_ids(items)
    result = []
    for item in items:
        if item['id'] in superseded or item.get('manual_status', item['status']) == '已处理':
            continue
        event = item.get('event_at')
        deadline = item.get('deadline')
        occurrence = deadline or event
        received = dt.datetime.fromisoformat(item['received'])
        is_live = not occurrence or dt.datetime.fromisoformat(occurrence) > at
        if item.get('important', False) and item['category'] in {'AI面试', '面试', '补充材料', '录用'} and is_live and at - received < dt.timedelta(days=30):
            result.append({'id': item['id'] + ':new', 'kind': '新通知', 'item': item})
        # Remind for each separately specified event and deadline; collapse identical times.
        timestamps = {v for v in (deadline, event) if v}
        optional = set(item.get('event_options', [])) if not event else set()
        timestamps.update(optional)
        for timestamp in sorted(timestamps):
            remaining = dt.datetime.fromisoformat(timestamp) - at
            hours = remaining.total_seconds() / 3600
            if 0 < hours <= 24:
                level = '2h' if hours <= 2 else '24h'
                # A 2h reminder obsoletes the missed 24h reminder; never deliver both.
                kind = '2小时内到期' if level == '2h' else '24小时内到期'
                if timestamp in optional:
                    kind += '（可选场次，尚未确认参加）'
                result.append({'id': item['id'] + ':' + timestamp + ':' + level, 'kind': kind, 'at': timestamp, 'item': item})
    return [entry for entry in result if not db.execute('SELECT 1 FROM delivered WHERE id=?', (entry['id'],)).fetchone()]


def ack(db, ids):
    with db:
        for key in ids:
            db.execute('INSERT OR IGNORE INTO delivered VALUES (?,?)', (key, now().isoformat()))


def progress(items):
    groups = {}
    for item in sorted(action_items(items), key=lambda x: x['received']):
        if not item.get('application_related', True):
            continue
        company = item.get('manual_company') or item['company']
        role = item.get('manual_role') or item['role']
        # Unknown positions must not collapse unrelated applications.
        key = (company, role, item['id'] if '待核实' in (company, role) else '')
        groups.setdefault(key, []).append(item)
    result = []
    for key, group in groups.items():
        # Internal outcome/withdrawal notices may update an existing application
        # row, but never get their own user-facing row.
        related = [x for x in items if
                   ((x.get('manual_company') or x['company'], x.get('manual_role') or x['role']) == key[:2]) and
                   ('待核实' not in (key[0], key[1]) or x['id'] == key[2])]
        superseded = inactive_ids(items)
        active = [x for x in group if x['id'] not in superseded and x.get('manual_status', x['status']) != '已处理']
        latest = group[-1]
        outcomes = [x for x in related if x['id'] in superseded and x['category'] in ('未通过', '录用')]
        latest_outcome = outcomes[-1] if outcomes else None
        stage = latest_outcome['category'] if latest_outcome else latest['category']
        row = {'id': digest(canonical(key)), 'company': key[0], 'role': key[1], 'stage': stage, 'received': latest['received'], 'latest_subject': latest_outcome['subject'] if latest_outcome else latest['subject'], 'pending_actions': '\n'.join(x['next_action'] for x in active if x['status'] != '已处理'), 'mail_ids': [x['id'] for x in group]}
        milestones = {'assessment': [], 'written': [], 'ai': [], 'round1': [], 'round2': [], 'other_interview': []}
        for item in related:
            label = milestone_for(item)
            if not label:
                continue
            bucket, text = label
            if latest_outcome and item['id'] in superseded:
                text += ' · ' + ('未通过（邮件结果）' if latest_outcome['category'] == '未通过' else '录用通知已到')
            elif item.get('manual_status') == '已处理' or item['status'] == '已处理':
                text += ' · 已处理'
            elif item['id'] in superseded:
                text += ' · 已由后续通知覆盖'
            elif item.get('deadline') and dt.datetime.fromisoformat(item['deadline']) < now():
                text += ' · 已过期，结果待核实'
            elif item.get('event_at') and dt.datetime.fromisoformat(item['event_at']) < now():
                text += ' · 时间已过，是否参加待核实'
            elif item['status'] == '待处理':
                text += ' · 待处理'
            else:
                text += ' · 状态待核实'
            milestones[bucket].append((item['received'], text))
        for bucket, values in milestones.items():
            row[bucket] = '\n'.join(text for _, text in sorted(values))
        result.append(row)
    return result


def milestone_for(item):
    if item['category'] not in ('测评', '笔试', 'AI面试', '面试'):
        return None
    text = (item['subject'] + '\n' + item.get('evidence', '')).lower()
    when = item.get('event_at') or item.get('deadline')
    stamp = dt.datetime.fromisoformat(when).strftime('%m-%d %H:%M') if when else '时间待核实'
    if item['category'] in ('测评', '笔试'):
        return ('written' if item['category'] == '笔试' else 'assessment'), stamp
    if item['category'] == 'AI面试' or re.search(r'ai\s*面试|ai初试|智能面试', text, re.I):
        return 'ai', stamp
    if re.search(r'第\s*2\s*轮|第2轮|第二轮|二面|终面|second round', text):
        return 'round2', stamp
    if re.search(r'第\s*1\s*轮|第1轮|第一轮|一面|初试|first round', text):
        return 'round1', stamp
    return 'other_interview', stamp


def requires_action(item):
    return item.get('requires_action', item['category'] in IMPORTANT)


def action_items(items):
    inactive = inactive_ids(items)
    result = []
    for value in items:
        if not requires_action(value):
            continue
        item = dict(value)
        if item['id'] in inactive and not item.get('manual_status'):
            item['status'] = '已处理'
            item['next_action'] = '后续通知已覆盖该待办，旧事项停止提醒；不表示本人已参加。原记录：' + item['next_action']
        result.append(item)
    return result


def inactive_ids(items):
    result = {x['supersedes'] for x in items if x.get('supersedes')}
    for item in items:
        result.update(item.get('resolves', []))
    return result


def star_important(db, credentials, items, client_factory=connect_mail):
    client = client_factory(credentials)
    count = 0
    verified = 0
    try:
        for item in items:
            if item['id'] in inactive_ids(items):
                continue
            if item['category'] not in IMPORTANT or item.get('manual_status', item['status']) == '已处理':
                continue
            # Only verified active actions should alter mailbox flags.
            if not item.get('important', False):
                continue
            for source in item['sources']:
                validity = select_folder(client, source['folder'], readonly=False)
                if validity != source['validity']:
                    raise RuntimeError('邮箱邮件编号已变化，需重新扫描后再加星')
                status, before = client.uid('FETCH', source['uid'], '(FLAGS)')
                if status != 'OK' or not before or not isinstance(before[0], bytes):
                    raise RuntimeError('无法确认邮件原有状态，未加星')
                seen_before = b'\\Seen' in imaplib.ParseFlags(before[0])
                changed = b'\\Flagged' not in imaplib.ParseFlags(before[0])
                if changed:
                    status, _ = client.uid('STORE', source['uid'], '+FLAGS.SILENT', '(\\Flagged)')
                    if status != 'OK':
                        raise RuntimeError('邮箱加星失败，保留待重试状态')
                status, after = client.uid('FETCH', source['uid'], '(FLAGS)')
                if status != 'OK' or not after or not isinstance(after[0], bytes):
                    raise RuntimeError('邮箱加星后未能核验，保留待重试状态')
                flags = imaplib.ParseFlags(after[0])
                if b'\\Flagged' not in flags or (b'\\Seen' in flags) != seen_before:
                    raise RuntimeError('邮箱星标或未读状态核验失败，需人工检查')
                db.execute('UPDATE sources SET starred=1 WHERE folder=? AND validity=? AND uid=?', (source['folder'], source['validity'], source['uid']))
                db.commit()
                count += int(changed)
                verified += 1
        if verified:
            set_meta(db, 'last_star_verified', now().isoformat())
    finally:
        try:
            client.logout()
        except Exception:
            pass
    return count
