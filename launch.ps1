# Opens PC Dashboard on the portrait (Onn) monitor, filling half of it.
#   launch.ps1            bottom half (default - leaves desktop icons at the top visible)
#   launch.ps1 -Top       top half
#   launch.ps1 -Stream    open Stream Control (OBS) instead, in the TOP half
#   launch.ps1 -Stop      stop the dashboard's background server
#   launch.ps1 -Delay 20  wait first (used at sign-in, so monitors and Explorer settle)
param([switch]$Top, [switch]$Stop, [switch]$Stream, [int]$Delay = 0)

if ($Delay -gt 0) { Start-Sleep -Seconds $Delay }

$ErrorActionPreference = 'Stop'
$here   = Split-Path -Parent $MyInvocation.MyCommand.Path
$port   = 8095
$url    = "http://127.0.0.1:$port/"
$py     = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = "$env:LOCALAPPDATA\Programs\Python\Python312\pythonw.exe" }

function Get-DashPid {
    $c = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { $c.OwningProcess }
}

if ($Stop) {
    $p = Get-DashPid
    if ($p) { Stop-Process -Id $p -Force; "PC Dashboard server stopped (pid $p)." } else { "Not running." }
    return
}

# 1. Start the background server if it is not already up.
if (-not (Get-DashPid)) {
    Start-Process -FilePath $py -ArgumentList "`"$here\dash.py`"" -WorkingDirectory $here -WindowStyle Hidden
    for ($i = 0; $i -lt 40 -and -not (Get-DashPid); $i++) { Start-Sleep -Milliseconds 250 }
}

$title = if ($Stream) { 'Stream Control' } else { 'PC Dashboard' }
if ($Stream) { $url = "${url}stream"; $Top = $true }

Add-Type -Namespace PCDash -Name Win -MemberDefinition @'
public delegate bool EnumProc(System.IntPtr h, System.IntPtr l);
[DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc p, System.IntPtr l);
[DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(System.IntPtr h, System.Text.StringBuilder s, int n);
[DllImport("user32.dll")] public static extern bool IsWindowVisible(System.IntPtr h);
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(System.IntPtr h);
[DllImport("user32.dll")] public static extern bool SetWindowPos(System.IntPtr h, System.IntPtr after, int x, int y, int cx, int cy, uint flags);
[DllImport("user32.dll")] public static extern bool ShowWindow(System.IntPtr h, int cmd);
'@

# Every top-level window, not each process's "main" one: Edge app windows share
# a process, so MainWindowTitle only ever reports one of them.
function Find-Window([string]$t) {
    $script:found = [System.IntPtr]::Zero
    [PCDash.Win]::EnumWindows({ param($h, $l)
        $sb = New-Object System.Text.StringBuilder 256
        [void][PCDash.Win]::GetWindowText($h, $sb, 256)
        if ($sb.ToString() -eq $t -and [PCDash.Win]::IsWindowVisible($h)) { $script:found = $h; return $false }
        $true }, [System.IntPtr]::Zero) | Out-Null
    $script:found
}

# 2. Where it goes: half of the portrait monitor (taller than wide), else the primary.
Add-Type -AssemblyName System.Windows.Forms
$scr = [System.Windows.Forms.Screen]::AllScreens | Where-Object { $_.Bounds.Height -gt $_.Bounds.Width } | Select-Object -First 1
if (-not $scr) { $scr = [System.Windows.Forms.Screen]::PrimaryScreen }
$wa = $scr.WorkingArea          # excludes that screen's own taskbar
$h  = [int]($wa.Height / 2)
$x  = $wa.X
$y  = if ($Top) { $wa.Y } else { $wa.Y + $wa.Height - $h }

function Place([System.IntPtr]$hwnd) {
    [void][PCDash.Win]::ShowWindow($hwnd, 9)   # SW_RESTORE, in case it was maximised/minimised
    # SWP_NOZORDER | SWP_NOACTIVATE
    [void][PCDash.Win]::SetWindowPos($hwnd, [System.IntPtr]::Zero, $x, $y, $wa.Width, $h, 0x0014)
}

# 3. Already open? Put it back in its slot and bring it forward - no second copy.
$existing = Find-Window $title
if ($existing -ne [System.IntPtr]::Zero) {
    Place $existing
    [void][PCDash.Win]::SetForegroundWindow($existing)
    return
}

# 4. Open a chromeless Edge app window in its own profile, then place it ourselves:
#    Edge remembers where an app window last was and ignores --window-position.
$edge = "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
if (-not (Test-Path $edge)) { $edge = "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe" }
$profileDir = "$env:LOCALAPPDATA\PCDash\edge-profile"
Start-Process -FilePath $edge -ArgumentList @(
    "--app=$url", "--user-data-dir=`"$profileDir`"", "--no-first-run",
    "--window-position=$x,$y", "--window-size=$($wa.Width),$h"
)
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 250
    $w = Find-Window $title
    if ($w -ne [System.IntPtr]::Zero) { Start-Sleep -Milliseconds 400; Place $w; break }
}
