#!/usr/bin/env bash
set -euo pipefail

# ─── Polars Daily Log — One-liner Bootstrap ─────────────────────────────
#
# 用法：
#   curl -fsSL https://raw.githubusercontent.com/Conner2077/polars-daily-log/master/bootstrap.sh | bash
#
# 可选环境变量：
#   PDL_VERSION       指定版本（默认 latest，拉最新 GitHub Release）
#   PDL_INSTALL_DIR   安装目录（默认 $HOME/.polars-daily-log）
#   PDL_ROLE          server / collector / both / ask（默认 ask，透传给 install.sh）
#   PDL_SERVER_URL    collector 指向的 server URL（非交互模式）
#   PDL_COLLECTOR_NAME collector 的展示名（非交互模式）
#
# 示例：
#   # 装最新版，交互选角色
#   curl -fsSL https://.../bootstrap.sh | bash
#
#   # 装 0.2.0 版本的 collector，连到已有 server
#   curl -fsSL https://.../bootstrap.sh | \
#     PDL_VERSION=0.2.0 PDL_ROLE=collector \
#     PDL_SERVER_URL=http://192.168.1.10:8888 \
#     PDL_COLLECTOR_NAME=my-laptop bash
# ─────────────────────────────────────────────────────────────────────────

REPO="Conner2077/polars-daily-log"
INSTALL_DIR="${PDL_INSTALL_DIR:-$HOME/.polars-daily-log}"
VERSION="${PDL_VERSION:-latest}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}!${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1" >&2; }
info() { echo -e "  ${BLUE}→${NC} $1"; }
header() { echo -e "\n${BOLD}$1${NC}"; }

# ─── 先做最小前置检查 ────────────────────────────────────────────────
require_cmd() {
    command -v "$1" &>/dev/null || { fail "缺少 $1 — 请先安装后再跑"; exit 1; }
}

check_prereqs() {
    header "0. Bootstrap 前置检查"
    require_cmd curl
    require_cmd tar
    ok "curl / tar 可用"

    local uname_s; uname_s="$(uname -s)"
    case "$uname_s" in
        Darwin|Linux) ok "Platform: $uname_s" ;;
        *) fail "不支持的平台: $uname_s（仅支持 macOS / Linux，Windows 用 install.ps1）"; exit 1 ;;
    esac
}

