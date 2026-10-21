# Shared resolution only: never registers or changes a scheduled task.
function ConvertTo-WindowlessPython([string]$Candidate, [string]$ProjectRoot) {
    if (!$Candidate) { return $null }
    $path = $Candidate.Trim().Trim('"')
    if (![IO.Path]::IsPathRooted($path)) { $path = Join-Path $ProjectRoot $path }
    if ((Split-Path $path -Leaf) -ieq 'python.exe') {
        $path = Join-Path (Split-Path $path -Parent) 'pythonw.exe'
    }
    if ((Split-Path $path -Leaf) -ine 'pythonw.exe' -or $path -match '\\WindowsApps\\') { return $null }
    if (Test-Path -LiteralPath $path -PathType Leaf) { return (Resolve-Path -LiteralPath $path).Path }
    return $null
}

function Get-ProjectTaskPython([string]$ProjectRoot) {
    $task = Get-ScheduledTask -TaskName 'AutumnRecruitMail-LocalOnly' -ErrorAction SilentlyContinue
    if (!$task) { return $null }
    $actions = @($task.Actions)
    if ($actions.Count -ne 1 -or $actions[0].WorkingDirectory -ine $ProjectRoot -or
        $actions[0].Arguments -cne '-X utf8 -m backend.jobs.scheduled_gate') { return $null }
    return ConvertTo-WindowlessPython $actions[0].Execute $ProjectRoot
}

function Resolve-ProjectPython([string]$ProjectRoot, [switch]$PreserveTask) {
    if ($PreserveTask) {
        $existing = Get-ProjectTaskPython $ProjectRoot
        if ($existing) { return $existing }
    }
    $config = Join-Path $ProjectRoot 'private\python-path.txt'
    if (Test-Path -LiteralPath $config -PathType Leaf) {
        $resolved = ConvertTo-WindowlessPython (Get-Content -LiteralPath $config -Raw -Encoding UTF8) $ProjectRoot
        if (!$resolved) { throw 'Invalid Python path in private/python-path.txt; pythonw.exe is required.' }
        return $resolved
    }
    if ($env:AUTUMN_PYTHON) {
        $resolved = ConvertTo-WindowlessPython $env:AUTUMN_PYTHON $ProjectRoot
        if (!$resolved) { throw 'Invalid AUTUMN_PYTHON; pythonw.exe is required.' }
        return $resolved
    }
    $resolved = ConvertTo-WindowlessPython '.venv\Scripts\pythonw.exe' $ProjectRoot
    if ($resolved) { return $resolved }
    $existing = Get-ProjectTaskPython $ProjectRoot
    if ($existing) { return $existing }
    foreach ($name in @('pythonw.exe', 'python.exe')) {
        $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($command) {
            $resolved = ConvertTo-WindowlessPython $command.Source $ProjectRoot
            if ($resolved) { return $resolved }
        }
    }
    throw 'Python not found. Create .venv or configure private/python-path.txt.'
}
