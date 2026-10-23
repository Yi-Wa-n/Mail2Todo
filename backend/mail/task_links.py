"""Model-confirmed reminder aliases; messages remain separate, tasks do not."""
def task_id(key, items):
    byid = items if isinstance(items, dict) else {i['id']: i for i in items}
    seen = set()
    while key in byid and byid[key].get('same_task_as'):
        if key in seen:
            raise ValueError('事项关联存在循环')
        seen.add(key)
        key = byid[key]['same_task_as']
    return key


def manual_conflict(item, target):
    fields={'通知类型':'category','开始时间':'event_at','截止时间':'deadline','待办事项':'next_action'}
    for label,field in fields.items():
        if label in item.get('manual_values',{}):
            value=item['manual_values'][label]
            other=target.get('manual_values',{}).get(label,target.get(field))
            if (value or None)!=(other or None):return True
    return False


def validate_link(item, byid):
    target = item.get('same_task_as')
    if not target:
        return
    if target == item['id'] or target not in byid:
        raise ValueError('提醒必须引用已有的其他事项')
    old = byid[target]
    if not old.get('requires_action', True):
        raise ValueError('提醒关联对象不是待办事项')
    if old.get('received') and item.get('received') and old['received'] >= item['received']:
        raise ValueError('提醒只能关联更早的事项')
    if old.get('category') != item.get('category'):
        raise ValueError('不同类型的操作不能作为同一提醒合并')
    if item.get('supersedes') or item.get('resolves'):
        raise ValueError('提醒关联不能同时取消或覆盖事项')
    if manual_conflict(item,old):
        raise ValueError('提醒已有人工日程修订，须保留独立事项待核实')
    if item.get('event_at') and old.get('event_at') and item['event_at'] != old['event_at']:
        raise ValueError('开始时间冲突，不能作为纯提醒合并')
    task_id(item['id'], {**byid, item['id']: item})


LINK_RULES = """提醒/催办/重复通知不代表新增一次考试。判断是否与已有事项完全相同，由模型结合两封邮件的正文、公司别名、岗位、轮次、时间和操作判断；同公司或同截止时间本身不足以合并。仅同一操作且没有新增材料、确认步骤、轮次、改期或独立期限时，same_task_as填更早的原事项ID（可引用related_existing_tasks或本批new_mail中更早的邮件ID）；否则null。提醒明确指向前一封邀请且岗位缺失时，可以依上下文确认，不能因双方岗位都是待核实就直接认定相同。存在多个可能对象或冲突无法消除时留null并说明待核实。supersedes仅用于真实改期/替换，不用于纯提醒。关联不会覆盖原日程或把提醒当成已完成。"""

LINK_RULES += "收件人称呼、考生身份或申请编号不同且无法确认是同一人时不得合并，统一考试场次相同不等于同一考生事项。被隐藏的链接、账号或手机号不可作为相同的证据；只有脱敏后模板相同也不足以认定重复。"