# ─── 解析版本号（拉 latest 或用指定的） ──────────────────────────────
resolve_version() {
    header "1. 确定版本"

    if [ "$VERSION" = "latest" ]; then
        info "查询最新 Release..."
        # GitHub API 返回 JSON，不想依赖 jq，用 grep 取 tag_name
        local api_url="https://api.github.com/repos/$REPO/releases/latest"
        local tag
        tag="$(curl -fsSL "$api_url" | grep -m1 '"tag_name"' | sed -E 's/.*"tag_name": *"v?([^"]+)".*/\1/')"
        if [ -z "$tag" ]; then
            fail "无法解析最新版本号 — 请手动指定 PDL_VERSION"
            exit 1
        fi
        VERSION="$tag"
    fi

    # 规范化：去掉前缀 v（tag 是 v0.2.0，tarball 里用 0.2.0）
    VERSION="${VERSION#v}"
    ok "版本: $VERSION"
}

# ─── 下载并解压 tarball ──────────────────────────────────────────────
download_and_extract() {
    header "2. 下载 tarball"

    local tarball="polars-daily-log-${VERSION}.tar.gz"
    local url="https://github.com/${REPO}/releases/download/v${VERSION}/${tarball}"
    _BOOTSTRAP_TMP="$(mktemp -d)"
    trap 'rm -rf "$_BOOTSTRAP_TMP"' EXIT
    local tmp_dir="$_BOOTSTRAP_TMP"

    info "从 $url 下载..."
    if ! curl -fL --progress-bar "$url" -o "$tmp_dir/$tarball"; then
        fail "下载失败 — 确认版本 v$VERSION 存在：https://github.com/$REPO/releases"
        exit 1
    fi
    ok "下载完成（$(du -h "$tmp_dir/$tarball" | awk '{print $1}')）"

    # 目标目录处理
    if [ -d "$INSTALL_DIR" ]; then
        if [ -f "$INSTALL_DIR/VERSION" ]; then
            local existing; existing="$(cat "$INSTALL_DIR/VERSION" 2>/dev/null || echo unknown)"
            warn "$INSTALL_DIR 已存在（v$existing），将原地覆盖升级"
            # 停掉可能在跑的进程
            if [ -x "$INSTALL_DIR/pdl" ]; then
                ("$INSTALL_DIR/pdl" stop 2>/dev/null) || true
            fi
        else
            warn "$INSTALL_DIR 已存在但不像 pdl 安装目录 — 终止以免误伤"
            exit 1
        fi
    else
        mkdir -p "$INSTALL_DIR"
        ok "创建安装目录: $INSTALL_DIR"
    fi

    info "解压到 $INSTALL_DIR..."
    tar -xzf "$tmp_dir/$tarball" -C "$INSTALL_DIR" --strip-components=1
    ok "解压完成"
}

# ─── 运行 install.sh（透传角色等 env） ──────────────────────────────
run_installer() {
    header "3. 运行 install.sh"

    if [ ! -f "$INSTALL_DIR/install.sh" ]; then
        fail "tarball 里没找到 install.sh — release 损坏？"
        exit 1
    fi

    # 透传可选 env（只在设置过时传，避免覆盖 install.sh 的默认值）
    local env_prefix=()
    [ -n "${PDL_ROLE:-}" ]           && env_prefix+=("PDL_ROLE=$PDL_ROLE")
    [ -n "${PDL_SERVER_URL:-}" ]     && env_prefix+=("PDL_SERVER_URL=$PDL_SERVER_URL")
    [ -n "${PDL_COLLECTOR_NAME:-}" ] && env_prefix+=("PDL_COLLECTOR_NAME=$PDL_COLLECTOR_NAME")

    cd "$INSTALL_DIR"
    # Key: when running via `curl | bash`, bash's stdin is the pipe (not the
    # terminal). Redirect install.sh's stdin from /dev/tty so interactive
    # prompts (role selection, passphrase, etc.) can read user input.
    # If /dev/tty is unavailable (CI, Docker), install.sh's tty_read falls
    # back to defaults — no harm done.
    local tty_redirect=""
    [[ -e /dev/tty ]] && tty_redirect="/dev/tty"

    if [ "${#env_prefix[@]}" -gt 0 ]; then
        if [[ -n "$tty_redirect" ]]; then
            env "${env_prefix[@]}" bash install.sh < "$tty_redirect"
        else
            env "${env_prefix[@]}" bash install.sh
        fi
    else
        if [[ -n "$tty_redirect" ]]; then
            bash install.sh < "$tty_redirect"
        else
            bash install.sh
        fi
    fi
}

# ─── 最后提示 ───────────────────────────────────────────────────────
print_next() {
    header "完成"
    echo ""
    echo -e "  ${BOLD}安装位置${NC}: $INSTALL_DIR"
    echo ""
    echo -e "  ${BOLD}建议做法${NC}: 把 pdl 加到 PATH，或用绝对路径调用"
    echo "    echo 'export PATH=\"$INSTALL_DIR:\$PATH\"' >> ~/.zshrc"
    echo "    source ~/.zshrc"
    echo "    pdl server start    # 或 pdl collector start / pdl start"
    echo ""
    echo -e "  ${BOLD}升级${NC}: 再跑一遍 bootstrap（会原地覆盖）"
    echo ""
}

main() {
    echo ""
    echo -e "${BOLD}╔════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}║  Polars Daily Log — One-liner Bootstrap   ║${NC}"
    echo -e "${BOLD}╚════════════════════════════════════════════╝${NC}"

    check_prereqs
    resolve_version
    download_and_extract
    run_installer
    print_next
}

main "$@"
