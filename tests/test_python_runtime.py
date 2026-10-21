"""Resolve fake executables in temporary directories; never launch scans/tasks."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1]/'tools/python_runtime.ps1'

class PythonRuntimeTests(unittest.TestCase):
    def test_resolution_priority_and_existing_task_compatibility(self):
        with tempfile.TemporaryDirectory(prefix='python runtime ') as folder:
            root = Path(folder)
            for name in ('configured','environment','existing','.venv/Scripts','path'):
                directory=root/name;directory.mkdir(parents=True)
                (directory/'pythonw.exe').touch()
            (root/'private').mkdir()
            script = r'''
$ErrorActionPreference='Stop'
. $env:TEST_RESOLVER
$root=$env:TEST_ROOT
function Get-ScheduledTask { [pscustomobject]@{Actions=@([pscustomobject]@{
    Execute=(Join-Path $root 'existing\pythonw.exe');WorkingDirectory=$root;
    Arguments='-X utf8 -m backend.jobs.scheduled_gate'})} }
function Get-Command { [pscustomobject]@{Source=(Join-Path $root 'path\pythonw.exe')} }
function Check($actual,$expected) { if ($actual -ne (Join-Path $root $expected)) {throw "Unexpected resolution: $actual"} }
$env:AUTUMN_PYTHON=Join-Path $root 'environment\pythonw.exe'
Set-Content -LiteralPath (Join-Path $root 'private\python-path.txt') -Value 'configured\python.exe' -Encoding UTF8
Check (Resolve-ProjectPython $root) 'configured\pythonw.exe'
Check (Resolve-ProjectPython $root -PreserveTask) 'existing\pythonw.exe'
Set-Content -LiteralPath (Join-Path $root 'private\python-path.txt') -Value 'missing\pythonw.exe'
$failed=$false
try {Resolve-ProjectPython $root | Out-Null} catch {$failed=$true}
if (!$failed) {throw 'Invalid explicit config silently fell back'}
Remove-Item -LiteralPath (Join-Path $root 'private\python-path.txt')
Check (Resolve-ProjectPython $root) 'environment\pythonw.exe'
Remove-Item Env:AUTUMN_PYTHON
Check (Resolve-ProjectPython $root) '.venv\Scripts\pythonw.exe'
Remove-Item -LiteralPath (Join-Path $root '.venv\Scripts\pythonw.exe')
Check (Resolve-ProjectPython $root) 'existing\pythonw.exe'
function Get-ScheduledTask {$null}
Check (Resolve-ProjectPython $root) 'path\pythonw.exe'
if (ConvertTo-WindowlessPython 'C:\Users\Example\AppData\Local\Microsoft\WindowsApps\python.exe' $root) {throw 'Store alias accepted'}
function Get-Command {$null}
$failed=$false
try {Resolve-ProjectPython $root | Out-Null} catch {$failed=$true}
if (!$failed) {throw 'Missing runtime accepted'}
'Runtime resolution checks passed'
'''
            harness=root/'check.ps1';harness.write_text(script,encoding='utf-8-sig')
            env={**os.environ,'TEST_RESOLVER':str(SCRIPT),'TEST_ROOT':str(root)}
            result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(harness)],env=env,capture_output=True,encoding='utf-8',errors='replace',timeout=20,
                                  creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.assertEqual(0,result.returncode,result.stderr)
            self.assertIn('checks passed',result.stdout)

    def test_launcher_quotes_path_without_starting_process(self):
        with tempfile.TemporaryDirectory(prefix='launch with spaces ') as folder:
            root=Path(folder);(root/'tools').mkdir();(root/'app').mkdir()
            (root/'tools/open_app.ps1').write_bytes((SCRIPT.parent/'open_app.ps1').read_bytes())
            (root/'tools/python_runtime.ps1').write_text("function Resolve-ProjectPython($root) {return (Join-Path $root 'pythonw.exe')}",encoding='utf-8')
            harness=root/'check.ps1'
            harness.write_text(r'''
$ErrorActionPreference='Stop'
function Start-Process {
    param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle)
    if ($WindowStyle -ne 'Hidden') {throw 'Visible process requested'}
    if ($ArgumentList[2] -ne ('"'+(Join-Path $WorkingDirectory 'app\launch.py')+'"')) {throw 'Missing path quoting'}
    'Launch parameters verified'
}
. (Join-Path $PSScriptRoot 'tools\open_app.ps1')
''',encoding='utf-8-sig')
            result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(harness)],capture_output=True,encoding='utf-8',errors='replace',timeout=15,
                                  creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.assertEqual(0,result.returncode,result.stderr)
            self.assertIn('parameters verified',result.stdout)
