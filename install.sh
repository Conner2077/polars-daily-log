#!/usr/bin/env bash
set -euo pipefail

# ─── Polars Daily Log Installer ─────────────────────────────────────
# Supports: macOS (Intel/Apple Silicon), Linux (Debian/Ubuntu/Fedora/Arch)
# Usage:    bash install.sh
#
# This script is shipped inside the release tarball AND in the source repo.
# All interactive reads use `< /dev/tty` so the script works when piped
# from bootstrap.sh via `curl ... | bash` (stdin is the curl stream).
# ─────────────────────────────────────────────────────────────────────

APP_NAME="auto-daily-log"
DATA_DIR="$HOME/.auto_daily_log"
INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$INSTALL_DIR/.venv"
MIN_PYTHON="3.9"

# Dynamic version: read from VERSION file (release tarball) or pyproject.toml (dev).
if [ -f "$INSTALL_DIR/VERSION" ]; then
    VERSION="$(cat "$INSTALL_DIR/VERSION")"
elif [ -f "$INSTALL_DIR/pyproject.toml" ]; then
    VERSION="$(grep -E '^version\s*=' "$INSTALL_DIR/pyproject.toml" | head -1 | cut -d'"' -f2)"
else
    VERSION="unknown"
fi

# Role selection (server / collector / both / ask)
# Override via env: PDL_ROLE=collector PDL_SERVER_URL=http://... PDL_COLLECTOR_NAME=foo bash install.sh
ROLE="${PDL_ROLE:-ask}"
SERVER_URL_INPUT="${PDL_SERVER_URL:-}"
COLLECTOR_NAME_INPUT="${PDL_COLLECTOR_NAME:-}"
INSTALL_SERVER=0
INSTALL_COLLECTOR=0

# ─── Colors ──────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}!${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }
info() { echo -e "  ${BLUE}→${NC} $1"; }
header() { echo -e "\n${BOLD}$1${NC}"; }

# Helper: read from tty with fallback to default when no tty available.
# Usage: tty_read "prompt" DEFAULT_VAR
#   Sets REPLY to user input or the default.
tty_read() {
    local prompt="$1" default="${2:-}"
    if [[ -r /dev/tty ]]; then
        read -rp "$prompt" REPLY < /dev/tty || REPLY=""
    else
        REPLY=""
    fi
    [[ -z "$REPLY" ]] && REPLY="$default"
}

# ─── Resolve role (server / collector / both) ───────────────────────
resolve_role() {
    header "1. What are you installing?"

    case "$ROLE" in
        server|collector|both) : ;;
        ask|"")
            echo "  1) server      — central web server + UI"
            echo "  2) collector   — activity collector (one per machine)"
            echo "  3) both        — server AND collector on this machine"
            local choice=""
            while [[ ! "$choice" =~ ^[123]$ ]]; do
                tty_read "  Choose [1/2/3]: "
                choice="$REPLY"
                if [[ -z "$choice" ]]; then
                    # No tty available — default to 'both'
                    warn "No tty — defaulting to 'both' (server + collector)"
                    choice="3"
                fi
            done
            case "$choice" in
                1) ROLE="server" ;;
                2) ROLE="collector" ;;
                3) ROLE="both" ;;
            esac
            ;;
        *) fail "Unknown PDL_ROLE: $ROLE (must be server/collector/both/ask)"; exit 1 ;;
    esac

    [[ "$ROLE" == "server"    || "$ROLE" == "both" ]] && INSTALL_SERVER=1
    [[ "$ROLE" == "collector" || "$ROLE" == "both" ]] && INSTALL_COLLECTOR=1

    local summary=""
    (( INSTALL_SERVER    )) && summary+="server "
    (( INSTALL_COLLECTOR )) && summary+="collector"
    info "Will install: $summary"
}

