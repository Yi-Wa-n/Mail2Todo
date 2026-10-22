$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
try {
    . (Join-Path $PSScriptRoot 'python_runtime.ps1')
    $python = Resolve-ProjectPython $root
    # A best-effort launch cache lets the VBS entry skip PowerShell next time.
    try {
        $private = Join-Path $root 'private'
        New-Item -ItemType Directory -Path $private -Force | Out-Null
        $cache = Join-Path $private 'python-launch-cache.txt'
        $cacheTemp = Join-Path $private ('python-launch-cache-' + [guid]::NewGuid().ToString('N') + '.tmp')
        [IO.File]::WriteAllText($cacheTemp, $python, [Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $cacheTemp -Destination $cache -Force
    } catch { } # Caching failure must not stop a valid launch.
    $entry = Join-Path $root 'app\launch.py'
    Start-Process -FilePath $python -ArgumentList @('-X', 'utf8', ('"' + $entry + '"')) -WorkingDirectory $root -WindowStyle Hidden
} catch {
    $folder = Join-Path $root 'private'
    New-Item -ItemType Directory -Path $folder -Force | Out-Null
    ((Get-Date -Format o) + ' ' + $_.Exception.Message) | Add-Content -LiteralPath (Join-Path $folder 'launcher.log') -Encoding UTF8
    exit 1
}
