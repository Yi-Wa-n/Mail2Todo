"""Project audited missing deadlines without replacing precise or manual schedules."""
import hashlib
import json
from backend.mail.core import action_items,inactive_ids


def project_supplements(items, detail_values, result):
    inactive=inactive_ids(items);byid={item['id']:item for item in items}
    for item in sorted(action_items(items),key=lambda i:i.get('received','')):
        if item.get('same_task_as'):continue
        rules=item.get('additional_date_steps',[])
        if not rules:continue
        parent=item;seen={item['id']}
        while parent.get('supersedes') in byid and parent['supersedes'] not in seen:
            parent=byid[parent['supersedes']];seen.add(parent['id'])
        root=parent['id'];values=detail_values(item)
        base=result.get(root+':事项')
        reusable=len(rules)==1 and base and base.get('mail_id')==item['id'] and not base.get('节点时间')
        for rule in rules:
            key=(root+':事项') if reusable else root+':补充期限-'+rule['supplement_id']
            stamp=rule.get('date') or '';basis=rule['basis'];estimated=rule['estimated']
            if '截止时间' in item.get('manual_values',{}):
                stamp='';estimated=False;basis+='；本人已有手动截止修订，补充规则不覆盖该修订'
            action=values['待办事项'] if '待办事项' in item.get('manual_values',{}) else rule['action']
            result[key]={'mail_id':item['id'],'日程':values['公司']+'｜'+rule['label'],
                '公司':values['公司'],'岗位':values['岗位'],'通知类型':values['通知类型'],
                '节点类型':rule['label'],'待办事项':action,'节点时间':stamp,'结束时间':stamp,
                '日期精度':rule.get('date_only',False),'推算':estimated,
                '已完成':bool(item.get('additional_completion_snapshot',False)),
                '原邮件':item.get('remote_record_id',''),'已失效':item['id'] in inactive,
                '时间依据':basis+('；沿用本人此前整封邮件的完成状态' if item.get('additional_completion_snapshot') else '')}
    return result


def supplement_key(rule):
    fields={k:rule.get(k) for k in ('kind','source_text','anchor','anchor_explicit','days','unit')}
    return hashlib.sha256(json.dumps(fields,ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:16]