# ─── Detect Platform ─────────────────────────────────────────────────
detect_platform() {
    local uname_s
    uname_s="$(uname -s)"
    case "$uname_s" in
        Darwin) PLATFORM="macos" ;;
        Linux)  PLATFORM="linux" ;;
        *)      echo -e "${RED}Unsupported platform: $uname_s${NC}"; exit 1 ;;
    esac

    if [ "$PLATFORM" = "linux" ]; then
        if command -v apt-get &>/dev/null; then
            PKG_MGR="apt"
            PKG_INSTALL="sudo apt-get install -y"
        elif command -v dnf &>/dev/null; then
            PKG_MGR="dnf"
            PKG_INSTALL="sudo dnf install -y"
        elif command -v pacman &>/dev/null; then
            PKG_MGR="pacman"
            PKG_INSTALL="sudo pacman -S --noconfirm"
        elif command -v yum &>/dev/null; then
            PKG_MGR="yum"
            PKG_INSTALL="sudo yum install -y"
        else
            PKG_MGR="unknown"
            PKG_INSTALL=""
        fi
    else
        if command -v brew &>/dev/null; then
            PKG_MGR="brew"
            PKG_INSTALL="brew install"
        else
            PKG_MGR="none"
            PKG_INSTALL=""
        fi
    fi
}

# ─── Check Python ────────────────────────────────────────────────────
check_python() {
    header "2. Python"

    PYTHON_CMD=""
    for cmd in python3 python; do
        if command -v "$cmd" &>/dev/null; then
            local ver
            ver="$($cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")"
            local major minor
            major="${ver%%.*}"
            minor="${ver#*.}"
            if [ "$major" -ge 3 ] && [ "$minor" -ge 9 ]; then
                PYTHON_CMD="$cmd"
                ok "Python $ver ($cmd)"
                return
            fi
        fi
    done

    fail "Python >= $MIN_PYTHON not found"
    if [ "$PLATFORM" = "macos" ]; then
        info "Install: brew install python3"
    else
        info "Install: $PKG_INSTALL python3 python3-venv python3-pip"
    fi
    MISSING_CRITICAL=1
}

# ─── Check System Dependencies ───────────────────────────────────────
check_sys_deps() {
    header "3. System Dependencies"

    MISSING_DEPS=()
    OPTIONAL_MISSING=()

    if [ "$PLATFORM" = "macos" ]; then
        check_dep_required "git" "git"
        ok "macOS native APIs (AppleScript, Vision OCR) — built-in"
    else
        check_dep_required "git" "git"
        check_dep_required "xdotool" "xdotool"

        check_dep_optional "xprop" "x11-utils"
        check_dep_optional "xprintidle" "xprintidle"

        local has_screenshot=0
        for tool in gnome-screenshot scrot maim import; do
            if command -v "$tool" &>/dev/null; then
                has_screenshot=1
                break
            fi
        done
        if [ "$has_screenshot" -eq 1 ]; then
            ok "Screenshot tool found ($tool)"
        else
            warn "No screenshot tool found (need one of: gnome-screenshot, scrot, maim, imagemagick)"
            OPTIONAL_MISSING+=("scrot")
        fi

        check_dep_optional "tesseract" "tesseract-ocr"
    fi
}

check_dep_required() {
    local cmd="$1" pkg="$2"
    if command -v "$cmd" &>/dev/null; then
        ok "$cmd"
    else
        fail "$cmd — required"
        MISSING_DEPS+=("$pkg")
    fi
}

check_dep_optional() {
    local cmd="$1" pkg="$2"
    if command -v "$cmd" &>/dev/null; then
        ok "$cmd"
    else
        warn "$cmd — optional, recommended"
        OPTIONAL_MISSING+=("$pkg")
    fi
}

