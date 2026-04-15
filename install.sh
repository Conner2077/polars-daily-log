#!/usr/bin/env bash
set -euo pipefail

# ─── Auto Daily Log Installer ───────────────────────────────────────
# Supports: macOS (Intel/Apple Silicon), Linux (Debian/Ubuntu/Fedora/Arch)
# Usage:    bash install.sh
# ─────────────────────────────────────────────────────────────────────

VERSION="0.1.0"
APP_NAME="auto-daily-log"
DATA_DIR="$HOME/.auto_daily_log"
INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$INSTALL_DIR/.venv"
MIN_PYTHON="3.9"

# ─── Colors ──────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}!${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }
info() { echo -e "  ${BLUE}→${NC} $1"; }
header() { echo -e "\n${BOLD}$1${NC}"; }

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
    header "1. Python"

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
    header "2. System Dependencies"

    MISSING_DEPS=()
    OPTIONAL_MISSING=()

    if [ "$PLATFORM" = "macos" ]; then
        check_dep_required "git" "git"
        # macOS uses native APIs (AppleScript, Vision framework) - no extra deps
        ok "macOS native APIs (AppleScript, Vision OCR) — built-in"
    else
        # Linux required
        check_dep_required "git" "git"
        check_dep_required "xdotool" "xdotool"

        # Linux optional but recommended
        check_dep_optional "xprop" "x11-utils"
        check_dep_optional "xprintidle" "xprintidle"

        # Screenshot: need at least one
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

        # OCR
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

    header "3. Install System Dependencies"

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
    read -rp "  Proceed? [Y/n] " answer
    answer="${answer:-Y}"
    if [[ "$answer" =~ ^[Yy] ]]; then
        # Map package names per distro
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
    header "4. Python Virtual Environment"

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

# ─── Install Python Dependencies ─────────────────────────────────────
install_python_deps() {
    header "5. Python Dependencies"

    info "Installing core + $PLATFORM dependencies..."
    pip install --upgrade pip -q 2>/dev/null
    pip install -e ".[$PLATFORM]" -q 2>&1 | tail -3
    ok "Installed auto-daily-log[$PLATFORM]"
}

# ─── Setup Data Directory & Config ────────────────────────────────────
setup_data() {
    header "6. Data & Config"

    if [ ! -d "$DATA_DIR" ]; then
        mkdir -p "$DATA_DIR"
        ok "Created data directory: $DATA_DIR"
    else
        ok "Data directory exists: $DATA_DIR"
    fi

    local config_dest="$INSTALL_DIR/config.yaml"
    if [ -f "$config_dest" ]; then
        ok "Config file exists: $config_dest"
    else
        cp "$INSTALL_DIR/config.yaml.example" "$config_dest" 2>/dev/null || true
        ok "Config file ready: $config_dest"
    fi
}

# ─── Build Frontend ──────────────────────────────────────────────────
build_frontend() {
    header "7. Frontend"

    local frontend_dir="$INSTALL_DIR/web/frontend"
    if [ ! -d "$frontend_dir" ]; then
        warn "Frontend directory not found, skipping"
        return
    fi

    if [ -d "$frontend_dir/dist" ]; then
        ok "Frontend already built (dist/ exists)"
        read -rp "  Rebuild? [y/N] " answer
        answer="${answer:-N}"
        if [[ ! "$answer" =~ ^[Yy] ]]; then
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
    header "8. Verification"

    source "$VENV_DIR/bin/activate" 2>/dev/null || true

    # Test Python imports — these must all pass, or the server won't start.
    # Checking core runtime deps individually so a missing one is obvious.
    local import_ok=1
    python3 -c "import aiosqlite" 2>/dev/null && ok "aiosqlite (async DB driver)" || { fail "aiosqlite — run: pip install -e ."; import_ok=0; }
    python3 -c "import sqlite_vec" 2>/dev/null && ok "sqlite_vec (vector index)" || { fail "sqlite_vec — run: pip install -e ."; import_ok=0; }
    python3 -c "import fastapi, uvicorn, httpx, apscheduler, pydantic, yaml, imagehash, PIL" 2>/dev/null && ok "FastAPI + core deps" || { fail "Core Python deps missing — run: pip install -e ."; import_ok=0; }
    python3 -c "from auto_daily_log.app import Application" 2>/dev/null && ok "Core module import" || { fail "Core module import"; import_ok=0; }
    python3 -c "from auto_daily_log.monitor.platforms.detect import get_platform_module; m = get_platform_module(); print(type(m).__name__)" 2>/dev/null && ok "Platform detection" || { fail "Platform detection"; import_ok=0; }
    python3 -c "from auto_daily_log.web.app import create_app" 2>/dev/null && ok "Web app import" || { fail "Web app import"; import_ok=0; }

    # Platform-specific checks
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

    # OCR check
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

# ─── Print Summary ───────────────────────────────────────────────────
summary() {
    header "Done!"
    echo ""
    echo -e "  ${BOLD}Start the app:${NC}"
    echo -e "    cd $INSTALL_DIR"
    echo -e "    source .venv/bin/activate"
    echo -e "    auto-daily-log --port 8080"
    echo ""
    echo -e "  ${BOLD}Or with Python:${NC}"
    echo -e "    python -m auto_daily_log --port 8080"
    echo ""
    echo -e "  ${BOLD}Then open:${NC} http://127.0.0.1:8080"
    echo ""

    if [ "$PLATFORM" = "macos" ]; then
        echo -e "  ${YELLOW}macOS Note:${NC} Grant Accessibility permissions to Terminal/iTerm2"
        echo -e "  in System Settings → Privacy & Security → Accessibility"
        echo ""
    fi
}

# ─── Main ─────────────────────────────────────────────────────────────
main() {
    echo ""
    echo -e "${BOLD}╔══════════════════════════════════════╗${NC}"
    echo -e "${BOLD}║   Auto Daily Log Installer v$VERSION   ║${NC}"
    echo -e "${BOLD}╚══════════════════════════════════════╝${NC}"

    MISSING_CRITICAL=0

    detect_platform
    info "Platform: $PLATFORM | Package manager: $PKG_MGR"

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
    build_frontend
    verify
    summary
}

main "$@"
