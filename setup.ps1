# Hertzon setup: creates .venv next to this script and installs Hertzon with all dependencies.
# Started automatically by Hertzon.exe on first run; can also be run by hand.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$Host.UI.RawUI.WindowTitle = 'Hertzon setup'
# everything shown in this window also goes to setup.log next to this script, so a failed setup can be
# diagnosed after the window is closed (the installer's error message points to it)
$log = Join-Path $PSScriptRoot 'setup.log'
try { Start-Transcript -LiteralPath $log -Force | Out-Null } catch {}

function Find-Python {
    try { $p = & py -3.13 -c "import sys; print(sys.executable)" 2>$null; if ($LASTEXITCODE -eq 0 -and $p) { return $p.Trim() } } catch {}
    # a Python 3.13 on PATH (for example one installed without the py launcher)
    try { $p = & python -c "import sys; print(sys.executable if sys.version_info[:2] == (3, 13) else '')" 2>$null; if ($LASTEXITCODE -eq 0 -and $p) { return $p.Trim() } } catch {}
    foreach ($c in @("$env:LOCALAPPDATA\Programs\Python\Python313\python.exe", "$env:ProgramFiles\Python313\python.exe")) {
        if (Test-Path -LiteralPath $c) { return $c }
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
        -not $own.ContainsKey($_.Id) -and $_.Path -and $_.Path.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)
    }
}

function Wait-HertzonClosed {
    while ($true) {
        $procs = @(Get-HertzonProcesses)
        if (-not $procs) { return }
        Write-Host ''
        Write-Host 'Hertzon is still running from this folder (window, remote server or MCP server of an AI app):' -ForegroundColor Yellow
        foreach ($p in $procs) {
            $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId = $($p.Id)" -ErrorAction SilentlyContinue).CommandLine
            Write-Host ("  {0,6}  {1}" -f $p.Id, $(if ($cmd) { $cmd } else { $p.Path }))
        }
        if ($env:CI) { Write-Host 'Continuing anyway (unattended run).'; return }
        $a = Read-Host 'Close them (for an MCP server: quit the AI app), then press Enter. S = stop them, C = continue anyway'
        if ($a -match '^[sS]') { $procs | Stop-Process -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 2 }
        elseif ($a -match '^[cC]') { return }
    }
}

try {
    $venvPy = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    Wait-HertzonClosed
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
