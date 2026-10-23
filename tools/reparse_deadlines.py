"""Explicit model reparse of one stored mail, without scanning or new credentials."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--id',required=True);parser.add_argument('--audit-id')
    args=parser.parse_args();key=args.id
    from backend.cli.manager import locked
    from backend.storage.vault import account_home,HOME,crypt
    from backend.storage import repository as repo
    from backend.mail.core import safe_text,ingest,now,get_meta,set_meta
    from backend.mail.deadlines import enrich
    from backend.jobs import local_gate as gate
    from backend.models.review_audit import AuditTrail
    with locked():
        home=account_home();db=repo.connect();gate.RUNTIME=home/'local-gate'
        try:
            row=db.execute('SELECT payload FROM mails WHERE id=?',(key,)).fetchone()
            if not row:raise ValueError('邮件不存在')
            mail=json.loads(crypt(row['payload'],decrypt=True))
            packet_mail={'id':key,**{k:safe_text(str(mail[k])) for k in ('subject','sender','received','body')},
                         'attachments':[safe_text(x) for x in mail['attachments']]}
            before=repo.state(db)
            folder=home/'backups'/'model-deadline-reparse';folder.mkdir(parents=True,exist_ok=True)
            stamp=now().strftime('%Y%m%dT%H%M%S%f');backup=sqlite3.connect(folder/(stamp+'.sqlite3'))
            try:db.backup(backup)
            finally:backup.close()
            config=json.loads((HOME/'local-gate.json').read_text(encoding='utf-8'))
            packet=gate.review_packet(db,[packet_mail])
            packet['related_existing_tasks']=[x for x in packet['related_existing_tasks'] if x['id']!=key]
            trail=AuditTrail(gate.RUNTIME/'audits')
            if args.audit_id:
                from backend.models.codebuddy_review import normalize
                from backend.models.review_audit import AuditedDecisions
                folder_audit=trail.path(args.audit_id)
                prior=trail.read(args.audit_id)
                saved_packet=json.loads((folder_audit/'input.json').read_text(encoding='utf-8'))['packet']
                if prior.get('mail_ids')!=[key] or prior.get('provider')!='codebuddy_cn':
                    raise ValueError('审计不属于这封邮件或模型入口')
                final=folder_audit/('correction-output.txt' if (folder_audit/'correction-output.txt').exists() else 'output.txt')
                output=normalize(final.read_text(encoding='utf-8'))
                checked=gate.validate_output(output,saved_packet,True)
                # Reuse the real model output after repairing a local validator;
                # preserve prior failure evidence rather than making another paid call.
                prior['validation_recovery']={'at':now().isoformat(),'reason':'local_schema_validator_integer_support',
                    'previous_model_status':prior.get('model_status'),'previous_correction':prior.get('correction'),
                    'new_model_call':False}
                trail.save(args.audit_id,prior)
                trail.model_result(args.audit_id,'validated',output=output,returncode=0,
                    provider='codebuddy_cn',reported_model=prior.get('actual_model'))
                decisions=AuditedDecisions(checked,args.audit_id,model_calls=len(prior.get('model_attempts',[])))
                packet_mail=saved_packet['new_mail'][0]
            else:
                decisions=gate.call_model(packet,config)
            if len(decisions)!=1 or not decisions[0].get('deadline_rules'):
                raise ValueError('模型未提供可核验的结构化期限规则，未覆盖现有数据')
            result=enrich(decisions[0],packet_mail,config.get('business_calendar'))
            try:ingest(db,[result],audit_id=decisions.audit_id)
            except Exception as error:
                trail.ingest_result(decisions.audit_id,'failed',type(error).__name__);raise
            trail.ingest_result(decisions.audit_id,'ingested')
            synced=repo.sync_local(db)
            waiting=get_meta(db,'pending_review_audits',[])
            trail.sync_result(waiting,'verified',result=synced,items=gate.records(db))
            set_meta(db,'pending_review_audits',[])
            after=repo.state(db);byid={m['id']:m for m in after['emails']}
            assert all(byid[m['id']]['notes']==m['notes'] and byid[m['id']]['done']==m['done'] for m in before['emails'])
            assert before['manual_events']==after['manual_events']
            oldsteps={s['id']:s['done'] for s in before['steps']}
            assert all(s['id'] not in oldsteps or oldsteps[s['id']]==s['done'] for s in after['steps'])
            steps=[s for s in after['steps'] if s['mail_id']==key and s['active']]
            report={'audit_id':decisions.audit_id,'model_rules':result['deadline_rules'],
                    'steps':[{k:s.get(k) for k in ('label','at','estimated','date_only','basis')} for s in steps],
                    'manual_state_preserved':True,'model_calls':decisions.model_calls}
            (folder/(stamp+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(report,ensure_ascii=False),flush=True)
        finally:db.close()
    return 0


if __name__=='__main__':sys.exit(main())
