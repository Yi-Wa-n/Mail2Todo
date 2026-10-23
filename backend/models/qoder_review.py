"""Qoder CN headless review transport. No tools, MCP, or session persistence."""
import json


def can_fallback(run):
    """A model failure may use Auto once; auth/account errors need user action."""
    message=(str(run.stderr or '')+'\n'+str(run.stdout or '')).lower()
    if any(text in message for text in ('not logged in','unauthorized','invalid token',
            'token expired','authentication','insufficient credits','insufficient balance',
            '未登录','登录失效','令牌失效','余额不足','额度不足')):
        return False
    if run.returncode:
        return True
    try:
        envelope=json.loads(run.stdout)
    except (ValueError,TypeError):
        return False  # Malformed decisions are not retried as a second judgment.
    return envelope.get('type')=='result' and envelope.get('is_error') is True


def validate(value, schema):
    """Validate the review contract, including bounded integer deadline counts."""
    kinds=schema['type'] if isinstance(schema['type'],list) else [schema['type']]
    matches={'object':isinstance(value,dict),'array':isinstance(value,list),
             'string':isinstance(value,str),'boolean':isinstance(value,bool),'null':value is None,
             'integer':type(value) is int}
    if not any(matches.get(kind,False) for kind in kinds):
        raise ValueError('模型字段类型不符合输出约定')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError('模型分类超出输出约定')
    if type(value) is int and (('minimum' in schema and value<schema['minimum']) or ('maximum' in schema and value>schema['maximum'])):
        raise ValueError('模型数值超出输出约定')
    if isinstance(value,list) and 'maxItems' in schema and len(value)>schema['maxItems']:
        raise ValueError('模型列表超出输出约定')
    if isinstance(value,dict):
        props=schema.get('properties',{})
        if not set(schema.get('required',[])).issubset(value):
            raise ValueError('模型输出缺少必需字段')
        if schema.get('additionalProperties') is False and set(value)-set(props):
            raise ValueError('模型输出包含未约定字段')
        for key,item in value.items():
            if key in props:validate(item,props[key])
    if isinstance(value,list):
        for item in value:validate(item,schema['items'])


def command(config, runtime):
    return [config['qoder_cli'],'--cwd',str(runtime),'--output-format','json',
            '--no-session-persistence','--tools','','--strict-mcp-config',
            '--mcp-config','{"mcpServers":{}}','--setting-sources','',
            '--permission-mode','dont_ask','--reasoning-effort','low',
            '--model',config.get('qoder_model','auto'),
            '--max-model-request-retries','0','-p']


def decode(stdout):
    envelope=json.loads(stdout)
    if envelope.get('type')!='result' or envelope.get('is_error') or envelope.get('subtype')!='success':
        raise ValueError('Qoder判断未成功，保留候选')
    if envelope.get('permission_denials'):
        raise ValueError('Qoder尝试使用判断任务以外的工具，未入账')
    output=envelope.get('result')
    if not isinstance(output,str):
        raise ValueError('Qoder未返回文本JSON结果')
    # Markdown wrapping is presentation only; reject other surrounding prose.
    text=output.strip()
    if text.startswith('```json\n') and text.endswith('```'):
        text=text[8:-3].strip()
    json.loads(text)
    models=list(envelope.get('modelUsage',{}))
    reported=models[0] if len(models)==1 else None
    return text,reported
