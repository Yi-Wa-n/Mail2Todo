"""Account selection under the project's shared scan/write lock."""
import hashlib
import json
import os
import re
from backend.storage import vault, repository
from backend.mail.core import open_db, connect_mail

def listing():
    result = []
    homes = [('legacy', vault.HOME)]
    directory = vault.HOME/'accounts'
    if directory.exists():
        homes += [(p.name,p) for p in directory.iterdir() if p.is_dir() and re.fullmatch(r'[0-9a-f]{64}',p.name)]
    for key,home in homes:
        if not (home/'credentials.dpapi').exists(): continue
        try: address = vault.load_credentials(home)['email']
        except (RuntimeError,ValueError,KeyError):
            result.append({'id':key,'email':'凭证不可读取'})
            continue
        result.append({'id':key,'email':address})
    return {'active':vault.account_id(),'accounts':result}

def update(value):
    if not isinstance(value,dict) or set(value)-{'op','email','code','account'}:
        raise ValueError('无效邮箱设置')
    if value.get('account') != vault.account_id():
        raise ValueError('邮箱已切换，请刷新后重试')
    op=value.get('op');code=value.get('code','')
    if not isinstance(code,str) or len(code)>1024: raise ValueError('无效授权码')
    if op=='credentials':
        if not code.strip(): raise ValueError('请输入新授权码')
        home=vault.account_home();address=vault.load_credentials(home)['email']
    elif op=='switch':
        address=value.get('email','')
        if not isinstance(address,str):raise ValueError('无效邮箱')
        address=address.strip().lower()
        if not re.fullmatch(r'[^\s@]+@(163\.com|126\.com|yeah\.net)',address):
            raise ValueError('仅支持 163.com、126.com 和 yeah.net 网易邮箱')
        existing=next((item for item in listing()['accounts'] if item['email'].lower()==address),None)
        key=existing['id'] if existing else hashlib.sha256(address.encode()).hexdigest()
        home=vault.account_home(key)
        if not code and existing:code=vault.load_credentials(home)['mail_code']
        if not code.strip():raise ValueError('新邮箱需要客户端授权码')
    else: raise ValueError('无效邮箱操作')
    # Login only; never selects a folder, scans mail or invokes a model.
    try: client=connect_mail({'email':address,'mail_code':code})
    except (RuntimeError,OSError):
        raise ValueError('邮箱验证失败，请检查 IMAP、客户端授权码或网络；原邮箱保持不变') from None
    try: client.logout()
    except Exception: pass
    if not (home/'ledger.sqlite3').exists():
        home.mkdir(parents=True,exist_ok=True)
        db=open_db(home/'ledger.sqlite3')
        try: repository.initialize(db)
        finally: db.close()
    vault.save_credentials({'email':address,'mail_code':code},home)
    if op=='switch':
        path=vault.HOME/'active-account.json';temp=path.with_suffix('.new')
        temp.write_text(json.dumps({'id':key}),encoding='utf-8');os.replace(temp,path)
    return listing()
