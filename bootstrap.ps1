# ─── Polars Daily Log — Windows One-liner Bootstrap ──────────────────────
#
# 用法：
#   irm https://raw.githubusercontent.com/Conner2077/polars-daily-log/master/bootstrap.ps1 | iex
#
# 可选环境变量（在调用前设置）：
#   $env:PDL_VERSION        指定版本（默认 latest，拉最新 GitHub Release）
#   $env:PDL_INSTALL_DIR    安装目录（默认 $HOME\.polars-daily-log）
#   $env:PDL_ROLE           server / collector / both / ask（默认 ask，透传给 install.ps1）
#   $env:PDL_SERVER_URL     collector 指向的 server URL（非交互模式）
#   $env:PDL_COLLECTOR_NAME collector 的展示名（非交互模式）
#
# 示例：
#   # 装最新版，交互选角色
#   irm https://.../bootstrap.ps1 | iex
#
#   # 非交互 collector 安装
#   $env:PDL_ROLE='collector'; $env:PDL_SERVER_URL='http://192.168.1.10:8888'; $env:PDL_COLLECTOR_NAME='my-laptop'
#   irm https://.../bootstrap.ps1 | iex
# ─────────────────────────────────────────────────────────────────────────

$ErrorActionPreference = 'Stop'

$Repo       = 'Conner2077/polars-daily-log'
$InstallDir = if ($env:PDL_INSTALL_DIR) { $env:PDL_INSTALL_DIR } else { Join-Path $HOME '.polars-daily-log' }
$Version    = if ($env:PDL_VERSION)     { $env:PDL_VERSION }     else { 'latest' }

# ─── Helpers ─────────────────────────────────────────────────────────
function Write-Ok    ($msg) { Write-Host "  ✓ " -ForegroundColor Green -NoNewline; Write-Host $msg }
function Write-Warn  ($msg) { Write-Host "  ! " -ForegroundColor Yellow -NoNewline; Write-Host $msg }
function Write-Fail  ($msg) { Write-Host "  ✗ " -ForegroundColor Red -NoNewline; Write-Host $msg }
function Write-Info  ($msg) { Write-Host "  → " -ForegroundColor Cyan -NoNewline; Write-Host $msg }
function Write-Header($msg) { Write-Host "`n$msg" -ForegroundColor White }

# ─── 0. 前置检查 ─────────────────────────────────────────────────────
function Test-Prerequisites {
    Write-Header "0. Bootstrap prerequisites"

    # PowerShell 5.1+ required
    if ($PSVersionTable.PSVersion.Major -lt 5) {
        Write-Fail "PowerShell 5.1+ required (current: $($PSVersionTable.PSVersion))"
        exit 1
    }
    Write-Ok "PowerShell $($PSVersionTable.PSVersion)"

    # tar (built into Windows 10 1803+)
    if (-not (Get-Command tar -ErrorAction SilentlyContinue)) {
        Write-Fail "tar not found — requires Windows 10 1803+ or install bsdtar"
        exit 1
    }
    Write-Ok "tar available"
}

# ─── 1. 解析版本 ─────────────────────────────────────────────────────
function Resolve-Version {
    Write-Header "1. Resolve version"

    if ($script:Version -eq 'latest') {
        Write-Info "Querying latest release..."
        $apiUrl = "https://api.github.com/repos/$Repo/releases/latest"
        try {
            # Use TLS 1.2 (GitHub requires it)
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            $resp = Invoke-RestMethod -Uri $apiUrl -UseBasicParsing -ErrorAction Stop
            $tag = $resp.tag_name -replace '^v', ''
        } catch {
            Write-Fail "Cannot query latest release: $_"
            Write-Info "Set `$env:PDL_VERSION = '0.5.1' and retry"
            exit 1
        }
        if (-not $tag) {
            Write-Fail "Cannot parse version from GitHub API"
            exit 1
        }
        $script:Version = $tag
    } else {
        $script:Version = $script:Version -replace '^v', ''
    }

    Write-Ok "Version: $($script:Version)"
}

