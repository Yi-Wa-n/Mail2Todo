"""Read-only inspection of deterministic local-filter decisions."""
import json
import re

REASONS={'account_notice':'账户通知','report_or_feedback':'报告 / 反馈问卷',
         'receipt':'投递回执','promotion':'招聘推广'}


def read_mail(payload):
    from backend.storage.vault import crypt
    from backend.mail.core import safe_text
    mail=json.loads(crypt(payload,decrypt=True))
    return {'subject':safe_text(str(mail.get('subject',''))),
            'received':mail.get('received'),
            'body':safe_text(str(mail.get('body','')))}


def exists(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_filter_log'").fetchone() is not None


def listing(db,offset=0):
    if type(offset) is not int or not 0 <= offset <= 1_000_000:
        raise ValueError('无效页码')
    if not exists(db):return {'items':[],'total':0,'offset':offset,'has_more':False}
    total=db.execute('SELECT count(*) FROM local_filter_log').fetchone()[0]
    rows=db.execute('SELECT f.mail_id,f.reason,f.at,m.payload FROM local_filter_log f LEFT JOIN mails m ON m.id=f.mail_id ORDER BY f.at DESC,f.mail_id DESC LIMIT 51 OFFSET ?',(offset,)).fetchall()
    items=[]
    for row in rows[:50]:
        item={'id':row['mail_id'],'reason':REASONS.get(row['reason'],'其他规则'),
              'filtered_at':row['at'],'subject':'原邮件不可读取','received':None,'available':False}
        try:
            mail=read_mail(row['payload'])
            item.update(subject=mail['subject'],received=mail['received'],available=True)
        except (OSError,ValueError,TypeError,RuntimeError):
            pass
        items.append(item)
    return {'items':items,'total':total,'offset':offset,'has_more':len(rows)>50}


def detail(db,key):
    if not re.fullmatch(r'[0-9a-f]{64}',key):raise ValueError('无效邮件标识')
    if not exists(db):raise ValueError('没有该过滤记录')
    row=db.execute('SELECT f.reason,f.at,m.payload FROM local_filter_log f JOIN mails m ON m.id=f.mail_id WHERE f.mail_id=?',(key,)).fetchone()
    if row is None:raise ValueError('没有该过滤记录')
    mail=read_mail(row['payload'])
    return {**mail,'reason':REASONS.get(row['reason'],'其他规则'),'filtered_at':row['at']}
