#!/usr/bin/env bash
# =============================================================================
# prepare-deploy.sh — Interactive preparation for a Survival Map deployment.
#
#   Phase 1: System checks (docker, compose, python ≥3.10, venv, RAM, disk, port 80)
#   Phase 2: Project structure checks (docker-compose.yml, service dirs, gen_session.py, .gitignore)
#   Phase 3: .env configuration (clean rewrite with R-C29 allow-list only)
#   Phase 4: parser/session.session generation via gen_session.py (env-var credentials)
#   Phase 5: Final checklist + manual deploy command
#
# G-3: this script NEVER runs `docker compose up`. It only prints the command.
# G-15 / R-C24: PARSER_API_ID / PARSER_API_HASH / phone are read from process env
#   and NEVER written to disk.
#
# Usage:
#   ./scripts/prepare-deploy.sh        # interactive
#   ./scripts/prepare-deploy.sh -y     # non-interactive (keep existing session, keep .env values)
#   ./scripts/prepare-deploy.sh -h     # help
#
# Bash 3.2 (macOS default) compatible: no ${var,,} / associative arrays.
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Phase 0: Script setup — constants, helpers, OS detection, safety trap.
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/.env"
SESSION_FILE="$PROJECT_ROOT/parser/session.session"
GEN_SESSION="$PROJECT_ROOT/scripts/gen_session.py"
VENV_DIR="$PROJECT_ROOT/.venv-session"

MIN_PY_MAJOR=3
MIN_PY_MINOR=10
MIN_RAM_MB=4096
MIN_DISK_MB=5120

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

ERRORS=0
WARNINGS=0

# macOS (bash 3.2) has no ${var,,} — lowercase via tr for case-insensitive compare.
to_lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]' ; }

log_ok()   { printf "${GREEN}[OK]${NC}    %s\n"  "$*"; }
log_warn() { printf "${YELLOW}[WARN]${NC}  %s\n"  "$*"; WARNINGS=$((WARNINGS + 1)); }
log_fail() { printf "${RED}[FAIL]${NC}  %s\n"    "$*"; ERRORS=$((ERRORS + 1)); }
log_info() { printf "${BLUE}[INFO]${NC}  %s\n"  "$*"; }

# GNU stat vs BSD/macOS stat: return file size in bytes.
file_size() {
    if stat --version >/dev/null 2>&1; then
        stat -c %s "$1"            # GNU coreutils
    else
        stat -f %z "$1"            # BSD / macOS
    fi
}

# Safety net (G-15, R-C24): make sure no credential lingers after any exit.
trap 'unset API_ID API_HASH PHONE 2>/dev/null || true' EXIT

OS="$(uname -s)"   # "Linux" | "Darwin" ...
case "$OS" in
    Linux*)  OS_FAMILY="linux" ;;
    Darwin*) OS_FAMILY="macos" ;;
    *)       OS_FAMILY="unknown" ;;
esac

# Package manager hint (for install instructions).
PM_HINT=""
if [[ "$OS_FAMILY" == "linux" ]]; then
    if command -v apt-get  >/dev/null 2>&1; then PM_HINT="apt-get"
    elif command -v dnf    >/dev/null 2>&1; then PM_HINT="dnf"
    elif command -v pacman >/dev/null 2>&1; then PM_HINT="pacman"
    fi
elif [[ "$OS_FAMILY" == "macos" ]]; then
    PM_HINT="brew"
fi

# ---------------------------------------------------------------------------
# Argument parsing (optional flags).
# ---------------------------------------------------------------------------
ASSUME_YES=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        -y|--yes) ASSUME_YES=true; shift ;;
        -h|--help)
            cat <<'HELP'
prepare-deploy.sh — prepare a Survival Map deployment.

Usage: ./scripts/prepare-deploy.sh [-y|--yes] [-h|--help]

  -y, --yes   Non-interactive: keep existing .env values & session,
              auto-append .gitignore entries, skip session-recreate prompt.
  -h, --help  Show this help.

Phases: system checks → structure checks → .env (R-C29 allow-list) →
        session.session generation (env-var creds, never on disk) → manual deploy command.

