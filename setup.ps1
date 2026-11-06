# Hertzon setup: creates .venv next to this script and installs Hertzon with all dependencies.
# Started automatically by Hertzon.exe on first run; can also be run by hand.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$Host.UI.RawUI.WindowTitle = 'Hertzon setup'
# everything shown in this window also goes to setup.log next to this script, so a failed setup can be
# diagnosed after the window is closed (the installer's error message points to it)
$log = Join-Path $PSScriptRoot 'setup.log'
# the log of the previous run is kept: a failed run followed by a successful one must not lose the error
try { if (Test-Path -LiteralPath $log) { Move-Item -LiteralPath $log (Join-Path $PSScriptRoot 'setup.prev.log') -Force } } catch {}
try { Start-Transcript -LiteralPath $log -Force | Out-Null } catch {}

function Find-Python {
    # the usual install locations first: no output of another program has to be decoded
    foreach ($c in @("$env:LOCALAPPDATA\Programs\Python\Python313\python.exe", "$env:ProgramFiles\Python313\python.exe")) {
        if (Test-Path -LiteralPath $c) { return $c }
    }
    # Python writes to a pipe in the ANSI code page while Windows PowerShell 5.1 reads it as the OEM code page,
    # which turns a non-ASCII character of the path (an umlaut in the user name) into another one; UTF-8 on
    # both sides keeps the path intact
    $prevEnc = $null
    try { $prevEnc = [Console]::OutputEncoding; [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
    $prevIo = $env:PYTHONIOENCODING
    $env:PYTHONIOENCODING = 'utf-8'
    try {
        try { $p = & py -3.13 -c "import sys; print(sys.executable)" 2>$null; if ($LASTEXITCODE -eq 0 -and $p) { return $p.Trim() } } catch {}
        # a Python 3.13 on PATH (for example one installed without the py launcher)
        try { $p = & python -c "import sys; print(sys.executable if sys.version_info[:2] == (3, 13) else '')" 2>$null; if ($LASTEXITCODE -eq 0 -and $p) { return $p.Trim() } } catch {}
    } finally {
        if ($prevEnc) { try { [Console]::OutputEncoding = $prevEnc } catch {} }
        $env:PYTHONIOENCODING = $prevIo
    }
    return $null
}

# Hertzon processes that run from this folder (the window, the remote server 'hertzon.remote serve', the
# MCP server started by an AI app, solver workers) keep files of .venv open; pip then fails to update them
function Get-HertzonProcesses {
    $root = (Resolve-Path -LiteralPath $PSScriptRoot).Path.TrimEnd('\') + '\'
    # this script and the processes that started it (Hertzon.exe runs it on the first start) do not count
    $own = @{}
    $id = $PID
    while ($id -and -not $own.ContainsKey($id)) {
        $own[$id] = $true
        $id = (Get-CimInstance Win32_Process -Filter "ProcessId = $id" -ErrorAction SilentlyContinue).ParentProcessId
    }
    Get-Process -ErrorAction SilentlyContinue | Where-Object {
        -not $own.ContainsKey($_.Id) -and (Test-UsesFolder $_ $root)
    }
}

# started from this folder, or a Python that has files of this folder loaded: .venv\Scripts\python.exe is
# only a small redirector that starts the base Python (outside this folder) as a child process with the
# packages of .venv. Stopping the redirector alone leaves that child running with the files of .venv in use,
# until the computer is restarted.
function Test-UsesFolder($p, [string]$root) {
    if ($p.Path -and $p.Path.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { return $true }
    if ($p.ProcessName -notmatch '^pythonw?(\d|\.|$)') { return $false }
    # its parent is the redirector .venv\Scripts\python.exe (or another process from this folder)
    $parent = (Get-CimInstance Win32_Process -Filter "ProcessId = $($p.Id)" -ErrorAction SilentlyContinue).ParentProcessId
    if ($parent) {
        $pp = (Get-CimInstance Win32_Process -Filter "ProcessId = $parent" -ErrorAction SilentlyContinue).ExecutablePath
        if ($pp -and $pp.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { return $true }
    }
    try {
        foreach ($m in $p.Modules) {
            if ($m.FileName -and $m.FileName.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { return $true }
        }
    } catch {}
    return $false
}

function Get-CommandLine($p) {
    (Get-CimInstance Win32_Process -Filter "ProcessId = $($p.Id)" -ErrorAction SilentlyContinue).CommandLine
}

function Show-HertzonProcesses($procs) {
    foreach ($p in $procs) {
        $cmd = Get-CommandLine $p
        Write-Host ("  {0,6}  {1}" -f $p.Id, $(if ($cmd) { $cmd } else { $p.Path }))
    }
}

# a Hertzon window (Hertzon.exe, or pythonw -m hertzon): it may hold unsaved work, so it is never stopped
# without asking; everything else (MCP server, remote service, solver processes, Qt helper processes) has no
# window of its own and is restarted when it is needed again
function Test-HertzonWindow($p) {
    if ($p.ProcessName -ieq 'Hertzon') { return $true }
    $cmd = Get-CommandLine $p
    return [bool]($cmd -and $cmd -match '\s-m\s+hertzon(\s|"|$)')
}

# a Hertzon that is closing (an update started from Hertzon) or solver processes that are being aborted
# need a moment: wait for them quietly instead of asking at once
function Wait-HertzonGone([int]$seconds) {
    $end = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $end) {
        if (-not @(Get-HertzonProcesses)) { return }
        Start-Sleep -Milliseconds 500
    }
}

# the package installation failed while Hertzon processes run from this folder: they are the likely cause
# (files of .venv in use). Background processes are stopped, an open Hertzon window has to be closed.
function Clear-HertzonProcesses {
    $procs = @(Get-HertzonProcesses)
    if (-not $procs) { return $false }
    Write-Host ''
    Write-Host 'These Hertzon processes from this folder keep files of the Python environment in use:' -ForegroundColor Yellow
    Show-HertzonProcesses $procs
    $bg = @($procs | Where-Object { -not (Test-HertzonWindow $_) })
    if ($bg) {
        Write-Host 'Stopping the background processes (MCP server, remote service, solver) ...'
        $bg | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
    while ($true) {
        $win = @(Get-HertzonProcesses | Where-Object { Test-HertzonWindow $_ })
        if (-not $win) { break }
        if ($env:CI) { $win | Stop-Process -Force -ErrorAction SilentlyContinue; break }
        $a = Read-Host 'A Hertzon window is still open. Close it, then press Enter (S = stop it without saving)'
        if ($a -match '^[sS]') { $win | Stop-Process -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 2 }
        else { Wait-HertzonGone 10 }
    }
    # an AI app may already have started its MCP server again: stopped once more right before the retry
    @(Get-HertzonProcesses) | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
    return $true
}

try {
    $venvPy = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    Wait-HertzonGone 30
    $procs = @(Get-HertzonProcesses)
    if ($procs) {
        # usually harmless: most updates do not replace a file these processes use; only if the installation
        # fails are they stopped and the installation repeated (see Clear-HertzonProcesses)
        Write-Host 'Hertzon processes are running from this folder (MCP server of an AI app, remote service, ...):' -ForegroundColor Yellow
        Show-HertzonProcesses $procs
    }
    if (-not (Test-Path -LiteralPath $venvPy)) {
        $py = Find-Python
        if (-not $py) {
            Write-Host 'Python 3.13 not found - installing it with winget ...'
            winget install -e --id Python.Python.3.13 --scope user --accept-package-agreements --accept-source-agreements
            $py = Find-Python
            if (-not $py) { throw 'Python 3.13 could not be installed automatically. Install it from python.org and start again.' }
        }
        Write-Host "Using $py"
        & $py -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Creating the virtual environment failed.' }
    }
    Write-Host 'Installing Hertzon and its packages (EMerge, Qt, VTK) ...'
    & $venvPy -m pip install --upgrade pip
    & $venvPy -m pip install -e ".[test]"
    if ($LASTEXITCODE -ne 0 -and (Clear-HertzonProcesses)) {
        Write-Host 'Installing the packages again ...'
        & $venvPy -m pip install -e ".[test]"
    }
    if ($LASTEXITCODE -ne 0) { throw 'Package installation failed (see messages above).' }
    Write-Host 'Preparing the solver (first-time compilation, can take a few minutes) ...'
    # stdin from a pipe: the setup runs unattended (release check) and must never wait for input
    '' | & $venvPy -c "import emerge; print('EMerge', getattr(emerge, '__version__', 'ok'))"
    if ($LASTEXITCODE -ne 0) { throw 'EMerge could not be imported.' }
    # compile all solver kernels now with a small test solve, so the first real solve starts at full speed
    '' | & $venvPy -m hertzon.solver.warmup
    if ($LASTEXITCODE -ne 0) { Write-Host 'Solver kernels could not be prepared now; they are compiled on the first solve.' -ForegroundColor Yellow }
    Set-Content -LiteralPath (Join-Path $PSScriptRoot '.venv\hertzon_installed.txt') -Value (Get-Date)
    Write-Host ''
    Write-Host 'Hertzon is installed. Start it with Hertzon.exe.' -ForegroundColor Green
    try { Stop-Transcript | Out-Null } catch {}
    Start-Sleep -Seconds 3
    exit 0
} catch {
    Write-Host ''
    Write-Host "ERROR: $_" -ForegroundColor Red
    Write-Host "Full log: $log"
    try { Stop-Transcript | Out-Null } catch {}
    if (-not $env:CI) { Read-Host 'Press Enter to close' }   # the release check runs it unattended
    exit 1
}
