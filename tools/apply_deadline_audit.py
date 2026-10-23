"""Apply complete, validated model deadline audits while preserving manual data."""
import argparse
import json
from pathlib import Path
import re
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--checkpoint',type=Path,required=True)
    args=parser.parse_args()
    from backend.cli.manager import locked
    from backend.storage.vault import HOME,account_home,account_id,crypt
    from backend.storage import repository as repo
    from backend.mail.core import canonical,digest,now,safe_text,ingest,get_meta,set_meta
    from backend.mail.deadlines import calculated_steps,validate_rules,enrich
    from backend.mail.deadline_supplements import supplement_key
    from backend.jobs import local_gate as gate
    from backend.models.review_audit import AuditTrail
    home=account_home();path=args.checkpoint.resolve()
    if not path.is_relative_to((home/'deadline-audit').resolve()):raise ValueError('不是当前台账的审查结果')
    audit=json.loads(path.read_text(encoding='utf-8'))
    if audit['account']!=account_id() or len(audit['decisions'])!=audit['total']:raise ValueError('审查尚未完整覆盖或邮箱已变化')
    config=json.loads((HOME/'local-gate.json').read_text(encoding='utf-8'))
    calendar=config.get('business_calendar') or {}
    gate.RUNTIME=home/'local-gate';trail=AuditTrail(gate.RUNTIME/'audits')
    with locked():
        db=repo.connect()
        try:
            before=repo.state(db);shown={m['id']:m for m in before['emails']}
            backup=sqlite3.connect(path.parent/'before-apply.sqlite3')
            try:db.backup(backup)
            finally:backup.close()
            missing=[d for d in audit['decisions'].values() if any(not b for b in d['already_recorded'])]
            classify=[d['id'] for d in missing if d['id'] not in shown]
            new_classified=0
            for offset in range(0,len(classify),5):
                batch=[]
                for key in classify[offset:offset+5]:
                    raw=json.loads(crypt(db.execute('SELECT payload FROM mails WHERE id=?',(key,)).fetchone()[0],decrypt=True))
                    batch.append({'id':key,**{k:safe_text(str(raw[k]),maximum=None) for k in ('subject','sender','received','body')},'attachments':[safe_text(x) for x in raw['attachments']]})
                # This is a new full classification only for mail that the audit
                # found actionable but was absent from the existing actionable UI.
                packet={'new_mail':batch,'now_beijing':now().isoformat(),'related_existing_tasks':[],
                        'product_scope':'本工具记录投递后的实质招聘操作。普通账户验证、账号激活、验证码和通用网申推广不纳入待办；不能因推广倒计时而替用户决定要投递。只有确实属于本人后续招聘操作才requires_action=true。'}
                rules_before=gate.REVIEW_RULES
                gate.REVIEW_RULES='遵循输入product_scope区分账户操作/通用推广与个人招聘后续事项。\n'+rules_before
                try:decisions=gate.call_model(packet,config)
                finally:gate.REVIEW_RULES=rules_before
                source={m['id']:m for m in batch}
                values=[enrich(d,source[d['id']],calendar) for d in decisions]
                ingest(db,values,audit_id=decisions.audit_id);trail.ingest_result(decisions.audit_id,'ingested')
                new_classified+=len(decisions)
            if classify:repo.sync_local(db)
            current=repo.state(db);shown={m['id']:m for m in current['emails']}
            changed=[];deferred=[]
            for d in missing:
                key=d['id']
                if key not in shown:
                    deferred.append({'id':key,'reason':'full_classification_no_action'});continue
                row=db.execute('SELECT payload FROM records WHERE mail_id=?',(key,)).fetchone()
                item=json.loads(row['payload'])
                mail=json.loads(crypt(db.execute('SELECT payload FROM mails WHERE id=?',(key,)).fetchone()[0],decrypt=True))
                rules=[{k:v for k,v in r.items()} for r,covered in zip(d['deadline_rules'],d['already_recorded']) if not covered]
                validate_rules(rules,safe_text(mail['body'],maximum=None))
                for r in rules:
                    if re.search(r'小时|hours?',r['source_text'],re.I) and r['unit']!='hours':raise ValueError('小时规则尚未完成单位复核')
                    if re.search(r'分钟|minutes?',r['source_text'],re.I) and r['unit']!='minutes':raise ValueError('分钟规则尚未完成单位复核')
                existing=list(item.get('additional_date_steps',[]))
                signatures={(r.get('kind'),r.get('anchor'),r.get('days'),r.get('unit'),re.sub(r'\s+','',r.get('source_text',''))) for r in existing+item.get('date_steps',[])}
                steps=calculated_steps(rules,mail['received'],calendar.get('holidays',()),calendar.get('workdays',()))
                added=[]
                for step in steps:
                    signature=(step['kind'],step['anchor'],step['days'],step['unit'],re.sub(r'\s+','',step['source_text']))
                    if signature in signatures:continue
                    step['supplement_id']=supplement_key(step);step['audit_id']=d['audit_id']
                    existing.append(step);added.append(step);signatures.add(signature)
                if not added:continue
                item['additional_date_steps']=existing
                item['additional_completion_snapshot']=shown[key]['done']
                item['additional_deadline_summary']='；'.join(r['action']+(('（推算日期 ' if r['date_only'] else '（截止/参考时间 ')+r['date']+'）' if r['date'] else '（起点尚未确认）') for r in existing)
                dated=next((r for r in existing if r.get('date')),None)
                if dated:
                    item['additional_deadline_display']=dated['date'];item['additional_deadline_estimated']=dated['estimated']
                with db:
                    encoded=canonical(item);db.execute('UPDATE records SET payload=?,synced_hash=? WHERE mail_id=?',(encoded,digest(encoded),key))
                changed.append({'id':key,'company':shown[key]['company'],'completed':shown[key]['done'],
                    'rules':[{k:r.get(k) for k in ('label','date','estimated','date_only','anchor','days','unit')} for r in added]})
            synced=repo.sync_local(db)
            waiting=get_meta(db,'pending_review_audits',[])
            if waiting:trail.sync_result(waiting,'verified',result=synced,items=gate.records(db));set_meta(db,'pending_review_audits',[])
            after=repo.state(db);after_mail={m['id']:m for m in after['emails']}
            preserved=all(after_mail[m['id']]['notes']==m['notes'] and after_mail[m['id']]['done']==m['done'] for m in before['emails'])
            steps_before={s['id']:s['done'] for s in before['steps']}
            preserved=preserved and all(s['id'] not in steps_before or s['done']==steps_before[s['id']] for s in after['steps']) and before['manual_events']==after['manual_events']
            precise_preserved=all((not m.get('start') or after_mail[m['id']]['start']==m['start']) and
                (not m.get('deadline') or after_mail[m['id']]['deadline']==m['deadline']) and
                ('action' not in m.get('manual_fields',[]) or after_mail[m['id']]['action']==m['action']) for m in before['emails'])
            preserved=preserved and precise_preserved
            if not preserved:raise RuntimeError('人工状态核验不一致，停止网站同步并保留备份')
            report={'checked':audit['total'],'model_calls':audit['model_calls'],'changed_emails':len(changed),
                    'changed':changed,'deferred':deferred,'additional_classifications':new_classified,
                    'manual_state_preserved':preserved,'existing_precise_times_preserved':precise_preserved,'emails':len(after['emails']),'steps':len(after['steps'])}
            (path.parent/'apply-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(report,ensure_ascii=False),flush=True)
        finally:db.close()
    from backend.storage.cloud_sync import sync_once
    result=sync_once(force=True);print(json.dumps({'cloud_sync':result},ensure_ascii=False),flush=True)
    return 0 if result.get('ok') else 1


if __name__=='__main__':sys.exit(main())