This script never runs `docker compose up` (G-3). Run that command yourself.
HELP
            exit 0
            ;;
        *) printf "${RED}[FAIL]${NC} Unknown option: %s\n" "$1"; exit 2 ;;
    esac
done

confirm() {  # confirm <prompt> : returns 0 on yes
    local prompt="$1"
    if [[ "$ASSUME_YES" == true ]]; then
        return 0
    fi
    local reply
    read -rp "$prompt [Y/n] " reply
    case "$(to_lower "${reply:-y}")" in
        y|yes|"") return 0 ;;
        *)        return 1 ;;
    esac
}

printf "${BLUE}=== Survival Map — prepare-deploy.sh ===${NC}\n"
log_info "Project root: $PROJECT_ROOT"
log_info "OS: $OS  (family: $OS_FAMILY, package manager hint: ${PM_HINT:-unknown})"

# ===========================================================================
# Phase 1: System Check
# ===========================================================================
log_info "--- Phase 1: System checks ---"

# 1a. Docker engine (installed + daemon running)
docker_installed=false
docker_running=false
if command -v docker >/dev/null 2>&1; then
    docker_installed=true
    if docker info >/dev/null 2>&1; then
        docker_running=true
    fi
fi

# 1b. Docker compose v2 vs v1
compose_v2=false
compose_v1=false
if docker compose version >/dev/null 2>&1; then
    compose_v2=true
elif docker-compose version >/dev/null 2>&1; then
    compose_v1=true
    log_warn "Docker Compose v1 detected; v2 (docker compose) is recommended"
fi

# 1c. Python >= 3.10
PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then PYTHON_BIN="python3"
elif command -v python   >/dev/null 2>&1; then PYTHON_BIN="python"
fi
PY_VERSION_OK=false
if [[ -n "$PYTHON_BIN" ]]; then
    if "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import sys
assert sys.version_info >= (3, 10), "need >=3.10"
PY
    then
        PY_VERSION_OK=true
    else
        log_warn "System Python is < 3.10; 3.10+ is required to build the venv"
    fi
else
    log_warn "No python3/python found on PATH"
fi

# 1d. venv module available
venv_ok=false
if [[ -n "$PYTHON_BIN" ]] && "$PYTHON_BIN" -c 'import venv' >/dev/null 2>&1; then
    venv_ok=true
else
    log_fail "Python venv module not available"
fi

# 1e. RAM >= 4 GB
RAM_MB=-1
if [[ "$OS_FAMILY" == "linux" ]]; then
    RAM_MB="$(grep MemTotal /proc/meminfo 2>/dev/null | awk '{print int($2/1024)}')"
elif [[ "$OS_FAMILY" == "macos" ]]; then
    RAM_MB="$(sysctl -n hw.memsize 2>/dev/null | awk '{print int($1/1048576)}')"
fi
if [[ "$RAM_MB" -ge 0 ]] && [[ "$RAM_MB" -lt "$MIN_RAM_MB" ]]; then
    log_warn "RAM ${RAM_MB}MB < ${MIN_RAM_MB}MB — containers may struggle to schedule"
elif [[ "$RAM_MB" -lt 0 ]]; then
    log_warn "Could not detect RAM; ensure ≥4GB is available"
fi

# 1f. Disk >= 5 GB free (POSIX 1MB blocks)
FREE_MB=-1
FREE_MB="$(df -Pm "$PROJECT_ROOT" 2>/dev/null | awk 'NR==2{print $4}')"
if [[ "$FREE_MB" =~ ^[0-9]+$ ]] && [[ "$FREE_MB" -lt "$MIN_DISK_MB" ]]; then
    log_warn "Free disk ${FREE_MB}MB < ${MIN_DISK_MB}MB — not enough space for images + data"
elif ! [[ "$FREE_MB" =~ ^[0-9]+$ ]]; then
    log_warn "Could not detect free disk space"
fi

# 1g. Port 80 free
port_80_in_use=false
if command -v ss >/dev/null 2>&1; then
    ss -tln 2>/dev/null | grep -q ':80 ' && port_80_in_use=true
