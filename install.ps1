# ─── Polars Daily Log — Windows Installer ──────────────────────────
# Supports: Windows 10 / 11 (PowerShell 5.1+)
# Native Windows install for server + collector (or either alone).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File install.ps1
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Mode server
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Mode collector
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Mode both -SkipScheduledTask
# ────────────────────────────────────────────────────────────────────

[CmdletBinding()]
param(
    [ValidateSet('server', 'collector', 'both', 'ask')]
    [string]$Mode = 'ask',
    [switch]$SkipScheduledTask,
    [switch]$SkipPython,
    [switch]$SkipFrontend
)

$ErrorActionPreference = 'Stop'
$Version = '0.1.0'
$InstallDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $InstallDir '.venv'
$MinPyMajor = 3
$MinPyMinor = 9

function Write-Ok    ($msg) { Write-Host "  " -NoNewline; Write-Host "OK  " -ForegroundColor Green -NoNewline; Write-Host $msg }
function Write-Warn  ($msg) { Write-Host "  " -NoNewline; Write-Host "!   " -ForegroundColor Yellow -NoNewline; Write-Host $msg }
function Write-Fail  ($msg) { Write-Host "  " -NoNewline; Write-Host "X   " -ForegroundColor Red -NoNewline; Write-Host $msg }
function Write-Info  ($msg) { Write-Host "  " -NoNewline; Write-Host "->  " -ForegroundColor Cyan -NoNewline; Write-Host $msg }
function Write-Header($msg) { Write-Host ""; Write-Host $msg -ForegroundColor White }

$script:MissingCritical = $false
$script:InstallServer = $false
$script:InstallCollector = $false
$script:InstallMode = 'dev'
$script:WheelPath = $null

# Detect install mode: a release tarball has wheels/auto_daily_log-*.whl;
# a dev checkout has the pyproject.toml and auto_daily_log/ source dir.
$wheelDir = Join-Path $InstallDir 'wheels'
if (Test-Path $wheelDir) {
    $wheels = Get-ChildItem -Path $wheelDir -Filter 'auto_daily_log-*.whl' -ErrorAction SilentlyContinue
    if ($wheels -and $wheels.Count -gt 0) {
        $script:InstallMode = 'release'
        $script:WheelPath = $wheels[0].FullName
    }
}

# ─── 0. Mode selection ─────────────────────────────────────────────
function Resolve-Mode {
    if ($Mode -eq 'ask') {
        Write-Header 'Installation mode'
        Write-Host '  1) server      — central web server + UI (usually one per team)'
        Write-Host '  2) collector   — activity collector (runs on each user machine)'
        Write-Host '  3) both        — server AND collector on this machine'
        $choice = Read-Host '  Choose [1/2/3]'
        switch ($choice) {
            '1' { $script:InstallServer = $true }
            '2' { $script:InstallCollector = $true }
            '3' { $script:InstallServer = $true; $script:InstallCollector = $true }
            default { throw "Invalid choice: $choice" }
        }
    } else {
        if ($Mode -eq 'server' -or $Mode -eq 'both') { $script:InstallServer = $true }
        if ($Mode -eq 'collector' -or $Mode -eq 'both') { $script:InstallCollector = $true }
    }
    $summary = @()
    if ($script:InstallServer)    { $summary += 'server' }
    if ($script:InstallCollector) { $summary += 'collector' }
    Write-Info "Will install: $($summary -join ' + ')"
}

# ─── 1. Python ─────────────────────────────────────────────────────
function Test-Python {
    Write-Header '1. Python'

    if ($SkipPython) {
        Write-Warn 'Skipping Python check (-SkipPython)'
        $script:PythonCmd = 'python'
        return
    }

    $candidates = @('py -3', 'python', 'python3')
    foreach ($candidate in $candidates) {
        $parts = $candidate.Split(' ')
        $exe = $parts[0]
        $cargs = if ($parts.Length -gt 1) { $parts[1..($parts.Length - 1)] } else { @() }
        try {
            $ver = & $exe @cargs -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($LASTEXITCODE -ne 0 -or -not $ver) { continue }
            $parts2 = $ver.Split('.')
            $major = [int]$parts2[0]; $minor = [int]$parts2[1]
            if ($major -gt $MinPyMajor -or ($major -eq $MinPyMajor -and $minor -ge $MinPyMinor)) {
                $script:PythonCmd = $candidate
                Write-Ok "Python $ver ($candidate)"
                return
            }
        } catch { continue }
    }

    Write-Fail "Python >= $MinPyMajor.$MinPyMinor not found"
    Write-Info 'Install: winget install Python.Python.3.12'
    Write-Info 'Or: https://www.python.org/downloads/windows/ (check "Add to PATH")'
    $script:MissingCritical = $true
}

