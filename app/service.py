"""Application service for an isolated local repository."""
import json
from pathlib import Path
import re
import subprocess
import sys
import threading

from backend.cli.manager import locked
from backend.storage import repository as repo
from backend.storage.vault import HOME
from backend.storage.vault import account_home,account_id

ROOT=Path(__file__).resolve().parents[1]


class Service:
    def __init__(self):
        self.guard=threading.RLock()
        self.changed=threading.Condition()
        self.job={'running':False,'message':''}
        self.job_version=0
        with locked():
            db=repo.connect()
            try:repo.initialize(db)
            finally:db.close()

    def use(self,fn):
        with self.guard:
            db=repo.connect()
            try:return fn(db)
            finally:db.close()

    def state(self):
        with self.guard:
            value=self.use(repo.state)
            value['job']=dict(self.job)
            value['account']=account_id()
            value['version']=value['account']+':'+str(value['revision'])+':'+str(self.job_version)
        from backend.jobs.scheduler import status
        value['schedule']=status()
        return value

    def revision(self):return self.use(repo.revision)

    def version(self):
        with self.guard:return account_id()+':'+str(self.revision())+':'+str(self.job_version)

    def check_account(self,value):
        if value.get('account') != account_id():raise ValueError('邮箱已切换，请刷新后再修改')

    def mailbox(self,value):
        from backend.storage.accounts import update
        with self.guard,locked():
            if self.job['running']:raise ValueError('扫描中，请稍后切换或更新授权码')
            result=update(value)
            self.job_version+=1
        with self.changed:self.changed.notify_all()
        return result

    def is_busy(self):return self.job['running']

    def edit(self,kind,value):
        with self.guard,locked():
            self.check_account(value)
            self.use(lambda db:repo.mutate(db,kind,value))
            result=self.state()
        with self.changed:self.changed.notify_all()
        return result

    def preview(self,key):return self.use(lambda db:repo.mail_preview(db,key))

    def manual(self,value):
        with self.guard,locked():
            self.check_account(value)
            self.use(lambda db:repo.edit_manual(db,value))
            result=self.state()
        with self.changed:self.changed.notify_all()
        return result

    def filtered(self,offset):
        from app.filtered import listing
        return self.use(lambda db:listing(db,offset))

    def filtered_detail(self,key):
        from app.filtered import detail
        return self.use(lambda db:detail(db,key))

    def audits(self):
        result=[]
        for path in sorted((account_home()/'local-gate/audits').glob('*/manifest.json'),reverse=True)[:200]:
            try:
                item=json.loads(path.read_text(encoding='utf-8'))
                result.append({k:item.get(k) for k in ('audit_id','started_at','actual_model','model_status','sync_status','provider','correction')})
            except (ValueError,OSError):continue
        return result

    def audit(self,key):
        if not re.fullmatch(r'[0-9]{8}T[0-9]{12}-[0-9a-f]{12}',key):raise ValueError('无效审计记录')
        folder=account_home()/'local-gate/audits'/key
        def read(name):
            path=folder/name
            return path.read_text(encoding='utf-8') if path.exists() else ''
        return {name:read(name) for name in ['manifest.json','input.json','output.txt','initial-output.txt','correction-input.json','correction-output.txt']}

    def settings(self):
        from backend.jobs.scheduler import status
        schedule=status()
        config=json.loads((HOME/'local-gate.json').read_text(encoding='utf-8'))
        from backend.storage.accounts import listing
        return {'mailbox':listing(),'model':config.get('codebuddy_model'),'fallback':config.get('codebuddy_fallback_model'),
                'effort':config.get('reasoning_effort','low'),'credentials_saved':(account_home()/'credentials.dpapi').exists(),
                'automatic_enabled':schedule.get('enabled'),'schedule':schedule}

    def save_settings(self,value):
        if set(value)!={'model','fallback','effort'}:raise ValueError('无效设置字段')
        for name in ('model','fallback'):
            if not isinstance(value[name],str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,79}',value[name]):raise ValueError('无效模型名称')
        if value['effort'] not in ('low','medium','high'):raise ValueError('无效思考强度')
        with self.guard,locked():
            if self.job['running']:raise ValueError('扫描中，请稍后保存')
            path=HOME/'local-gate.json'
            config=json.loads(path.read_text(encoding='utf-8'))
            config.update(codebuddy_model=value['model'],codebuddy_fallback_model=value['fallback'],reasoning_effort=value['effort'],notifications_enabled=False)
            temp=path.with_suffix('.new');temp.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)
        return self.settings()

    def save_schedule(self,value):
        from backend.jobs.scheduler import update
        with self.guard:
            if self.job['running']:raise ValueError('正在扫描，请结束后再保存')
            result=update(value)
            self.job_version+=1
        with self.changed:self.changed.notify_all()
        return result

    def scan(self):
        with self.guard:
            if self.job['running']:return False
            self.job={'running':True,'message':'扫描中…'}
            self.job_version+=1
        threading.Thread(target=self._scan,daemon=True).start()
        return True

    def _scan(self):
        python=Path(sys.executable).with_name('python.exe')
        try:
            run=subprocess.run([str(python),'-X','utf8','-m','backend.jobs.manual_scan'],cwd=ROOT,
                               capture_output=True,encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.job={'running':False,'message':'扫描完成' if run.returncode==0 else '扫描未完成，候选保留待重试'}
        except OSError:self.job={'running':False,'message':'无法启动扫描程序'}
        self.job_version+=1
        with self.changed:self.changed.notify_all()