# ─── Install Missing System Deps ─────────────────────────────────────
install_sys_deps() {
    local all_missing=("${MISSING_DEPS[@]}" "${OPTIONAL_MISSING[@]}")
    if [ "${#all_missing[@]}" -eq 0 ]; then
        return
    fi

    header "4. Install System Dependencies"

    if [ -z "$PKG_INSTALL" ]; then
        warn "No package manager detected. Please install manually:"
        for dep in "${all_missing[@]}"; do
            info "  - $dep"
        done
        if [ "${#MISSING_DEPS[@]}" -gt 0 ]; then
            MISSING_CRITICAL=1
        fi
        return
    fi

    echo ""
    info "Will install: ${all_missing[*]}"
    tty_read "  Proceed? [Y/n] " "Y"
    if [[ "$REPLY" =~ ^[Yy] ]]; then
        local pkgs=()
        for dep in "${all_missing[@]}"; do
            pkgs+=("$(map_pkg_name "$dep")")
        done
        $PKG_INSTALL "${pkgs[@]}" || warn "Some packages failed to install"
    else
        if [ "${#MISSING_DEPS[@]}" -gt 0 ]; then
            warn "Skipped required deps — app may not work correctly"
        fi
    fi
}

map_pkg_name() {
    local dep="$1"
    case "$PKG_MGR" in
        apt)
            case "$dep" in
                tesseract-ocr) echo "tesseract-ocr" ;;
                x11-utils)     echo "x11-utils" ;;
                scrot)         echo "scrot" ;;
                *)             echo "$dep" ;;
            esac
            ;;
        dnf|yum)
            case "$dep" in
                tesseract-ocr) echo "tesseract" ;;
                x11-utils)     echo "xorg-x11-utils" ;;
                xprintidle)    echo "xprintidle" ;;
                scrot)         echo "scrot" ;;
                *)             echo "$dep" ;;
            esac
            ;;
        pacman)
            case "$dep" in
                tesseract-ocr) echo "tesseract" ;;
                x11-utils)     echo "xorg-xprop" ;;
                xprintidle)    echo "xprintidle" ;;
                scrot)         echo "scrot" ;;
                *)             echo "$dep" ;;
            esac
            ;;
        *)
            echo "$dep"
            ;;
    esac
}

# ─── Setup Python Virtual Env ────────────────────────────────────────
setup_venv() {
    header "5. Python Virtual Environment"

    if [ -z "$PYTHON_CMD" ]; then
        fail "Skipped — Python not available"
        return 1
    fi

    if [ -d "$VENV_DIR" ]; then
        ok "Virtual environment exists at $VENV_DIR"
    else
        info "Creating virtual environment..."
        $PYTHON_CMD -m venv "$VENV_DIR"
        ok "Created $VENV_DIR"
    fi

    source "$VENV_DIR/bin/activate"
    ok "Activated venv ($(python3 --version))"
}

# ─── Detect install mode (dev vs release) ───────────────────────────
detect_install_mode() {
    if compgen -G "$INSTALL_DIR/wheels/auto_daily_log-*.whl" > /dev/null; then
        INSTALL_MODE="release"
        WHEEL_PATH="$(ls "$INSTALL_DIR/wheels/"auto_daily_log-*.whl | head -1)"
    elif [ -d "$INSTALL_DIR/auto_daily_log" ] && [ -f "$INSTALL_DIR/pyproject.toml" ]; then
        INSTALL_MODE="dev"
    else
        fail "Can't determine install mode — no wheels/ and no source tree"
        exit 1
    fi
}

# ─── Install Python Dependencies ─────────────────────────────────────
# Use Aliyun PyPI mirror by default for faster downloads in China.
# Override via env: PDL_PIP_INDEX_URL=https://your.mirror/simple/
PIP_MIRROR="${PDL_PIP_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple/}"
PIP_HOST="$(echo "$PIP_MIRROR" | sed -E 's|https?://([^/]+).*|\1|')"