# ─── 2. System deps (git, optionally node) ─────────────────────────
function Test-SystemDeps {
    Write-Header '2. System Dependencies'

    if (Get-Command git -ErrorAction SilentlyContinue) {
        Write-Ok 'git'
    } else {
        Write-Fail 'git — required for commit collection'
        Write-Info 'Install: winget install Git.Git'
        $script:MissingCritical = $true
    }

    if ($script:InstallServer -and -not $SkipFrontend) {
        if (Get-Command node -ErrorAction SilentlyContinue) {
            $nodeVer = node --version
            Write-Ok "Node.js $nodeVer (needed for server frontend build)"
        } else {
            Write-Warn 'Node.js not found — server frontend cannot be built'
            Write-Info 'Install: winget install OpenJS.NodeJS'
            Write-Info '(server can still start, but the Web UI will be missing until you build dist/)'
        }
    }

    Write-Ok 'Windows native APIs (GetForegroundWindow / WinRT OCR via winocr) — built-in'
}

# ─── 3. venv ───────────────────────────────────────────────────────
function New-Venv {
    Write-Header '3. Python Virtual Environment'

    if (Test-Path $VenvDir) {
        Write-Ok "Virtual environment exists at $VenvDir"
    } else {
        Write-Info 'Creating virtual environment...'
        $parts = $script:PythonCmd.Split(' ')
        $exe = $parts[0]
        $cargs = @()
        if ($parts.Length -gt 1) { $cargs += $parts[1..($parts.Length - 1)] }
        $cargs += @('-m', 'venv', $VenvDir)
        & $exe @cargs
        if ($LASTEXITCODE -ne 0) { throw "Failed to create venv" }
        Write-Ok "Created $VenvDir"
    }

    $script:VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
    $script:VenvPip    = Join-Path $VenvDir 'Scripts\pip.exe'
    if (-not (Test-Path $script:VenvPython)) { throw "venv python not found: $script:VenvPython" }
    Write-Ok "venv Python: $($script:VenvPython)"
}

# ─── 4. pip install ─────────────────────────────────────────────────
function Install-PythonDeps {
    Write-Header '4. Python Dependencies'

    Write-Info 'Upgrading pip...'
    & $script:VenvPython -m pip install --upgrade pip -q 2>&1 | Out-Null

    if ($script:InstallMode -eq 'release') {
        Write-Info "Installing from bundled wheel: $(Split-Path -Leaf $script:WheelPath)"
        & $script:VenvPip install "$($script:WheelPath)[windows]" -q
        if ($LASTEXITCODE -ne 0) { throw "pip install (wheel) failed" }
        Write-Ok 'Installed auto-daily-log[windows] (release mode)'
    } else {
        Write-Info 'Installing editable source with [windows] extras...'
        Push-Location $InstallDir
        try {
            & $script:VenvPip install -e '.[windows]' -q
            if ($LASTEXITCODE -ne 0) { throw "pip install -e failed" }
            Write-Ok 'Installed auto-daily-log[windows] (dev mode)'
        } finally {
            Pop-Location
        }
    }
}

# ─── 5. Frontend build (server mode only) ──────────────────────────
function Build-Frontend {
    if (-not $script:InstallServer) { return }
    Write-Header '5. Frontend'
    if ($script:InstallMode -eq 'release') {
        Write-Ok 'Frontend ships inside the wheel — no build needed'
        return
    }
    if ($SkipFrontend) { Write-Warn 'Skipped (-SkipFrontend)'; return }

    $feDir = Join-Path $InstallDir 'web\frontend'
    if (-not (Test-Path $feDir)) { Write-Warn 'Frontend dir missing; skipping'; return }
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
        Write-Warn 'Node.js not found; skipping frontend build'
        Write-Info 'Install Node 18+ then run: cd web\frontend; npm install; npm run build'
        return
    }

    Push-Location $feDir
    try {
        Write-Info 'Installing npm deps...'
        & npm install --silent 2>&1 | Select-Object -Last 3 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
        Write-Info 'Building frontend...'
        & npm run build 2>&1 | Select-Object -Last 3 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
        Write-Ok "Frontend built: $feDir\dist\"
    } finally {
        Pop-Location
    }
}

