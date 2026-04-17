# ─── Polars Daily Log — Windows Installer ──────────────────────────
# Supports: Windows 10 / 11 (PowerShell 5.1+)
# Native Windows install for server + collector (or either alone).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File install.ps1
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Mode server
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Mode collector
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Mode both -SkipScheduledTask
#
# All interactive prompts use [Console]::ReadLine() / Read-Host which read
# from the console host, NOT stdin — so piping via `irm ... | iex` works.
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
$InstallDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $InstallDir '.venv'
$DataDir = Join-Path $env:USERPROFILE '.auto_daily_log'
$MinPyMajor = 3
$MinPyMinor = 9

# Dynamic version: read from VERSION file (release) or pyproject.toml (dev).
$Version = 'unknown'
$versionFile = Join-Path $InstallDir 'VERSION'
$pyprojectFile = Join-Path $InstallDir 'pyproject.toml'
if (Test-Path $versionFile) {
    $Version = (Get-Content $versionFile -Raw).Trim()
} elseif (Test-Path $pyprojectFile) {
    $m = Select-String -Path $pyprojectFile -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
    if ($m) { $Version = $m.Matches[0].Groups[1].Value }
}

# PyPI mirror (default aliyun for China; override via $env:PDL_PIP_INDEX_URL)
$PipMirror = if ($env:PDL_PIP_INDEX_URL) { $env:PDL_PIP_INDEX_URL } else { 'https://mirrors.aliyun.com/pypi/simple/' }
$PipHost = ([Uri]$PipMirror).Host

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

# Detect install mode
$wheelDir = Join-Path $InstallDir 'wheels'
if (Test-Path $wheelDir) {
    $wheels = Get-ChildItem -Path $wheelDir -Filter 'auto_daily_log-*.whl' -ErrorAction SilentlyContinue
    if ($wheels -and $wheels.Count -gt 0) {
        $script:InstallMode = 'release'
        $script:WheelPath = $wheels[0].FullName
    }
}

