"""Read Windows scheduler status, cached briefly; never starts a scan."""
import json
from pathlib import Path
import subprocess
import threading
import time

_guard=threading.Lock()
_cached=None
_checked=0
SCRIPT=Path(__file__).resolve().parents[2]/'tools/scheduled_task.ps1'

def status():
    global _cached,_checked
    with _guard:
        if _cached is not None and time.monotonic()-_checked<20:return dict(_cached)
        try:
            run=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(SCRIPT),'-Mode','Status'],
                capture_output=True,encoding='utf-8',errors='replace',timeout=12,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if run.returncode:raise RuntimeError('scheduler status failed')
            _cached=json.loads(run.stdout.lstrip('\ufeff'))
        except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired):
            _cached={'installed':None,'enabled':None,'error':'无法读取计划任务状态'}
        _checked=time.monotonic()
        return dict(_cached)


def update(value):
    global _cached,_checked
    fields={'interval_hours','logon_delay_minutes','retry_minutes','retry_count'}
    if not isinstance(value,dict) or set(value)!=fields:raise ValueError('定时设置字段不完整')
    hours=value['interval_hours']
    if type(hours) not in (int,float) or not 0.25<=hours<=168 or hours*60!=int(hours*60) or int(hours*60)%15:
        raise ValueError('检查间隔为0.25至168小时，以15分钟递增')
    for key,low,high in [('logon_delay_minutes',0,1440),('retry_minutes',1,1440),('retry_count',1,10)]:
        if type(value[key]) is not int or not low<=value[key]<=high:raise ValueError('延迟/重试参数超出允许范围')
    args=['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(SCRIPT),'-Mode','Update',
        '-IntervalMinutes',str(int(hours*60)),'-LogonDelayMinutes',str(value['logon_delay_minutes']),
        '-RetryMinutes',str(value['retry_minutes']),'-RetryCount',str(value['retry_count'])]
    with _guard:
        try:
            result=subprocess.run(args,capture_output=True,encoding='utf-8',errors='replace',timeout=45,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except (OSError,subprocess.TimeoutExpired):
            _cached=None
            raise ValueError('保存结果未确认，请刷新定时状态后重试') from None
        _cached=None
        if result.returncode:
            if 'SCAN_RUNNING' in result.stderr:raise ValueError('后台正在扫描，请结束后再保存')
            raise ValueError('定时设置保存未成功，请刷新核对；已保留修改前任务备份')
        try:actual=json.loads(result.stdout.lstrip('\ufeff'))
        except ValueError:raise ValueError('无法确认保存结果，请刷新定时状态') from None
        if any(actual.get(key)!=value[key] for key in fields):raise ValueError('定时参数读回不一致，请刷新核对')
        _cached=dict(actual)
        _checked=time.monotonic()
    return actual
