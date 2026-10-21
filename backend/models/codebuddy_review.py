"""CodeBuddy CN JSON transport; discard reasoning and echoed messages in memory."""
import json
from types import SimpleNamespace
from backend.models.qoder_review import can_fallback as qoder_can_fallback


def command(config, runtime):
    return [config['codebuddy_cli'],'--output-format','json','--no-session-persistence',
            '--tools','','--strict-mcp-config','--mcp-config','{"mcpServers":{}}',
            '--setting-sources','','--permission-mode','dontAsk','--effort',config.get('reasoning_effort','low'),
            '--model',config.get('codebuddy_model','deepseek-v4.1-flash'),
            '--max-turns','1','-p']


def events(stdout):
    value=json.loads(stdout)
    if isinstance(value,dict):return [value]
    if isinstance(value,list) and all(isinstance(e,dict) for e in value):return value
    raise ValueError('CodeBuddy输出不是JSON事件或事件数组')


def final_event(stdout):
    matches=[e for e in events(stdout) if e.get('type')=='result']
    if len(matches)!=1:raise ValueError('CodeBuddy未提供唯一最终结果')
    return matches[0]


def can_fallback(run):
    try:final=final_event(run.stdout)
    except (ValueError,TypeError):
        return qoder_can_fallback(run) if run.returncode else False
    # Inspect transport errors only, not assistant output/reasoning or echoed mail.
    return qoder_can_fallback(SimpleNamespace(returncode=run.returncode,stderr=run.stderr,
                                             stdout=json.dumps(final) if final.get('is_error') else '{}'))


def extract_final(stdout):
    records=events(stdout);final=final_event(stdout)
    if final.get('is_error') or final.get('subtype')!='success' or final.get('permission_denials'):
        raise ValueError('CodeBuddy判断未成功或尝试调用禁用工具')
    output=final.get('result')
    if not isinstance(output,str):raise ValueError('CodeBuddy缺少最终文本')
    models={e.get('providerData',{}).get('model') for e in records
            if e.get('type')=='message' and e.get('role')=='assistant'}-{None,''}
    model=next(iter(models)) if len(models)==1 else None
    return output,model


def normalize(output):
    output=output.strip()
    if output.startswith('```json\n') and output.endswith('```'):output=output[8:-3].strip()
    return output


def decode(stdout):
    output,model=extract_final(stdout)
    output=normalize(output)
    json.loads(output)
    return output,model