install_python_deps() {
    header "6. Python Dependencies"

    info "PyPI mirror: $PIP_MIRROR"
    pip install --upgrade pip -q -i "$PIP_MIRROR" --trusted-host "$PIP_HOST" 2>/dev/null
    if [ "$INSTALL_MODE" = "release" ]; then
        info "Installing from bundled wheel: $(basename "$WHEEL_PATH")"
        pip install "$WHEEL_PATH[$PLATFORM]" -q -i "$PIP_MIRROR" --trusted-host "$PIP_HOST" 2>&1 | tail -3
        ok "Installed auto-daily-log[$PLATFORM] (release mode)"
    else
        info "Installing editable source + $PLATFORM dependencies..."
        pip install -e ".[$PLATFORM]" -q -i "$PIP_MIRROR" --trusted-host "$PIP_HOST" 2>&1 | tail -3
        ok "Installed auto-daily-log[$PLATFORM] (dev mode)"
    fi
}

# ─── Setup Data Directory & Config ────────────────────────────────────
setup_data() {
    header "7. Data & Config"

    if [ ! -d "$DATA_DIR" ]; then
        mkdir -p "$DATA_DIR"
        ok "Created data directory: $DATA_DIR"
    else
        ok "Data directory exists: $DATA_DIR"
    fi

    # --- Server config ---
    if (( INSTALL_SERVER )); then
        local config_dest="$INSTALL_DIR/config.yaml"
        if [ -f "$config_dest" ]; then
            ok "Server config exists: $config_dest"
        elif [ -f "$INSTALL_DIR/config.yaml.example" ]; then
            cp "$INSTALL_DIR/config.yaml.example" "$config_dest"
            ok "Created $config_dest from template"
            info "Edit to customize, or do it later via Web UI Settings"
        else
            warn "config.yaml.example not found — server may not start without config.yaml"
        fi
    fi

    # --- Collector config (separate block, no early-return that could skip server) ---
    if (( INSTALL_COLLECTOR )); then
        local coll_dest="$INSTALL_DIR/collector.yaml"
        if [ -f "$coll_dest" ]; then
            ok "Collector config exists: $coll_dest"
        elif [[ -f "$INSTALL_DIR/collector.yaml.example" ]]; then
            local default_url="http://127.0.0.1:8888"
            local default_name
            default_name="$(hostname -s 2>/dev/null || hostname)"

            local server_url="$SERVER_URL_INPUT"
            local name="$COLLECTOR_NAME_INPUT"
            if [[ -z "$server_url" ]]; then
                tty_read "  Server URL [$default_url]: " "$default_url"
                server_url="$REPLY"
            else
                info "Server URL: $server_url (from PDL_SERVER_URL)"
            fi
            if [[ -z "$name" ]]; then
                tty_read "  Collector display name [$default_name]: " "$default_name"
                name="$REPLY"
            else
                info "Collector name: $name (from PDL_COLLECTOR_NAME)"
            fi

            # Write collector.yaml via Python to avoid sed escaping pitfalls.
            python3 -c "
import sys, yaml
with open('$INSTALL_DIR/collector.yaml.example') as f:
    cfg = yaml.safe_load(f)
cfg['server_url'] = sys.argv[1]
cfg['name'] = sys.argv[2]
with open('$coll_dest', 'w') as f:
    yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
" "$server_url" "$name" 2>/dev/null || {
                # Fallback: sed if pyyaml not available yet (rare — pip step ran first)
                sed -e "s|^server_url:.*|server_url: \"$server_url\"|" \
                    -e "s|^name:.*|name: \"$name\"|" \
                    "$INSTALL_DIR/collector.yaml.example" > "$coll_dest"
            }
            ok "Created collector.yaml (server=$server_url, name=$name)"
        else
            warn "collector.yaml.example not found — cannot auto-generate collector.yaml"
        fi
    fi
}