# ─── 1. Mode selection ─────────────────────────────────────────────
function Resolve-Mode {
    Write-Header '1. What are you installing?'
    if ($Mode -eq 'ask') {
        Write-Host '  1) server      - central web server + UI'
        Write-Host '  2) collector   - activity collector (one per machine)'
        Write-Host '  3) both        - server AND collector on this machine'
        $choice = Read-Host '  Choose [1/2/3]'
        switch ($choice) {
            '1' { $script:InstallServer = $true }
            '2' { $script:InstallCollector = $true }
            '3' { $script:InstallServer = $true; $script:InstallCollector = $true }
            default {
                Write-Warn "Invalid choice '$choice' - defaulting to 'both'"
                $script:InstallServer = $true; $script:InstallCollector = $true
            }
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

# ─── 2. Python ─────────────────────────────────────────────────────
function Test-Python {
    Write-Header '2. Python'

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

# ─── 3. System deps ───────────────────────────────────────────────
function Test-SystemDeps {
    Write-Header '3. System Dependencies'

    if (Get-Command git -ErrorAction SilentlyContinue) {
        Write-Ok 'git'
    } else {
        Write-Fail 'git - required for commit collection'
        Write-Info 'Install: winget install Git.Git'
        $script:MissingCritical = $true
    }

    if ($script:InstallServer -and -not $SkipFrontend) {
        if (Get-Command node -ErrorAction SilentlyContinue) {
            $nodeVer = node --version
            Write-Ok "Node.js $nodeVer (needed for dev frontend build)"
        } else {
            Write-Warn 'Node.js not found (ok for release installs; needed for dev builds)'
        }
    }

    Write-Ok 'Windows native APIs (GetForegroundWindow / WinRT OCR via winocr) - built-in'
}

# ─── 4. venv ───────────────────────────────────────────────────────
function New-Venv {
    Write-Header '4. Python Virtual Environment'

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
    if (-not (Test-Path $script:VenvPython)) { throw "venv python not found: $($script:VenvPython)" }
    Write-Ok "venv Python: $($script:VenvPython)"
}

# ─── 5. pip install ─────────────────────────────────────────────────
function Install-PythonDeps {
    Write-Header '5. Python Dependencies'

    Write-Info "PyPI mirror: $PipMirror"
    & $script:VenvPython -m pip install --upgrade pip -q -i $PipMirror --trusted-host $PipHost 2>&1 | Out-Null

    if ($script:InstallMode -eq 'release') {
        Write-Info "Installing from bundled wheel: $(Split-Path -Leaf $script:WheelPath)"
        & $script:VenvPip install "$($script:WheelPath)[windows]" -q -i $PipMirror --trusted-host $PipHost
        if ($LASTEXITCODE -ne 0) { throw "pip install (wheel) failed" }
        Write-Ok 'Installed auto-daily-log[windows] (release mode)'
    } else {
        Write-Info 'Installing editable source with [windows] extras...'
        Push-Location $InstallDir
        try {
            & $script:VenvPip install -e '.[windows]' -q -i $PipMirror --trusted-host $PipHost
            if ($LASTEXITCODE -ne 0) { throw "pip install -e failed" }
            Write-Ok 'Installed auto-daily-log[windows] (dev mode)'
        } finally {
            Pop-Location
        }
    }
}

# ─── 6. Frontend build (server mode only) ──────────────────────────
function Build-Frontend {
    if (-not $script:InstallServer) { return }
    Write-Header '6. Frontend'
    if ($script:InstallMode -eq 'release') {
        Write-Ok 'Frontend ships inside the wheel - no build needed'
        return
    }
    if ($SkipFrontend) { Write-Warn 'Skipped (-SkipFrontend)'; return }

    $feDir = Join-Path $InstallDir 'web\frontend'
    if (-not (Test-Path $feDir)) { Write-Warn 'Frontend dir missing; skipping'; return }
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
        Write-Warn 'Node.js not found; skipping frontend build'
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

# ─── 7. Data directory & configs ──────────────────────────────────
function New-Configs {
    Write-Header '7. Data & Config'

    # Data directory
    if (-not (Test-Path $DataDir)) {
        New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
        Write-Ok "Created data directory: $DataDir"
    } else {
        Write-Ok "Data directory exists: $DataDir"
    }

    # Server config
    if ($script:InstallServer) {
        $cfg = Join-Path $InstallDir 'config.yaml'
        $example = Join-Path $InstallDir 'config.yaml.example'
        if (Test-Path $cfg) {
            Write-Ok "config.yaml exists"
        } elseif (Test-Path $example) {
            Copy-Item $example $cfg
            Write-Ok "Copied config.yaml.example -> config.yaml"
        } else {
            Write-Warn 'config.yaml.example not found'
        }
    }

    # Collector config (separate block, no early-return that could skip server)
    if ($script:InstallCollector) {
        $cfg = Join-Path $InstallDir 'collector.yaml'
        $example = Join-Path $InstallDir 'collector.yaml.example'
        if (Test-Path $cfg) {
            Write-Ok "collector.yaml exists"
        } elseif (Test-Path $example) {
            $defaultUrl = 'http://127.0.0.1:8888'
            $defaultName = $env:COMPUTERNAME

            $serverUrl = if ($env:PDL_SERVER_URL) {
                Write-Info "Server URL: $($env:PDL_SERVER_URL) (from PDL_SERVER_URL)"
                $env:PDL_SERVER_URL
            } else {
                $input = Read-Host "  Server URL [$defaultUrl]"
                if ([string]::IsNullOrWhiteSpace($input)) { $defaultUrl } else { $input }
            }
            $name = if ($env:PDL_COLLECTOR_NAME) {
                Write-Info "Collector name: $($env:PDL_COLLECTOR_NAME) (from PDL_COLLECTOR_NAME)"
                $env:PDL_COLLECTOR_NAME
            } else {
                $input = Read-Host "  Collector display name [$defaultName]"
                if ([string]::IsNullOrWhiteSpace($input)) { $defaultName } else { $input }
            }

            # Use Python yaml to avoid regex escaping pitfalls.
            try {
                & $script:VenvPython -c @"
import sys, yaml
with open(r'$example') as f:
    cfg = yaml.safe_load(f)
cfg['server_url'] = sys.argv[1]
cfg['name'] = sys.argv[2]
with open(r'$cfg', 'w') as f:
    yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
"@ $serverUrl $name 2>$null
                if ($LASTEXITCODE -ne 0) { throw "yaml fallback" }
            } catch {
                # Fallback: literal string replace (not regex)
                $content = Get-Content $example -Raw
                $content = $content.Replace('server_url: "http://127.0.0.1:8888"', "server_url: ""$serverUrl""")
                $content = $content.Replace('name: "My-Mac"', "name: ""$name""")
                Set-Content -Path $cfg -Value $content -Encoding UTF8
            }
            Write-Ok "Created collector.yaml (server=$serverUrl, name=$name)"
        } else {
            Write-Warn 'collector.yaml.example missing'
        }
    }
}

# ─── 8. Built-in LLM (optional, passphrase-protected) ─────────────
function Install-BuiltinLLM {
    if (-not $script:InstallServer) { return }
    $encFile = Join-Path $InstallDir 'builtin_llm.enc'
    if (-not (Test-Path $encFile)) { return }

    Write-Header '8. Built-in LLM (optional)'

    # Need openssl — Git for Windows bundles it
    $opensslCmd = $null
    $gitDir = (Get-Command git -ErrorAction SilentlyContinue).Source | Split-Path | Split-Path
    $gitOpenssl = Join-Path $gitDir 'usr\bin\openssl.exe'
    if (Test-Path $gitOpenssl) {
        $opensslCmd = $gitOpenssl
    } elseif (Get-Command openssl -ErrorAction SilentlyContinue) {
        $opensslCmd = 'openssl'
    }

    if (-not $opensslCmd) {
        Write-Warn 'openssl not found (Git for Windows usually provides it) - skipping'
        return
    }

    $passphrase = $env:PDL_BUILTIN_PASSPHRASE
    if (-not $passphrase) {
        Write-Host '  If the author gave you a passphrase, enter it to auto-configure LLM.'
        Write-Host '  Press Enter to skip.'
        $passphrase = Read-Host '  Passphrase'
    } else {
        Write-Info 'Using PDL_BUILTIN_PASSPHRASE env var'
    }

    if ([string]::IsNullOrWhiteSpace($passphrase)) {
        Write-Info 'Skipped - configure LLM later in Settings page'
        return
    }

    $target = Join-Path $DataDir 'builtin.key'
    try {
        & $opensslCmd enc -d -aes-256-cbc -pbkdf2 -iter 100000 -base64 `
            -in $encFile -out $target -pass "pass:$passphrase" 2>$null
        if ($LASTEXITCODE -ne 0) { throw "decrypt failed" }
        # Validate JSON
        & $script:VenvPython -c "import json,sys; json.load(open(sys.argv[1]))" $target 2>$null
        if ($LASTEXITCODE -ne 0) { throw "invalid json" }
        Write-Ok "Built-in LLM configured -> $target"
    } catch {
        Remove-Item $target -ErrorAction SilentlyContinue
        Write-Warn 'Wrong passphrase - skipped (re-run install.ps1 to retry)'
    }
}

# ─── 9. Scheduled Tasks ───────────────────────────────────────────
function Register-ScheduledTasks {
    if ($SkipScheduledTask) {
        Write-Header '9. Auto-Start (Scheduled Tasks)'
        Write-Warn 'Skipped (-SkipScheduledTask)'
        return
    }

    Write-Header '9. Auto-Start (Scheduled Tasks)'
    $answer = Read-Host "  Register scheduled tasks to auto-start at login? [Y/n]"
    if ($answer -match '^[nN]') { Write-Info 'Skipped'; return }

    $logDir = Join-Path $DataDir 'logs'
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

# ─── 10. Verification ─────────────────────────────────────────────
function Invoke-Verify {
    Write-Header '10. Verification'

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
            Write-Fail "$($t.Name) - run: $($script:VenvPip) install -e .[windows]"
            $importOk = $false
        }
    }

    # winocr — optional
    & $script:VenvPython -c 'import winocr' 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Ok 'winocr (Windows native OCR)'
    } else {
        Write-Warn 'winocr unavailable - OCR disabled (ok if monitor.ocr_enabled=false)'
    }

    if ($importOk) { Write-Host ''; Write-Ok 'All checks passed' } else { $script:MissingCritical = $true }
}

# ─── 11. Summary & auto-start ─────────────────────────────────────
function Show-Summary {
    Write-Header 'Done!'
    Write-Host ''
    Write-Host '  Next steps (via .\pdl):' -ForegroundColor White
    if ($script:InstallServer) {
        Write-Host '    .\pdl server start             # start the Web UI + API'
        Write-Host '    Open http://127.0.0.1:8888'
    }
    if ($script:InstallCollector) {
        Write-Host '    .\pdl collector start           # push activity to server'
    }
    if ($script:InstallServer -and $script:InstallCollector) {
        Write-Host '    .\pdl start                     # start both'
    }
    Write-Host ''

    # Offer auto-start
    $pdlPath = Join-Path $InstallDir 'pdl'
    if (Test-Path $pdlPath) {
        $answer = Read-Host '  Start now? [Y/n]'
        if (-not ($answer -match '^[nN]')) {
            Write-Host ''
            $startCmd = if ($script:InstallServer -and $script:InstallCollector) { 'start' }
                        elseif ($script:InstallServer) { 'server start' }
                        else { 'collector start' }
            try {
                & $pdlPath $startCmd.Split(' ')
            } catch {
                Write-Warn "Failed to start - check logs: .\pdl server logs"
            }
        }
    }
}

# ─── Main ──────────────────────────────────────────────────────────
function Main {
    Write-Host ''
    Write-Host "+==============================================+" -ForegroundColor White
    Write-Host "|  Polars Daily Log Windows Installer v$Version" -ForegroundColor White
    Write-Host "+==============================================+" -ForegroundColor White

    Resolve-Mode
    Write-Info "Install mode: $($script:InstallMode) | Platform: Windows"
    Test-Python
    Test-SystemDeps

    if ($script:MissingCritical) {
        Write-Host ''
        Write-Fail 'Cannot continue - fix missing dependencies above first'
        exit 1
    }

    New-Venv
    Install-PythonDeps
    Build-Frontend
    New-Configs
    Install-BuiltinLLM
    Register-ScheduledTasks
    Invoke-Verify
    Show-Summary
}

Main
