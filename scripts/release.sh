#!/usr/bin/env bash
# ─── Polars Daily Log — Release Builder ──────────────────────────────
# Produces a self-contained tarball that non-developer users can install
# without git, Node.js, or compiler toolchains:
#
#   release/polars-daily-log-<version>.tar.gz
#
# Tarball layout:
#   polars-daily-log-<version>/
#     wheels/
#       auto_daily_log-<version>-py3-none-any.whl
#     install.sh
#     install.ps1
#     pdl
#     config.yaml.example
#     collector.yaml.example
#     README.md
#     VERSION
#
# Usage:
#   bash scripts/release.sh                    # use version from pyproject.toml
#   bash scripts/release.sh 0.2.0              # override version (also tags)
#   VERSION=0.2.0 bash scripts/release.sh      # or via env
# ──────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# ─── Colors ──────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
    GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'; BLUE=$'\033[0;34m'; BOLD=$'\033[1m'; NC=$'\033[0m'
else
    GREEN=""; YELLOW=""; BLUE=""; BOLD=""; NC=""
fi
ok()   { echo "${GREEN}✓${NC} $1"; }
info() { echo "${BLUE}→${NC} $1"; }
warn() { echo "${YELLOW}!${NC} $1"; }

# ─── Resolve version ─────────────────────────────────────────────────
VERSION="${1:-${VERSION:-}}"
if [[ -z "$VERSION" ]]; then
    VERSION="$(grep -E '^version\s*=' pyproject.toml | head -1 | cut -d'"' -f2)"
fi
[[ -n "$VERSION" ]] || { echo "Cannot determine version"; exit 1; }

STAGE_DIR="$ROOT_DIR/release/polars-daily-log-$VERSION"
TARBALL="$ROOT_DIR/release/polars-daily-log-$VERSION.tar.gz"

echo ""
echo "${BOLD}Building Polars Daily Log $VERSION${NC}"
echo ""

# ─── Pre-flight ──────────────────────────────────────────────────────
info "Pre-flight checks..."
command -v node >/dev/null 2>&1 || { echo "Node.js required (for frontend build)"; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 required"; exit 1; }
python3 -c "import build" 2>/dev/null || { info "Installing 'build' package..."; python3 -m pip install --user build -q; }
ok "node + python3 + build available"

# ─── 1. Build frontend ───────────────────────────────────────────────
info "Building frontend (npm ci + npm run build)..."
(cd web/frontend && npm ci --silent 2>&1 | tail -2 && npm run build 2>&1 | tail -3)
[[ -f web/frontend/dist/index.html ]] || { echo "Frontend build failed (no dist/index.html)"; exit 1; }
ok "Frontend built: web/frontend/dist/"

# ─── 2. Stage frontend_dist into package ─────────────────────────────
info "Staging frontend_dist inside auto_daily_log/ for wheel inclusion..."
STAGED_DIST="$ROOT_DIR/auto_daily_log/frontend_dist"
rm -rf "$STAGED_DIST"
cp -r web/frontend/dist "$STAGED_DIST"
ok "Staged: $STAGED_DIST ($(du -sh "$STAGED_DIST" | cut -f1))"

# Cleanup on exit so dev tree stays clean even if a later step fails
trap 'rm -rf "$STAGED_DIST" "$ROOT_DIR/build" "$ROOT_DIR"/*.egg-info 2>/dev/null || true' EXIT

# ─── 3. Build wheel ──────────────────────────────────────────────────
info "Building Python wheel..."
rm -rf dist
python3 -m build --wheel 2>&1 | tail -5
WHEEL_PATH=$(ls dist/auto_daily_log-*-py3-none-any.whl 2>/dev/null | head -1)
[[ -n "$WHEEL_PATH" && -f "$WHEEL_PATH" ]] || { echo "Wheel not produced"; exit 1; }
WHEEL_SIZE=$(du -h "$WHEEL_PATH" | cut -f1)
ok "Wheel: $(basename "$WHEEL_PATH") ($WHEEL_SIZE)"

# Sanity-check: wheel contains frontend_dist
info "Verifying wheel contents..."
python3 -c "
import zipfile, sys
with zipfile.ZipFile('$WHEEL_PATH') as z:
    names = z.namelist()
    has_dist = any('frontend_dist/index.html' in n for n in names)
    has_core = any(n.endswith('auto_daily_log/app.py') for n in names)
    has_collector = any('auto_daily_log_collector/runner.py' in n for n in names)
    has_shared = any('shared/schemas.py' in n for n in names)
    if not (has_dist and has_core and has_collector and has_shared):
        print(f'  missing: dist={has_dist} core={has_core} collector={has_collector} shared={has_shared}', file=sys.stderr)
        sys.exit(1)
"
ok "Wheel contains: frontend_dist/, auto_daily_log/, auto_daily_log_collector/, shared/"

# ─── 4. Assemble tarball ─────────────────────────────────────────────
info "Assembling release tarball..."
rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR/wheels"
cp "$WHEEL_PATH" "$STAGE_DIR/wheels/"
cp install.sh install.ps1 pdl "$STAGE_DIR/"
cp config.yaml.example collector.yaml.example "$STAGE_DIR/" 2>/dev/null || {
    # If examples are missing, use current configs as templates
    [[ -f config.yaml ]] && cp config.yaml "$STAGE_DIR/config.yaml.example"
    [[ -f collector.yaml ]] && cp collector.yaml "$STAGE_DIR/collector.yaml.example" || true
}
[[ -f README.md ]] && cp README.md "$STAGE_DIR/"
echo "$VERSION" > "$STAGE_DIR/VERSION"

tar czf "$TARBALL" -C "$ROOT_DIR/release" "polars-daily-log-$VERSION"
TARBALL_SIZE=$(du -h "$TARBALL" | cut -f1)
ok "Tarball: $TARBALL ($TARBALL_SIZE)"

# ─── 5. Summary ──────────────────────────────────────────────────────
echo ""
echo "${BOLD}Done!${NC}"
echo ""
echo "  Tarball:    $TARBALL"
echo "  Wheel:      $WHEEL_PATH"
echo "  Stage dir:  $STAGE_DIR"
echo ""
echo "  To test install locally:"
echo "    mkdir -p /tmp/pdl-test && cd /tmp/pdl-test"
echo "    tar xzf $TARBALL"
echo "    cd polars-daily-log-$VERSION"
echo "    bash install.sh"
echo ""
echo "  To publish:"
echo "    scp $TARBALL user@release-server:/var/www/releases/"
echo ""