# ─── 2. 下载并解压 ──────────────────────────────────────────────────
function Get-AndExtract {
    Write-Header "2. Download tarball"

    $tarball = "polars-daily-log-$($script:Version).tar.gz"
    $url     = "https://github.com/$Repo/releases/download/v$($script:Version)/$tarball"
    $tmpDir  = Join-Path $env:TEMP "pdl-bootstrap-$(Get-Random)"
    New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null
    $tmpFile = Join-Path $tmpDir $tarball

    Write-Info "Downloading from $url ..."
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        # Use .NET WebClient for progress + speed
        $wc = New-Object System.Net.WebClient
        $wc.DownloadFile($url, $tmpFile)
    } catch {
        Write-Fail "Download failed — check version v$($script:Version) exists: https://github.com/$Repo/releases"
        exit 1
    }
    $size = [math]::Round((Get-Item $tmpFile).Length / 1KB)
    Write-Ok "Downloaded (${size} KB)"

    # Handle existing install
    if (Test-Path $InstallDir) {
        $versionFile = Join-Path $InstallDir 'VERSION'
        if (Test-Path $versionFile) {
            $existing = Get-Content $versionFile -ErrorAction SilentlyContinue
            Write-Warn "$InstallDir exists (v$existing) — upgrading in-place"
            # Stop running services
            $pdl = Join-Path $InstallDir 'pdl'
            if (Test-Path $pdl) {
                try { & $pdl stop 2>$null } catch {}
            }
        } else {
            Write-Fail "$InstallDir exists but doesn't look like a pdl install — aborting"
            exit 1
        }
    } else {
        New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
        Write-Ok "Created install directory: $InstallDir"
    }

    Write-Info "Extracting to $InstallDir ..."
    tar -xzf $tmpFile -C $InstallDir --strip-components=1
    Write-Ok "Extracted"

    # Cleanup temp
    Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue
}

# ─── 3. 运行 install.ps1 ────────────────────────────────────────────
function Invoke-Installer {
    Write-Header "3. Running install.ps1"

    $installerPath = Join-Path $InstallDir 'install.ps1'
    if (-not (Test-Path $installerPath)) {
        Write-Fail "install.ps1 not found in tarball — corrupted release?"
        exit 1
    }

    # Build arguments — only pass what the user set
    $args = @()
    $role = $env:PDL_ROLE
    if ($role) { $args += "-Mode", $role }

    Set-Location $InstallDir
    & powershell -ExecutionPolicy Bypass -File $installerPath @args
}

# ─── 4. 完成提示 ─────────────────────────────────────────────────────
function Show-Next {
    Write-Header "Done!"
    Write-Host ""
    Write-Host "  Install location: $InstallDir" -ForegroundColor White
    Write-Host ""
    Write-Host "  Add to PATH (run once):"
    Write-Host "    `$env:Path += `";$InstallDir`""
    Write-Host "    [Environment]::SetEnvironmentVariable('Path', `$env:Path, 'User')"
    Write-Host ""
    Write-Host "  Then:"
    Write-Host "    pdl server start            # or: pdl collector start / pdl start"
    Write-Host "    Open http://127.0.0.1:8888"
    Write-Host ""
    Write-Host "  Upgrade: re-run this bootstrap (overwrites in-place)" -ForegroundColor DarkGray
    Write-Host ""
}

# ─── Main ────────────────────────────────────────────────────────────
function Main {
    Write-Host ""
    Write-Host "╔════════════════════════════════════════════════╗" -ForegroundColor White
    Write-Host "║  Polars Daily Log — Windows Bootstrap         ║" -ForegroundColor White
    Write-Host "╚════════════════════════════════════════════════╝" -ForegroundColor White

    Test-Prerequisites
    Resolve-Version
    Get-AndExtract
    Invoke-Installer
    Show-Next
}

Main