# ─── 6. Configs ────────────────────────────────────────────────────
function New-Configs {
    Write-Header '6. Configuration'

    if ($script:InstallServer) {
        $cfg = Join-Path $InstallDir 'config.yaml'
        $example = Join-Path $InstallDir 'config.yaml.example'
        if (Test-Path $cfg) {
            Write-Ok "config.yaml exists"
        } elseif (Test-Path $example) {
            Copy-Item $example $cfg
            Write-Ok "Copied config.yaml.example → config.yaml"
        } else {
            Write-Warn 'Neither config.yaml nor config.yaml.example found'
        }
    }

    if ($script:InstallCollector) {
        $cfg = Join-Path $InstallDir 'collector.yaml'
        $example = Join-Path $InstallDir 'collector.yaml.example'
        if (Test-Path $cfg) {
            Write-Ok "collector.yaml exists"
        } elseif (Test-Path $example) {
            $defaultUrl = if ($script:InstallServer) { 'http://127.0.0.1:8888' } else { 'http://127.0.0.1:8888' }
            $serverUrl = Read-Host "  Server URL for this collector [$defaultUrl]"
            if ([string]::IsNullOrWhiteSpace($serverUrl)) { $serverUrl = $defaultUrl }
            $name = Read-Host "  Collector display name [$env:COMPUTERNAME]"
            if ([string]::IsNullOrWhiteSpace($name)) { $name = $env:COMPUTERNAME }

            $content = Get-Content $example -Raw
            $content = $content -replace 'server_url: ".*"', "server_url: ""$serverUrl"""
            $content = $content -replace 'name: ".*"', "name: ""$name"""
            Set-Content -Path $cfg -Value $content -Encoding UTF8
            Write-Ok "Created collector.yaml (server=$serverUrl, name=$name)"
        } else {
            Write-Warn 'collector.yaml.example missing'
        }
    }
}

# ─── 7. Scheduled Tasks ─────────────────────────────────────────────
function Register-ScheduledTasks {
    if ($SkipScheduledTask) {
        Write-Header '7. Auto-Start (Scheduled Tasks)'
        Write-Warn 'Skipped (-SkipScheduledTask)'
        return
    }

    Write-Header '7. Auto-Start (Scheduled Tasks)'
    $answer = Read-Host "  Register scheduled tasks to auto-start at login? [Y/n]"
    if ($answer -match '^[nN]') { Write-Info 'Skipped'; return }

    $logDir = Join-Path $env:USERPROFILE '.auto_daily_log\logs'
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero)
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

    if ($script:InstallServer) {
        $taskName = 'AutoDailyLogServer'
        $configPath = Join-Path $InstallDir 'config.yaml'
        $action = New-ScheduledTaskAction `
            -Execute $script:VenvPython `
            -Argument "-u -m auto_daily_log --config ""$configPath"" --port 8888" `
            -WorkingDirectory $InstallDir
        try {
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
            Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
            Write-Ok "Registered task: $taskName (at login, port 8888)"
        } catch { Write-Warn "Failed to register server task: $_" }
    }

    if ($script:InstallCollector) {
        $taskName = 'AutoDailyLogCollector'
        $configPath = Join-Path $InstallDir 'collector.yaml'
        $action = New-ScheduledTaskAction `
            -Execute $script:VenvPython `
            -Argument "-u -m auto_daily_log_collector --config ""$configPath""" `
            -WorkingDirectory $InstallDir
        try {
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
            Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
            Write-Ok "Registered task: $taskName (at login)"
        } catch { Write-Warn "Failed to register collector task: $_" }
    }
}

