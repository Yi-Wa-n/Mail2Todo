$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
try {
    . (Join-Path $PSScriptRoot 'python_runtime.ps1')
    $python = Resolve-ProjectPython $root
    $entry = Join-Path $root 'app\launch.py'
    Start-Process -FilePath $python -ArgumentList @('-X', 'utf8', ('"' + $entry + '"')) -WorkingDirectory $root -WindowStyle Hidden
} catch {
    $folder = Join-Path $root 'private'
    New-Item -ItemType Directory -Path $folder -Force | Out-Null
    ((Get-Date -Format o) + ' ' + $_.Exception.Message) | Add-Content -LiteralPath (Join-Path $folder 'launcher.log') -Encoding UTF8
    exit 1
}
