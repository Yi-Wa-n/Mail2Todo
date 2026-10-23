"""Pure task projection, without any online document adapter."""
import re
from backend.mail.core import action_items,inactive_ids

def project(items, detail_values):
    result = {}
    inactive = inactive_ids(items)
    by_id = {i['id']:i for i in items}
    def root_id(item):
        seen = {item['id']}
        while item.get('supersedes') in by_id and item['supersedes'] not in seen:
            item = by_id[item['supersedes']]
            seen.add(item['id'])
        return item['id']
    for item in sorted(action_items(items), key=lambda i:i.get('received','')):
        if item.get('same_task_as'):continue
        v = detail_values(item)
        category = v['通知类型']
        if item.get('date_steps'):
            rules=item['date_steps']
            for index,rule in enumerate(rules):
                stamp=rule.get('date') or ''
                basis=rule['basis']
                estimated=bool(rule.get('estimated'))
                if index==0 and '截止时间' in item.get('manual_values',{}):
                    stamp=v.get('截止时间') or '';basis='手动修订的截止日期/时间（北京时间）';estimated=False
                action=v['待办事项'] if index==0 and '待办事项' in item.get('manual_values',{}) else rule['action']
                result[root_id(item)+':'+rule['key']]={
                    'mail_id':item['id'],'日程':v['公司']+'｜'+rule['label'],
                    '公司':v['公司'],'岗位':v['岗位'],'通知类型':category,
                    '节点类型':rule['label'],'待办事项':action,'节点时间':stamp,'结束时间':stamp,
                    '日期精度':bool(stamp) and len(str(stamp))==10,'推算':estimated,
                    '已完成':bool(item.get('manual_completed')) and len(rules)==1,
                    '原邮件':item.get('remote_record_id',''),'已失效':item['id'] in inactive,'时间依据':basis}
            continue
        # Only explicit action evidence creates a confirmation step. Notes are never parsed.
        evidence = (v.get('待办事项') or '') + '\n' + (item.get('evidence') or '')
        confirm = bool(re.search(r'确认.{0,5}(?:是否.{0,3})?参加|回复.{0,12}参加', evidence) or (re.search(r'预约|准考证', evidence) and not re.search(r'(?:无需|不需|不用|免预约).{0,10}(?:预约|准考证)', evidence)))
        attendance = category in ('笔试','面试','AI面试') or bool(v.get('开始时间'))
        activity = category if category in ('笔试','面试','AI面试','测评') else '安排'
        stages = []
        if v.get('开始时间'):
            stages.append(('开始时间', '参加' + activity, v['开始时间']))
        if v.get('截止时间'):
            label = ('预约/确认参加' if confirm else ('完成' + category if not stages else '截止事项（待核实）'))
            stages.append(('截止时间', label, v['截止时间']))
        if confirm and not v.get('截止时间') and attendance:
            stages.append(('确认参加', '预约/确认参加', ''))
        if not v.get('开始时间') and attendance and confirm:
            # Confirmation and actual attendance stay separate even without a fixed slot.
            stages.append(('事项', '参加' + activity, ''))
        elif not stages:
            label = '参加' + activity if attendance else ('提交材料' if category == '补充材料' else '完成' + category)
            stages.append(('事项', label, ''))
        legacy_done = bool(item.get('manual_completed', v.get('已完成', False)))
        split = len(stages) > 1
        for source, label, stamp in stages:
            estimated = source == '截止时间' and '截止时间' not in item.get('manual_values', {}) and (item.get('deadline_estimated', False) or '估算' in v['待办事项'])
            action = v['待办事项']
            if label == '预约/确认参加':
                action = '按原邮件要求预约或确认是否参加；完成本步骤不代表已参加考试/面试。'
            elif label.startswith('参加') and '待办事项' not in item.get('manual_values', {}):
                event_name = category if category in ('笔试','面试','AI面试','测评') else '该安排'
                action = '按邮件安排实际参加' + event_name + '；结束后再勾选本步骤。'
            basis = '手动修订（北京时间）' if (('开始时间' if source == '开始时间' else '截止时间') in item.get('manual_values', {}) and stamp) else '按收件时间估算，以系统为准' if estimated else ('原邮件及原台账时间（北京时间）' if stamp else '时间待核实；可选场次不作为确定安排')
            if split and legacy_done:
                basis += '；原邮件已有完成标记，未区分步骤，请分别核实'
            result[root_id(item) + ':' + source] = {
                'mail_id':item['id'], '日程':v['公司'] + '｜' + label + ('（估算）' if estimated else ''),
                '公司':v['公司'], '岗位':v['岗位'], '通知类型':category,
                '节点类型':label, '待办事项':action, '节点时间':str(stamp), '结束时间':str(stamp),
                # A legacy email flag is ambiguous for multi-step invitations.
                '已完成':legacy_done and not split,
                '原邮件':item.get('remote_record_id',''),
                '已失效':item['id'] in inactive, '时间依据':basis}
    from backend.mail.deadline_supplements import project_supplements
    return project_supplements(items,detail_values,result)