# ─── 8. Verification ───────────────────────────────────────────────
function Invoke-Verify {
    Write-Header '8. Verification'

    $tests = @(
        @{ Name = 'aiosqlite (async DB driver)'; Code = 'import aiosqlite' },
        @{ Name = 'sqlite_vec (vector index)';   Code = 'import sqlite_vec' },
        @{ Name = 'httpx + pydantic + yaml';     Code = 'import httpx, pydantic, yaml' },
        @{ Name = 'Pillow + imagehash';          Code = 'import PIL, imagehash' }
    )
    if ($script:InstallServer) {
        $tests += @{ Name = 'Server core module';   Code = 'from auto_daily_log.app import Application' }
        $tests += @{ Name = 'FastAPI + uvicorn';    Code = 'import fastapi, uvicorn, apscheduler' }
    }
    if ($script:InstallCollector) {
        $tests += @{ Name = 'Collector module';       Code = 'import auto_daily_log_collector' }
        $tests += @{ Name = 'WindowsAdapter loads';   Code = 'from auto_daily_log_collector.platforms.windows import WindowsAdapter; WindowsAdapter()' }
    }

    $importOk = $true
    foreach ($t in $tests) {
        & $script:VenvPython -c $t.Code 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Ok $t.Name
        } else {
            Write-Fail "$($t.Name) — run: $($script:VenvPip) install -e .[windows]"
            $importOk = $false
        }
    }

    # winocr — optional
    & $script:VenvPython -c 'import winocr' 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Ok 'winocr (Windows native OCR)'
    } else {
        Write-Warn 'winocr unavailable — OCR disabled (ok if monitor.ocr_enabled=false)'
    }

    # Collector smoke-test: GetForegroundWindow actually works
    if ($script:InstallCollector) {
        $smoke = & $script:VenvPython -c "from auto_daily_log_collector.platforms.windows import WindowsAdapter; a=WindowsAdapter(); print(a.get_frontmost_app() or '<no foreground>')"
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "WindowsAdapter.get_frontmost_app() -> $smoke"
        } else {
            Write-Warn 'get_frontmost_app smoke test failed; check PowerShell permissions'
        }
    }

    if ($importOk) { Write-Host ''; Write-Ok 'All checks passed' } else { $script:MissingCritical = $true }
}

# ─── 9. Summary ────────────────────────────────────────────────────
function Show-Summary {
    Write-Header 'Done!'
    Write-Host ''
    Write-Host '  Manage via .\adl.ps1 (Windows equivalent of ./pdl):' -ForegroundColor White
    if ($script:InstallServer) {
        Write-Host '    .\adl.ps1 server start       # start the Web UI + API'
        Write-Host '    .\adl.ps1 server status'
        Write-Host '    .\adl.ps1 server logs 100'
        Write-Host '    Open http://127.0.0.1:8888'
    }
    if ($script:InstallCollector) {
        Write-Host '    .\adl.ps1 collector start    # push activity to server'
        Write-Host '    .\adl.ps1 collector status'
    }
    if ($script:InstallServer -and $script:InstallCollector) {
        Write-Host '    .\adl.ps1 start              # start both'
        Write-Host '    .\adl.ps1 status'
    }
    Write-Host ''
    Write-Host '  Rebuild after pulling code:' -ForegroundColor White
    Write-Host '    .\adl.ps1 build --restart'
    Write-Host ''
    Write-Host '  Scheduled tasks (auto-start at login):' -ForegroundColor White
    if ($script:InstallServer)    { Write-Host '    Get-ScheduledTaskInfo -TaskName AutoDailyLogServer' }
    if ($script:InstallCollector) { Write-Host '    Get-ScheduledTaskInfo -TaskName AutoDailyLogCollector' }
    Write-Host ''
}

# ─── Main ──────────────────────────────────────────────────────────
function Main {
    Write-Host ''
    Write-Host '+====================================================+' -ForegroundColor White
    Write-Host "|  Polars Daily Log Windows Installer v$Version            |" -ForegroundColor White
    Write-Host '+====================================================+' -ForegroundColor White

    Resolve-Mode
    Write-Info "Install mode: $($script:InstallMode)"
    Test-Python
    Test-SystemDeps

    if ($script:MissingCritical) {
        Write-Host ''
        Write-Fail 'Cannot continue — fix missing dependencies above first'
        exit 1
    }

    New-Venv
    Install-PythonDeps
    Build-Frontend
    New-Configs
    Register-ScheduledTasks
    Invoke-Verify
    Show-Summary
}

Main
