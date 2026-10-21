"""Local per-call audit files. No model requests and no credential logging."""
import datetime as dt
import json
from pathlib import Path
import re
import uuid


def stamp():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat()


def cli_header(stderr,field):
    if isinstance(stderr,bytes):
        stderr=stderr.decode('utf-8',errors='replace')
    lines=re.sub(r'\x1b\[[0-9;]*m','',stderr or '').splitlines()
    if not any('OpenAI Codex' in line for line in lines[:5]):
        return None
    # Only the CLI session header is evidence; never parse echoed mail text.
    for line in lines[:50]:
        if line.strip() in ('user','user:'):
            break
        match=re.match(r'^\s*'+re.escape(field)+r':\s*(\S+)\s*$',line)
        if match:
            return match[1]
    return None


def cli_model(stderr):
    return cli_header(stderr,'model')


class AuditTrail:
    def __init__(self,root):
        self.root=Path(root)

    def path(self,key):
        if not re.fullmatch(r'[0-9]{8}T[0-9]{12}-[0-9a-f]{12}',key):
            raise ValueError('审计记录ID无效')
        return self.root/key

    def save(self,key,value):
        target=self.path(key)/'manifest.json'
        temporary=target.with_suffix('.new')
        temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(target)

    def read(self,key):
        return json.loads((self.path(key)/'manifest.json').read_text(encoding='utf-8'))

    def begin(self,packet,prompt,schema,requested_effort=None):
        key=dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime('%Y%m%dT%H%M%S%f')+'-'+uuid.uuid4().hex[:12]
        folder=self.path(key)
        folder.mkdir(parents=True,exist_ok=False)
        (folder/'input.json').write_text(json.dumps({'packet':packet,'prompt':prompt,'output_schema':schema},ensure_ascii=False,indent=2),encoding='utf-8')
        self.save(key,{'audit_id':key,'started_at':stamp(),'mail_ids':[m['id'] for m in packet['new_mail']],
                       'model_status':'prepared','actual_model':None,'model_evidence':None,
                       'requested_reasoning_effort':requested_effort,'reported_reasoning_effort':None,
                       'ingest_status':'not_started','sync_status':'not_started','sync_attempts':[]})
        return key

    def model_result(self,key,status,stderr='',output=None,returncode=None,error_type=None,
                     provider='codex',reported_model=None):
        item=self.read(key)
        model=cli_model(stderr) if provider=='codex' else (reported_model if reported_model and reported_model.lower()!='auto' else None)
        item.update(model_status=status,finished_at=stamp(),actual_model=model,
                    reported_reasoning_effort=cli_header(stderr,'reasoning effort'),
                    model_evidence=({'codex':'Codex CLI session header','qoder_cn':'Qoder CN CLI modelUsage','codebuddy_cn':'CodeBuddy CN CLI assistant providerData.model'}.get(provider)) if model else 'CLI未报告具体型号，不能确认',
                    returncode=returncode,error_type=error_type)
        item.update(provider=provider,reported_model=reported_model if provider!='codex' else model)
        if output is not None:
            (self.path(key)/'output.txt').write_text(output,encoding='utf-8')
        self.save(key,item)

    def review_attempt(self,key,requested_model,returncode,error_type=None,phase='review'):
        item=self.read(key)
        item.setdefault('model_attempts',[]).append({'at':stamp(),'requested_model':requested_model,
                                                   'returncode':returncode,'error_type':error_type,'phase':phase})
        self.save(key,item)

    def correction_begin(self,key,prompt,original_output,error,requested_model):
        folder=self.path(key)
        (folder/'initial-output.txt').write_text(original_output,encoding='utf-8')
        (folder/'correction-input.json').write_text(json.dumps({'prompt':prompt,'validation_error':error,
             'requested_model':requested_model},ensure_ascii=False,indent=2),encoding='utf-8')
        item=self.read(key)
        item['correction']={'status':'prepared','started_at':stamp(),'validation_error':error,
                            'requested_model':requested_model,'original_actual_model':item.get('actual_model')}
        self.save(key,item)

    def correction_result(self,key,status,output=None,reported_model=None,error=None,error_type=None,returncode=None):
        item=self.read(key)
        item['correction'].update(status=status,updated_at=stamp(),actual_model=reported_model,
                                  error=error,error_type=error_type,returncode=returncode)
        if output is not None:
            (self.path(key)/'correction-output.txt').write_text(output,encoding='utf-8')
        self.save(key,item)

    def ingest_result(self,key,status,error_type=None):
        item=self.read(key)
        item.update(ingest_status=status,ingested_at=stamp(),ingest_error_type=error_type)
        if status=='ingested':
            item['sync_status']='pending'
        self.save(key,item)

    def sync_result(self,keys,status,result=None,items=(),error_type=None,stage='sync'):
        by_id={item['id']:item for item in items}
        for key in keys:
            entry=self.read(key)
            # Only IDs committed atomically with ingest reach the pending queue.
            entry['ingest_status']='ingested'
            outcomes=[]
            for mail_id in entry['mail_ids']:
                item=by_id.get(mail_id)
                requires=bool(item and item.get('requires_action',False))
                outcome=('not_recorded' if item is None else 'no_action') if not requires else (
                    'verified' if status=='verified' and not item.get('needs_sync') else status)
                outcomes.append({'mail_id':mail_id,
                                 'requires_action':requires,'sync_status':outcome,
                                 'storage':'local_sqlite',
                                 'needs_sync':item.get('needs_sync') if item else None})
            entry['sync_status']=status
            entry['sync_attempts'].append({'at':stamp(),'status':status,'stage':stage,
                                           'result':result,'error_type':error_type,'mail_results':outcomes})
            self.save(key,entry)


class AuditedDecisions(list):
    def __init__(self,decisions,audit_id,model_calls=1):
        super().__init__([{**item,'review_audit_id':audit_id} for item in decisions])
        self.audit_id=audit_id
        self.model_calls=model_calls