# ─── Built-in LLM (optional, passphrase-protected) ───────────────────
setup_builtin_llm() {
    local enc_file="$INSTALL_DIR/builtin_llm.enc"
    (( INSTALL_SERVER )) || return 0
    [[ -f "$enc_file" ]] || return 0

    header "8. Built-in LLM (optional)"

    if ! command -v openssl &>/dev/null; then
        warn "openssl not available — skipping built-in LLM"
        return 0
    fi

    local passphrase="${PDL_BUILTIN_PASSPHRASE:-}"
    if [[ -z "$passphrase" ]]; then
        if [[ -r /dev/tty ]]; then
            echo "  If the author gave you a passphrase, enter it to auto-configure LLM."
            echo "  Press Enter to skip."
            tty_read "  Passphrase: "
            passphrase="$REPLY"
        else
            info "No tty — skipping (set PDL_BUILTIN_PASSPHRASE for non-interactive)"
            return 0
        fi
    else
        info "Using PDL_BUILTIN_PASSPHRASE env var"
    fi

    if [[ -z "$passphrase" ]]; then
        info "Skipped — configure LLM later in Settings page"
        return 0
    fi

    local target="$DATA_DIR/builtin.key"
    if openssl enc -d -aes-256-cbc -pbkdf2 -iter 100000 -base64 \
        -in "$enc_file" -out "$target" -pass pass:"$passphrase" 2>/dev/null \
        && python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$target" 2>/dev/null; then
        chmod 600 "$target"
        ok "Built-in LLM configured → $target"
    else
        rm -f "$target"
        warn "Wrong passphrase — skipped (re-run install.sh to retry)"
    fi
}

# ─── Build Frontend ──────────────────────────────────────────────────
build_frontend() {
    header "9. Frontend"

    if (( ! INSTALL_SERVER )); then
        ok "Collector-only install — no frontend needed"
        return
    fi

    if [ "$INSTALL_MODE" = "release" ]; then
        ok "Frontend ships inside the wheel — no build needed"
        return
    fi

    local frontend_dir="$INSTALL_DIR/web/frontend"
    if [ ! -d "$frontend_dir" ]; then
        warn "Frontend directory not found, skipping"
        return
    fi

    if [ -d "$frontend_dir/dist" ]; then
        ok "Frontend already built (dist/ exists)"
        tty_read "  Rebuild? [y/N] " "N"
        if [[ ! "$REPLY" =~ ^[Yy] ]]; then
            return
        fi
    fi

    if ! command -v node &>/dev/null; then
        warn "Node.js not found — cannot build frontend"
        info "Install Node.js 18+ and run: cd web/frontend && npm install && npm run build"
        return
    fi

    info "Installing npm dependencies..."
    (cd "$frontend_dir" && npm install -q 2>&1 | tail -1)
    info "Building frontend..."
    (cd "$frontend_dir" && npm run build 2>&1 | tail -1)
    ok "Frontend built"
}

# ─── Verification ────────────────────────────────────────────────────
verify() {
    header "10. Verification"

    source "$VENV_DIR/bin/activate" 2>/dev/null || true

    local import_ok=1
    python3 -c "import aiosqlite" 2>/dev/null && ok "aiosqlite (async DB driver)" || { fail "aiosqlite — run: pip install -e ."; import_ok=0; }
    python3 -c "import sqlite_vec" 2>/dev/null && ok "sqlite_vec (vector index)" || { fail "sqlite_vec — run: pip install -e ."; import_ok=0; }
    python3 -c "import fastapi, uvicorn, httpx, apscheduler, pydantic, yaml, imagehash, PIL" 2>/dev/null && ok "FastAPI + core deps" || { fail "Core Python deps missing — run: pip install -e ."; import_ok=0; }
    python3 -c "from auto_daily_log.app import Application" 2>/dev/null && ok "Core module import" || { fail "Core module import"; import_ok=0; }
    python3 -c "from auto_daily_log_collector.monitor_internals.platforms.detect import get_platform_module; m = get_platform_module(); print(type(m).__name__)" 2>/dev/null && ok "Platform detection" || { fail "Platform detection"; import_ok=0; }
    python3 -c "from auto_daily_log.web.app import create_app" 2>/dev/null && ok "Web app import" || { fail "Web app import"; import_ok=0; }

    if [ "$PLATFORM" = "macos" ]; then
        python3 -c "
import subprocess
r = subprocess.run(['osascript', '-e', 'tell application \"System Events\" to get name of first process whose frontmost is true'], capture_output=True, text=True)
assert r.returncode == 0, r.stderr
" 2>/dev/null && ok "macOS AppleScript (window tracking)" || warn "macOS AppleScript — may need Accessibility permissions"
    else
        python3 -c "
import subprocess
r = subprocess.run(['xdotool', 'getactivewindow'], capture_output=True, text=True)
assert r.returncode == 0, r.stderr
" 2>/dev/null && ok "xdotool (window tracking)" || warn "xdotool — not available or no display"
    fi

    if [ "$PLATFORM" = "macos" ]; then
        python3 -c "import Vision" 2>/dev/null && ok "Vision OCR (native)" || warn "Vision OCR — install pyobjc-framework-Vision"
    else
        command -v tesseract &>/dev/null && ok "Tesseract OCR" || warn "Tesseract OCR — not installed"
    fi

    if [ "$import_ok" -eq 1 ]; then
        echo ""
        ok "All checks passed"
    fi
}

