"""Date arithmetic for model-extracted rules; never classify mail with regexes."""
import datetime as dt
import re

DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def date_only(value):
    return isinstance(value,str) and bool(DATE.fullmatch(value))


def due_date(anchor, days, business=False, holidays=(), workdays=()):
    """Exclude anchor day. Explicit holiday/workday overrides take precedence."""
    if not isinstance(days,int) or isinstance(days,bool) or not 1<=days<=366:
        raise ValueError('期限天数须为1至366')
    day=dt.date.fromisoformat(anchor)
    holidays={dt.date.fromisoformat(d) for d in holidays}
    workdays={dt.date.fromisoformat(d) for d in workdays}
    if holidays & workdays:raise ValueError('同一天不能同时为休假和工作日')
    while days:
        day+=dt.timedelta(days=1)
        if not business or day in workdays or (day.weekday()<5 and day not in holidays):days-=1
    return day.isoformat()



def validate_rules(rules, body):
    if not isinstance(rules,list) or len(rules)>12:raise ValueError('无效期限规则列表')
    for rule in rules:
        if not isinstance(rule,dict):raise ValueError('无效期限规则')
        if rule.get('kind') not in ('medical_exam','report_upload','other'):raise ValueError('无效期限事项')
        if rule.get('anchor') not in ('received_date','report_received','other_event','unknown'):raise ValueError('无效期限起点')
        if rule.get('unit') not in ('calendar_days','business_days','hours','minutes'):raise ValueError('无效期限单位')
        maximum={'calendar_days':366,'business_days':366,'hours':8784,'minutes':527040}[rule['unit']]
        if type(rule.get('days')) is not int or not 1<=rule['days']<=maximum:raise ValueError('无效期限数量')
        if type(rule.get('anchor_explicit')) is not bool:raise ValueError('须明确起点是否来自原文')
        for key in ('label','action','source_text'):
            if not isinstance(rule.get(key),str) or not rule[key].strip() or len(rule[key])>2000:raise ValueError('缺少期限说明及原文依据')
        if re.sub(r'\s+','',rule['source_text']) not in re.sub(r'\s+','',body):
            raise ValueError('模型期限依据不在原邮件中')
        if re.search(r'小时|hours?',rule['source_text'],re.I) and rule['unit']!='hours':
            raise ValueError('原文小时单位必须保留为hours，不得换成自然日')
        if re.search(r'分钟|minutes?',rule['source_text'],re.I) and rule['unit']!='minutes':
            raise ValueError('原文分钟单位必须保留为minutes')


def calculated_steps(rules, received, holidays=(), workdays=()):
    """Semantics/trigger/unit come from model. This function only calculates dates."""
    anchor=dt.datetime.fromisoformat(received).astimezone(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
    result=[]
    seen=set()
    for index,rule in enumerate(rules):
        key='报告上传' if rule['kind']=='report_upload' else f'日期期限-{index+1}'
        if key in seen:raise ValueError('期限规则键重复')
        seen.add(key)
        business=rule['unit']=='business_days'
        stamp=None
        if rule['anchor']=='received_date':
            if rule['unit'] in ('hours','minutes'):
                receipt=dt.datetime.fromisoformat(received)
                if receipt.tzinfo is None:raise ValueError('收件时间须包含时区')
                stamp=(receipt+dt.timedelta(**{rule['unit']:rule['days']})).isoformat()
                basis=f'模型提取期限规则；以收件时刻{received}为参考起点，顺延{rule["days"]}{"小时" if rule["unit"]=="hours" else "分钟"}；'+('原文明示收件起算' if rule['anchor_explicit'] else '原文未明示起点，按收件时间估算，以系统为准')
                result.append({**rule,'key':key,'date':stamp,'estimated':not rule['anchor_explicit'],'date_only':False,
                               'basis':basis,'anchor_date':anchor,'business_days':False,'trigger':rule['anchor'],'rule_source':'model'})
                continue
            stamp=due_date(anchor,rule['days'],business,holidays,workdays)
            note=('按周一至周五计数，已排除配置休假并计入配置调休' if holidays or workdays else '按周一至周五计数，未另核对法定节假日和调休') if business else '按自然日计数'
            basis=f'模型提取期限规则；推算日期：以收件日期{anchor}为参考起点，不计收件当日，顺延{rule["days"]}{"个工作日" if business else "天"}；{note}；计数口径待确认，以通知方为准；未指定具体时刻'
            if not rule['anchor_explicit']:basis+='；原文未明确起算点，收件日仅作参考假设'
        elif rule['anchor']=='report_received':
            basis='模型提取期限规则；以实际取得报告为起点，取得日期尚未提供，暂不推算截止日期'
        else:
            basis='模型提取期限规则；所依赖事件或起点尚未确认，暂不推算截止日期'
        result.append({**rule,'key':key,'date':stamp,'estimated':stamp is not None,'date_only':stamp is not None,
                       'basis':basis,'anchor_date':anchor if stamp else None,'business_days':business,
                       'trigger':rule['anchor'],'rule_source':'model'})
    return result


def enrich(decision, mail, calendar=None):
    if not decision.get('relevant') or not decision.get('requires_action'):return decision
    rules=decision.get('deadline_rules',[])
    if not rules:return decision
    validate_rules(rules,mail['body'])
    calendar=calendar or {}
    steps=calculated_steps(rules,mail['received'],calendar.get('holidays',()),calendar.get('workdays',()))
    value=dict(decision);value['date_steps']=steps
    dates=[s['date'] for s in steps if s['date']]
    if dates and not decision.get('deadline'):
        value['deadline']=dates[0];value['deadline_estimated']=next(s['estimated'] for s in steps if s['date'])
        value['deadline_basis']=next(s['basis'] for s in steps if s['date'])
    elif not dates:
        value['deadline']=None
    value['next_action']='；'.join(s['action']+(('（推算截止日期 ' if s['date_only'] else '（估算截止时间 ' if s['estimated'] else '（截止时间 ')+s['date']+('，计数口径待确认）' if s['estimated'] else '）') if s['date'] else '') for s in steps)
    return value