elif command -v netstat >/dev/null 2>&1; then
    netstat -tln 2>/dev/null | grep -q ':80 ' && port_80_in_use=true
elif command -v lsof >/dev/null 2>&1; then
    lsof -i :80 >/dev/null 2>&1 && port_80_in_use=true
fi
if [[ "$port_80_in_use" == true ]]; then
    log_warn "Port 80 is already in use — web container cannot bind"
fi

# 1h. Critical dependency check — exit immediately if docker/python missing.
critical_missing=false
if ! $docker_installed; then
    critical_missing=true
    log_fail "Docker is not installed"
fi
if ! $docker_running; then
    critical_missing=true
    log_fail "Docker daemon is not running"
fi
if [[ -z "$PYTHON_BIN" ]]; then
    critical_missing=true
    log_fail "Python is not installed"
elif ! $PY_VERSION_OK; then
    critical_missing=true
    log_fail "Python >= ${MIN_PY_MAJOR}.${MIN_PY_MINOR} is required"
fi

if $critical_missing; then
    echo ""
    log_info "Install instructions for your system:"
    if [[ "$OS_FAMILY" == "macos" ]]; then
        echo "  brew install --cask docker"
        echo "  brew install python@3.10"
        echo "  open -a Docker   # then wait for daemon to start"
    elif [[ "$PM_HINT" == "apt-get" ]]; then
        echo "  sudo apt-get update && sudo apt-get install -y docker.io python3 python3-venv"
        echo "  sudo systemctl start docker"
    elif [[ "$PM_HINT" == "dnf" ]]; then
        echo "  sudo dnf install -y docker python3 python3-virtualenv"
        echo "  sudo systemctl start docker"
    elif [[ "$PM_HINT" == "pacman" ]]; then
        echo "  sudo pacman -S --noconfirm docker python python-virtualenv"
        echo "  sudo systemctl start docker"
    else
        echo "  Install Docker + Python 3.10+ for your platform."
    fi
    exit 1
fi

log_ok "Docker engine + Python $("$PYTHON_BIN" -c 'import sys; print("%d.%d" % (sys.version_info[0], sys.version_info[1]))')"

# ===========================================================================
# Phase 2: Project Structure Check
# ===========================================================================
log_info "--- Phase 2: Project structure checks ---"

# 2a. Required files (GEN_SESSION is absolute; docker-compose.yml is relative)
for f in docker-compose.yml "$GEN_SESSION"; do
    if [[ ! -f "$f" ]]; then
        log_fail "Missing: $f"
    else
        log_ok "Found: $f"
    fi
done

# 2b. Required service directories
for dir in core parser processor postgres web; do
    if [[ ! -d "$PROJECT_ROOT/$dir" ]]; then
        log_fail "Missing directory: $dir/"
    else
        log_ok "Found directory: $dir/"
    fi
done

# 2c. .gitignore must cover secrets + sessions + venv variants.
gitignore_missing=()
while IFS= read -r entry; do
    [[ -z "$entry" ]] && continue
    # entries are stored without leading slash; compare against gitignore content
    if ! grep -qxF "$entry" "$PROJECT_ROOT/.gitignore" 2>/dev/null; then
        gitignore_missing+=("$entry")
    fi
done <<'GI_ENTRIES'
.env
*.session
.venv-session/
.venv-*
GI_ENTRIES

