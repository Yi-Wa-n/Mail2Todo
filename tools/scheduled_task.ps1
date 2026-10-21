param(
    [ValidateSet('Install','Status','Update')][string]$Mode='Status',
    [ValidateRange(15,10080)][int]$IntervalMinutes=60,
    [ValidateRange(0,1440)][int]$LogonDelayMinutes=1,
    [ValidateRange(1,1440)][int]$RetryMinutes=5,
    [ValidateRange(1,10)][int]$RetryCount=2
)
$ErrorActionPreference='Stop'
$taskName='AutumnRecruitMail-LocalOnly'
$oldName='AutumnRecruitMail-LocalGate'
$root=(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'python_runtime.ps1')
$python=$null
if ($Mode -ne 'Status') { $python=Resolve-ProjectPython $root -PreserveTask }
$arguments='-X utf8 -m backend.jobs.scheduled_gate'
$folder=Join-Path $root 'private\scheduler'
function Ns($doc) { $n=[Xml.XmlNamespaceManager]::new($doc.NameTable);$n.AddNamespace('t',$doc.DocumentElement.NamespaceURI);return ,$n }
if ($Mode -eq 'Update') {
    $task=Get-ScheduledTask -TaskName $taskName
    if ([string]$task.State -eq 'Running') {throw 'SCAN_RUNNING'}
    if ($task.Actions.Execute -ne $python -or $task.Actions.Arguments -ne $arguments -or $task.Actions.WorkingDirectory -ne $root) {throw 'ENTRY_MISMATCH'}
    $original=Export-ScheduledTask -TaskName $taskName
    [xml]$xml=$original;$ns=Ns $xml
    $intervalNode=$xml.SelectSingleNode('//t:TimeTrigger/t:Repetition/t:Interval',$ns)
    if ([Xml.XmlConvert]::ToTimeSpan($intervalNode.InnerText).TotalMinutes -ne $IntervalMinutes) {
        $xml.SelectSingleNode('//t:TimeTrigger/t:StartBoundary',$ns).InnerText=(Get-Date).AddMinutes($IntervalMinutes).ToString('yyyy-MM-ddTHH:mm:sszzz')
    }
    $intervalNode.InnerText='PT'+$IntervalMinutes+'M'
    $xml.SelectSingleNode('//t:LogonTrigger/t:Delay',$ns).InnerText='PT'+$LogonDelayMinutes+'M'
    $xml.SelectSingleNode('//t:RestartOnFailure/t:Interval',$ns).InnerText='PT'+$RetryMinutes+'M'
    $xml.SelectSingleNode('//t:RestartOnFailure/t:Count',$ns).InnerText=[string]$RetryCount
    New-Item -ItemType Directory -Path $folder -Force | Out-Null
    $original | Set-Content -LiteralPath (Join-Path $folder ('before-update-'+(Get-Date -Format 'yyyyMMdd-HHmmss-ffff')+'.xml')) -Encoding Unicode
    try {
        Register-ScheduledTask -TaskName $taskName -Xml $xml.OuterXml -Force | Out-Null
        [xml]$readback=Export-ScheduledTask -TaskName $taskName;$readNs=Ns $readback
        $verified=([Xml.XmlConvert]::ToTimeSpan($readback.SelectSingleNode('//t:TimeTrigger/t:Repetition/t:Interval',$readNs).InnerText).TotalMinutes -eq $IntervalMinutes -and
            [Xml.XmlConvert]::ToTimeSpan($readback.SelectSingleNode('//t:LogonTrigger/t:Delay',$readNs).InnerText).TotalMinutes -eq $LogonDelayMinutes -and
            [Xml.XmlConvert]::ToTimeSpan($readback.SelectSingleNode('//t:RestartOnFailure/t:Interval',$readNs).InnerText).TotalMinutes -eq $RetryMinutes -and
            [int]$readback.SelectSingleNode('//t:RestartOnFailure/t:Count',$readNs).InnerText -eq $RetryCount)
        $action=(Get-ScheduledTask -TaskName $taskName).Actions
        if (!$verified -or $action.Execute -ne $python -or $action.Arguments -ne $arguments -or $action.WorkingDirectory -ne $root) {throw 'READBACK_MISMATCH'}
    } catch {
        Register-ScheduledTask -TaskName $taskName -Xml $original -Force | Out-Null
        throw
    }
}
if ($Mode -eq 'Install') {
    if (!(Test-Path -LiteralPath $python) -or !(Test-Path -LiteralPath (Join-Path $root 'backend\jobs\scheduled_gate.py'))) {throw 'Missing local entry point'}
    $old=Get-ScheduledTask -TaskName $oldName
    if ([string]$old.State -eq 'Running') {throw 'Original scan is running; wait for completion before switching'}
    New-Item -ItemType Directory -Path $folder -Force | Out-Null
    $backup=Join-Path $folder 'original-task.xml'
    if (!(Test-Path -LiteralPath $backup)) {Export-ScheduledTask -TaskName $oldName | Set-Content -LiteralPath $backup -Encoding Unicode}
    $existing=Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing -and ($existing.Actions.WorkingDirectory -ne $root -or $existing.Actions.Arguments -ne $arguments)) {throw 'Target task already belongs to another entry point'}
    [xml]$xml=Export-ScheduledTask -TaskName $oldName
    $ns=Ns $xml
    $xml.SelectSingleNode('//t:RegistrationInfo/t:URI',$ns).InnerText='\'+$taskName
    $xml.SelectSingleNode('//t:RegistrationInfo/t:Description',$ns).InnerText='Local SQLite recruitment mail scan; no document APIs or notifications.'
    $exec=$xml.SelectSingleNode('//t:Actions/t:Exec',$ns)
    $exec.SelectSingleNode('t:Command',$ns).InnerText=$python
    $exec.SelectSingleNode('t:Arguments',$ns).InnerText=$arguments
    $exec.SelectSingleNode('t:WorkingDirectory',$ns).InnerText=$root
    $settings=$xml.SelectSingleNode('//t:Settings',$ns)
    $enabled=$settings.SelectSingleNode('t:Enabled',$ns)
    if (!$enabled) {$enabled=$xml.CreateElement('Enabled',$xml.DocumentElement.NamespaceURI);$settings.AppendChild($enabled)|Out-Null}
    $enabled.InnerText='false'
    Register-ScheduledTask -TaskName $taskName -Xml $xml.OuterXml -Force | Out-Null
    $new=Get-ScheduledTask -TaskName $taskName
    if ($new.Actions.Execute -ne $python -or $new.Actions.WorkingDirectory -ne $root -or $new.Actions.Arguments -ne $arguments) {throw 'New task readback mismatch; original task unchanged'}
    $oldWasEnabled=$old.Settings.Enabled
    try {
        Disable-ScheduledTask -TaskName $oldName | Out-Null
        Enable-ScheduledTask -TaskName $taskName | Out-Null
        if ((Get-ScheduledTask -TaskName $oldName).Settings.Enabled -or !(Get-ScheduledTask -TaskName $taskName).Settings.Enabled) {throw 'Task enable state mismatch'}
    } catch {
        Disable-ScheduledTask -TaskName $taskName | Out-Null
        if ($oldWasEnabled) {Enable-ScheduledTask -TaskName $oldName | Out-Null}
        throw
    }
}
$task=Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (!$task) { @{installed=$false;enabled=$false} | ConvertTo-Json -Compress; exit }
$info=Get-ScheduledTaskInfo -TaskName $taskName
[xml]$actual=Export-ScheduledTask -TaskName $taskName
$ns=Ns $actual
$interval=$actual.SelectSingleNode('//t:TimeTrigger/t:Repetition/t:Interval',$ns).InnerText
$delay=$actual.SelectSingleNode('//t:LogonTrigger/t:Delay',$ns).InnerText
$retry=$actual.SelectSingleNode('//t:RestartOnFailure/t:Interval',$ns).InnerText
$old=Get-ScheduledTask -TaskName $oldName -ErrorAction SilentlyContinue
$result=[ordered]@{installed=$true;enabled=[bool]$task.Settings.Enabled;task=$taskName;state=[string]$task.State;
    interval_hours=[Xml.XmlConvert]::ToTimeSpan($interval).TotalHours;
    logon_delay_minutes=[Xml.XmlConvert]::ToTimeSpan($delay).TotalMinutes;
    retry_minutes=[Xml.XmlConvert]::ToTimeSpan($retry).TotalMinutes;
    retry_count=[int]$actual.SelectSingleNode('//t:RestartOnFailure/t:Count',$ns).InnerText;
    next_run=$info.NextRunTime.ToString('yyyy-MM-ddTHH:mm:sszzz');
    last_run=$info.LastRunTime.ToString('yyyy-MM-ddTHH:mm:sszzz');last_result=$info.LastTaskResult;
    old_task_enabled=($old -and [bool]$old.Settings.Enabled)}
$json=$result | ConvertTo-Json -Compress
if ($Mode -eq 'Install') {$json | Set-Content -LiteralPath (Join-Path $folder 'installed.json') -Encoding UTF8}
$json
