# Run by the installer before it replaces the program files: stops the Hertzon processes without a window
# that use the installation folder (MCP server started by an AI app, the remote service
# 'hertzon.remote serve', solver processes, Qt helper processes). They keep files of the program
# (hertzon\mesher\_bin\hzmesh.dll) and of .venv in use, so the update could not replace them until the next
# restart of the computer. A Hertzon window is not touched: the installer asks to close it.
param([Parameter(Mandatory = $true)][string]$Root)
$ErrorActionPreference = 'SilentlyContinue'
$root = $Root.TrimEnd('\') + '\'

# started from the folder, or a Python with files of the folder loaded: .venv\Scripts\python.exe is only a
# redirector that runs the base Python (outside the folder) as a child process with the packages of .venv
function Test-UsesFolder($p) {
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

function Get-Background {
    Get-Process | Where-Object { $_.Id -ne $PID -and (Test-UsesFolder $_) } | Where-Object {
        $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId = $($_.Id)").CommandLine
        $_.ProcessName -ine 'Hertzon' -and -not ($cmd -and $cmd -match '\s-m\s+hertzon(\s|"|$)')
    }
}

$procs = @(Get-Background)
foreach ($p in $procs) {
    Write-Output ("stopping {0} {1}" -f $p.Id, $p.Path)
    Stop-Process -Id $p.Id -Force
}
# until Windows has released their files
$end = (Get-Date).AddSeconds(10)
while ($procs -and @(Get-Background) -and (Get-Date) -lt $end) { Start-Sleep -Milliseconds 300 }
exit 0