# ─── Print Summary & Offer Auto-start ────────────────────────────────
summary() {
    header "Done!"
    echo ""
    echo -e "  ${BOLD}Next steps${NC} (via ./pdl):"
    if (( INSTALL_SERVER )); then
        echo "    ./pdl server start             # start the Web UI + API"
        echo "    Open http://127.0.0.1:8888 in browser"
    fi
    if (( INSTALL_COLLECTOR )); then
        echo "    ./pdl collector start          # push activity to server"
    fi
    if (( INSTALL_SERVER && INSTALL_COLLECTOR )); then
        echo "    ./pdl start                    # start both"
    fi
    echo ""
    if [ "$PLATFORM" = "macos" ]; then
        echo -e "  ${YELLOW}macOS Note:${NC} Grant Accessibility permissions to Terminal/iTerm2"
        echo -e "  in System Settings → Privacy & Security → Accessibility"
        echo ""
    fi

    # Offer auto-start
    local start_cmd=""
    if (( INSTALL_SERVER && INSTALL_COLLECTOR )); then
        start_cmd="start"
    elif (( INSTALL_SERVER )); then
        start_cmd="server start"
    elif (( INSTALL_COLLECTOR )); then
        start_cmd="collector start"
    fi

    if [[ -n "$start_cmd" && -x "$INSTALL_DIR/pdl" ]]; then
        tty_read "  Start now? [Y/n] " "Y"
        if [[ "$REPLY" =~ ^[Yy] ]]; then
            echo ""
            "$INSTALL_DIR/pdl" $start_cmd || warn "Failed to start — check logs with: ./pdl server logs"
        fi
    fi
}

# ─── Main ─────────────────────────────────────────────────────────────
main() {
    echo ""
    echo -e "${BOLD}╔══════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}║   Polars Daily Log Installer v$VERSION${NC}"
    echo -e "${BOLD}╚══════════════════════════════════════════╝${NC}"

    MISSING_CRITICAL=0

    resolve_role
    detect_platform
    detect_install_mode
    info "Platform: $PLATFORM | Package manager: $PKG_MGR | Mode: $INSTALL_MODE | Role: $ROLE"

    check_python
    check_sys_deps

    if [ "${#MISSING_DEPS[@]}" -gt 0 ] || [ "${#OPTIONAL_MISSING[@]}" -gt 0 ]; then
        install_sys_deps
    fi

    if [ "$MISSING_CRITICAL" -eq 1 ]; then
        echo ""
        fail "Cannot continue — fix critical dependencies above first"
        exit 1
    fi

    setup_venv
    install_python_deps
    setup_data
    setup_builtin_llm
    build_frontend
    verify
    summary
}

main "$@"