if [[ ${#gitignore_missing[@]} -gt 0 ]]; then
    log_warn ".gitignore missing entries: ${gitignore_missing[*]}"
    if confirm "Add these entries to .gitignore?"; then
        {
            printf '\n# --- prepare-deploy.sh: deployment hygiene ---\n'
            for entry in "${gitignore_missing[@]}"; do
                printf '%s\n' "$entry"
            done
        } >> "$PROJECT_ROOT/.gitignore"
        log_ok "Added missing entries to .gitignore"
    else
        log_info "Skipping .gitignore update (manual review recommended)"
    fi
else
    log_ok ".gitignore covers .env, sessions, and venv variants"
fi

# ===========================================================================
# Phase 3: .env Configuration (clean rewrite — R-C29 allow-list only)
# ===========================================================================
log_info "--- Phase 3: .env configuration ---"

if [[ -f "$ENV_FILE" ]]; then
    set -a
    # source may contain syntax we don't control; never let set -e kill us here.
    # shellcheck disable=SC1090
    source "$ENV_FILE" 2>/dev/null || true
    set +a
    log_info "Loaded existing .env values as a base"
else
    log_info "No existing .env — creating new"
fi

# --- BOT_TOKEN (prompt only if missing; validate format, never block) ---
if [[ -z "${BOT_TOKEN:-}" ]]; then
    read -rp "BOT_TOKEN (from @BotFather): " BOT_TOKEN
    if [[ -n "$BOT_TOKEN" ]] && [[ ! "$BOT_TOKEN" =~ ^[0-9]{8,10}:[A-Za-z0-9_-]{35}$ ]]; then
        log_warn "BOT_TOKEN format doesn't match <digits>:<35-char-token> (may be invalid)"
    fi
fi

# --- POSTGRES_* (fixed per R-C29) ---
POSTGRES_USER="postgres"
POSTGRES_PASSWORD="postgres"
POSTGRES_DB="postgres"

# --- JWT_SECRET (R-C8: >= 32 chars; auto-generate, never prompt) ---
if [[ -z "${JWT_SECRET:-}" ]] || [[ ${#JWT_SECRET} -lt 32 ]]; then
    if command -v openssl >/dev/null 2>&1; then
        JWT_SECRET="$(openssl rand -base64 48 | tr -d '\n' | tr '+/' '-_')"
    else
        JWT_SECRET="$("$PYTHON_BIN" -c 'import secrets; print(secrets.token_urlsafe(48))')"
    fi
    log_info "JWT_SECRET auto-generated (${#JWT_SECRET} chars)"
else
    log_info "JWT_SECRET present (${#JWT_SECRET} chars)"
fi

# --- WEBAPP_URL (keep silently if present; prompt only if missing) ---
if [[ -n "${WEBAPP_URL:-}" ]]; then
    log_info "WEBAPP_URL = '$WEBAPP_URL' (kept from .env; re-run with -y to skip)"
elif [[ "$ASSUME_YES" != true ]]; then
    read -rp "WEBAPP_URL (press Enter to skip): " WEBAPP_URL
else
    WEBAPP_URL=""
fi

# --- REDIRECT_URL (preserve if present, otherwise prompt or set empty) ---
if [[ -z "${REDIRECT_URL:-}" ]]; then
    if [[ "$ASSUME_YES" == true ]]; then
        REDIRECT_URL=""
    else
        read -rp "REDIRECT_URL (press Enter to skip): " REDIRECT_URL
    fi
fi

# --- Validate REDIRECT_URL format (bare domains cause redirect loops) ---
if [[ -n "$REDIRECT_URL" ]]; then
    if [[ ! "$REDIRECT_URL" =~ ^https?:// ]]; then
        log_warn "REDIRECT_URL='$REDIRECT_URL' does not start with http:// or https://"
        log_warn "Bare domains (e.g. 'ddgo.com') cause redirect loops — browser"
        log_warn "treats them as relative paths on the current server."
        if confirm "Fix URL automatically (prepend https://)?"; then
            REDIRECT_URL="https://$REDIRECT_URL"
            log_ok "Fixed: REDIRECT_URL=$REDIRECT_URL"
        fi
    fi
fi

# --- TELEGRAM_WEBVIEW_VALIDATION (R-C10: dev-bypass warning) ---
TV="${TELEGRAM_WEBVIEW_VALIDATION:-true}"
if [[ -z "$TV" ]]; then TV="true"; fi
if [[ "$(to_lower "$TV")" == "false" ]] || [[ "$TV" == "0" ]]; then
    log_warn "R-C10: TELEGRAM_WEBVIEW_VALIDATION=false — dev bypass is NOT valid for production!"
fi

# --- PROXY_* (R-P18/R-P22: only written if present in existing .env) ---
PROXY_HOST="${PROXY_HOST:-}"
PROXY_PORT="${PROXY_PORT:-}"
PROXY_SCHEME="${PROXY_SCHEME:-}"

# --- Write CLEAN .env (allow-list only; drops ENTITY_SIMILARITY_THRESHOLD etc.) ---
UTC_DATE="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
{
    printf '# =============================================================================\n'
    printf '# Survival Map — .env\n'
    printf '# Generated: %s by scripts/prepare-deploy.sh\n' "$UTC_DATE"
    printf '# =============================================================================\n'
    printf '#\n'
    printf '# G-15 / R-C24: PARSER_API_ID и PARSER_API_HASH НЕ хранятся здесь!\n'
    printf '#   Используются ТОЛЬКО для одноразовой генерации session.session через\n'
    printf '#   scripts/gen_session.py (через env vars процесса, не в файлах).\n'
    printf '#   Нарушение — в логах или в .env — критична утечка аккаунта.\n'
    printf '#\n'
    printf '# Всё, что не перечислено ниже, хардкодится в core/settings.py.\n'
    printf '# =============================================================================\n'
    printf '\n'
    printf '# --- Secrets (R-C29) ---\n'
    printf 'BOT_TOKEN=%s\n' "$BOT_TOKEN"
    printf 'POSTGRES_USER=%s\n' "$POSTGRES_USER"
    printf 'POSTGRES_PASSWORD=%s\n' "$POSTGRES_PASSWORD"
    printf 'POSTGRES_DB=%s\n' "$POSTGRES_DB"
    printf '\n'
    printf '# --- JWT (R-C8: >= 32 chars) ---\n'
    printf 'JWT_SECRET=%s\n' "$JWT_SECRET"
    printf '\n'
    printf '# --- Deployment URLs ---\n'
    printf 'WEBAPP_URL=%s\n' "$WEBAPP_URL"
    printf 'REDIRECT_URL=%s\n' "$REDIRECT_URL"
    printf '\n'
    printf '# --- Telegram WebView Validation (R-C10: dev-bypass ONLY for local dev) ---\n'
    printf 'TELEGRAM_WEBVIEW_VALIDATION=%s\n' "$TV"
} > "$ENV_FILE"

if [[ -n "$PROXY_HOST" ]]; then
    {
        printf '\n'
        printf '# --- Optional Proxy (R-P18/R-P22: for parser) ---\n'
        printf 'PROXY_HOST=%s\n' "$PROXY_HOST"
    } >> "$ENV_FILE"
fi
if [[ -n "$PROXY_PORT" ]]; then
    printf 'PROXY_PORT=%s\n' "$PROXY_PORT" >> "$ENV_FILE"
fi
if [[ -n "$PROXY_SCHEME" ]]; then
    printf 'PROXY_SCHEME=%s\n' "$PROXY_SCHEME" >> "$ENV_FILE"
fi

# BOT_TOKEN / JWT_SECRET must never leak to stdout — heredoc/printf wrote them
# only into the file, never echoed.
chmod 600 "$ENV_FILE"
log_ok ".env written (chmod 600) with R-C29 allow-list only"

# ===========================================================================
# Phase 4: Session Generation (parser/session.session via gen_session.py)
# ===========================================================================
log_info "--- Phase 4: session generation ---"

if [[ ! -f "$GEN_SESSION" ]]; then
    log_fail "scripts/gen_session.py not found — cannot generate session"
    exit 1
fi

if [[ ! $venv_ok ]]; then
    log_fail "Python venv module unavailable — skipping session generation"
else
    # 4a. Existing session — offer to recreate (silent keep under -y).
    need_generate=false
    if [[ -f "$SESSION_FILE" ]]; then
        if [[ "$ASSUME_YES" == true ]]; then
            log_info "Using existing session.session (-y) — skipping generation"
        else
            if confirm "parser/session.session already exists. Пересоздать?"; then
                need_generate=true   # regenerate
            else
                log_info "Using existing session.session — skipping generation"
            fi
        fi
    else
        need_generate=true
    fi

    # -y cannot prompt for Telegram credentials, so we cannot generate a new
    # session non-interactively. Exit cleanly with guidance instead.
    if [[ "$need_generate" == true ]] && [[ "$ASSUME_YES" == true ]]; then
        log_fail "No existing session.session and -y given — cannot generate without PARSER_API_ID/PARSER_API_HASH (would prompt)"
        log_info "Re-run without -y to generate a session interactively:"
        log_info "  ./scripts/prepare-deploy.sh"
        exit 1
    fi

    if [[ "$need_generate" == true ]]; then
        # 4d. Create venv
        log_info "Creating .venv-session/ ..."
        "$PYTHON_BIN" -m venv "$VENV_DIR"

        # 4e. Install pinned deps from parser/requirements.txt + qrcode (R-C24: exact versions)
        log_info "Installing pinned dependencies (kurigram==2.2.9, tgcrypto==1.2.5) ..."
        "$VENV_DIR/bin/python" -m pip install --upgrade pip >/dev/null 2>&1 || true
        if ! "$VENV_DIR/bin/python" -m pip install -r "$PROJECT_ROOT/parser/requirements.txt" qrcode; then
            log_fail "Failed to install dependencies in .venv-session"
            log_info "Keeping .venv-session/ for debugging. Retry with:"
            log_info "  source .venv-session/bin/activate && python scripts/gen_session.py"
            exit 1
        fi

        # 4f. Prompt for credentials (R-C24: never stored, only in process env).
        API_ID=""
        API_HASH=""
        PHONE=""
        while [[ ! "$API_ID" =~ ^[0-9]{5,10}$ ]]; do
            read -rp "PARSER_API_ID (from https://my.telegram.org/apps): " API_ID
            if [[ ! "$API_ID" =~ ^[0-9]{5,10}$ ]]; then
                log_warn "PARSER_API_ID must be 5-10 digits; re-enter"
            fi
        done
        while [[ ! "$API_HASH" =~ ^[a-f0-9]{32}$ ]]; do
            read -rp "PARSER_API_HASH (32-char hex): " API_HASH
            if [[ ! "$API_HASH" =~ ^[a-f0-9]{32}$ ]]; then
                log_warn "PARSER_API_HASH must be 32 hex chars; re-enter"
            fi
        done
        read -rp "Phone number (format: +XXXXXXXXXXX, or blank for QR): " PHONE
        if [[ -n "$PHONE" ]] && [[ ! "$PHONE" =~ ^\+[0-9]{10,15}$ ]]; then
            log_warn "Phone format invalid (expected +XXXXXXXXXXX); continuing as QR-only"
            PHONE=""
        fi

        # 4g. Run gen_session.py with env-var prefix (credentials never touch disk).
        log_info "Starting session generation via gen_session.py"
        if [[ -z "$PHONE" ]]; then
            log_info "Telegram → Settings → Devices → 'Connect Device' → scan QR below"
        else
            log_info "Telegram will prompt for confirmation code (+ 2FA if set)"
        fi

        set +e
        source "$VENV_DIR/bin/activate"
        PARSER_API_ID="$API_ID" \
        PARSER_API_HASH="$API_HASH" \
        PARSER_PHONE="$PHONE" \
        SESSION_OUTPUT="$SESSION_FILE" \
            python "$GEN_SESSION"
        SESSION_EXIT=$?
        deactivate 2>/dev/null || true
        set -e

        # ALWAYS unset credentials immediately (G-15, R-C24); trap is the safety net.
        unset API_ID API_HASH PHONE

        # 4h. Handle result.
        if [[ "$SESSION_EXIT" -eq 0 ]]; then
            log_ok "Session generated successfully"
            # Cleanup temporary venv + any stray .venv-* / venv-tmp-* dirs.
            rm -rf "$VENV_DIR"
            for d in "$PROJECT_ROOT"/.venv-* "$PROJECT_ROOT"/venv-tmp-*; do
                if [[ -d "$d" ]]; then
                    rm -rf "$d"
                fi
            done
            log_ok "Temporary .venv-session/ and stray venv dirs removed"
        else
            log_fail "Session generation failed (exit code $SESSION_EXIT)"
            log_info "Keeping .venv-session/ for debugging — inspect logs in it"
            log_info "To retry: source .venv-session/bin/activate && python scripts/gen_session.py"
            exit 1
        fi
    fi
fi

# ===========================================================================
# Phase 5: Final Report + manual deploy command
# ===========================================================================
log_info "--- Phase 5: final report ---"

# 5a. Build checklist status
print_status() {
    local label="$1"; local ok="$2"; local icon="$3"
    printf "  %s %s\n" "$icon" "$label"
}

echo ""
printf "${BLUE}Checklist:${NC}\n"
if [[ "$docker_running" == true ]]; then
    print_status "Docker Engine running"            "ok" "✅"
else
    print_status "Docker Engine not running"        "bad" "❌"
fi
if [[ "$compose_v2" == true ]]; then
    print_status "Docker Compose v2 available"      "ok" "✅"
elif [[ "$compose_v1" == true ]]; then
    print_status "Docker Compose v1 only (v2 recommended)" "warn" "⚠️"
else
    print_status "Docker Compose not found"         "bad" "❌"
fi
if [[ -f "$ENV_FILE" ]]; then
    print_status ".env file exists"                 "ok" "✅"
else
    print_status ".env file missing"                "bad" "❌"
fi
if [[ -f "$SESSION_FILE" ]]; then
    print_status "parser/session.session exists"    "ok" "✅"
else
    print_status "parser/session.session missing"   "bad" "❌"
fi
if [[ -n "${BOT_TOKEN:-}" ]]; then
    print_status "BOT_TOKEN set"                    "ok" "✅"
else
    print_status "BOT_TOKEN not set"                "bad" "❌"
fi
if [[ -n "${POSTGRES_PASSWORD:-}" ]]; then
    print_status "POSTGRES_PASSWORD set"            "ok" "✅"
else
    print_status "POSTGRES_PASSWORD not set"        "bad" "❌"
fi
if [[ -n "${JWT_SECRET:-}" ]] && [[ ${#JWT_SECRET} -ge 32 ]]; then
    print_status "JWT_SECRET set (${#JWT_SECRET} chars)" "ok" "✅"
else
    print_status "JWT_SECRET missing or < 32 chars" "bad" "❌"
fi
if [[ "$(to_lower "$TV")" == "false" ]] || [[ "$TV" == "0" ]]; then
    print_status "TELEGRAM_WEBVIEW_VALIDATION=false (dev-bypass, NOT production!)" "warn" "⚠️"
else
    print_status "TELEGRAM_WEBVIEW_VALIDATION enabled" "ok" "✅"
fi

# 5b. Exit logic
if [[ "$ERRORS" -gt 0 ]]; then
    echo ""
    log_fail "Preparation incomplete — $ERRORS error(s), $WARNINGS warning(s)"
    log_info "Fix the issues above and re-run: ./scripts/prepare-deploy.sh"
    exit 1
fi

if [[ "$WARNINGS" -gt 0 ]]; then
    echo ""
    log_warn "Preparation complete with $WARNINGS warning(s)"
else
    echo ""
    log_ok "All checks passed — system ready for deployment"
fi

# 5c. Manual deploy command (G-3: never auto-start containers)
echo ""
log_info "=== MANUAL DEPLOYMENT (G-3) ==="
echo "  NOTE: This script does NOT start the deployment automatically."
echo "  Run the following command to deploy:"
echo ""
if [[ "$compose_v2" == true ]]; then
    printf "    ${GREEN}docker compose up --build${NC}\n"
else
    printf "    ${GREEN}docker-compose up --build${NC}\n"
fi
echo ""
if [[ -z "${PROXY_HOST:-}" ]]; then
    log_info "Tip: to use a proxy for Telegram (if blocked in your env),"
    log_info "add PROXY_HOST, PROXY_PORT, PROXY_SCHEME to .env manually."
fi
log_info "Press Ctrl+C to cancel, or run the command above to proceed."
