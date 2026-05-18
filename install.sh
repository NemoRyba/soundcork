#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="SoundCork"
SERVICE_NAME="${SERVICE_NAME:-soundcork}"
PORT="${SOUNDCORK_PORT:-8000}"
HOST="${SOUNDCORK_HOST:-0.0.0.0}"
DATA_DIR="${DATA_DIR:-/var/lib/soundcork}"
LOG_DIR="${LOG_DIR:-/var/log/soundcork}"
PYTHON_BIN="${PYTHON_BIN:-}"
SKIP_APT="${SKIP_APT:-0}"
SKIP_SERVICE="${SKIP_SERVICE:-0}"
UPDATE_REPO="${UPDATE_REPO:-0}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_REPO_URL="$(git -C "$SCRIPT_DIR" config --get remote.origin.url 2>/dev/null || true)"
REPO_URL="${REPO_URL:-${DEFAULT_REPO_URL:-https://github.com/NemoRyba/soundcork.git}}"

if [ -f "$SCRIPT_DIR/requirements.txt" ] && [ -d "$SCRIPT_DIR/soundcork" ]; then
    DEFAULT_INSTALL_DIR="$SCRIPT_DIR"
else
    DEFAULT_INSTALL_DIR="$HOME/soundcork"
fi

INSTALL_DIR="${INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"
APP_DIR="$INSTALL_DIR/soundcork"
VENV_DIR="${VENV_DIR:-$INSTALL_DIR/.venv}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
    SERVICE_USER="${SERVICE_USER:-$SUDO_USER}"
else
    SERVICE_USER="${SERVICE_USER:-$(id -un)}"
fi
SERVICE_GROUP="${SERVICE_GROUP:-$(id -gn "$SERVICE_USER" 2>/dev/null || id -gn)}"

log() {
    printf "\n==> %s\n" "$*"
}

die() {
    printf "\nERROR: %s\n" "$*" >&2
    exit 1
}

have() {
    command -v "$1" >/dev/null 2>&1
}

sudo_run() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    elif have sudo; then
        sudo "$@"
    else
        die "sudo is required for installing packages and the systemd service"
    fi
}

run_as_service_user() {
    if [ "$(id -u)" -eq 0 ] && [ "$SERVICE_USER" != "root" ]; then
        if have sudo; then
            sudo -H -u "$SERVICE_USER" "$@"
        elif have runuser; then
            runuser -u "$SERVICE_USER" -- "$@"
        else
            die "Cannot run commands as $SERVICE_USER; install sudo or run as that user"
        fi
    else
        "$@"
    fi
}

python_is_312_or_newer() {
    "$1" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
PY
}

python_version() {
    "$1" - <<'PY'
import sys
print(".".join(str(part) for part in sys.version_info[:3]))
PY
}

detect_ip() {
    local detected=""
    if have hostname; then
        detected="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
    fi
    if [ -z "$detected" ] && have ip; then
        detected="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i=1;i<=NF;i++) if ($i=="src") {print $(i+1); exit}}' || true)"
    fi
    printf "%s" "$detected"
}

install_apt_packages() {
    if [ "$SKIP_APT" = "1" ]; then
        log "Skipping apt package installation because SKIP_APT=1"
        return
    fi

    if ! have apt-get; then
        log "apt-get not found; skipping system package installation"
        return
    fi

    log "Installing Raspberry Pi OS / Debian packages"
    local packages=(
        avahi-daemon
        build-essential
        ca-certificates
        curl
        git
        libffi-dev
        libssl-dev
        python3
        python3-dev
        python3-pip
        python3-venv
    )

    sudo_run apt-get update

    local candidate
    for candidate in python3.12 python3.12-dev python3.12-venv; do
        if apt-cache show "$candidate" >/dev/null 2>&1; then
            packages+=("$candidate")
        fi
    done

    sudo_run env DEBIAN_FRONTEND=noninteractive apt-get install -y "${packages[@]}"

    if have systemctl && systemctl list-unit-files avahi-daemon.service >/dev/null 2>&1; then
        sudo_run systemctl enable --now avahi-daemon >/dev/null 2>&1 || true
    fi
}

choose_python() {
    if [ -n "$PYTHON_BIN" ]; then
        have "$PYTHON_BIN" || die "PYTHON_BIN was set to '$PYTHON_BIN' but it is not executable"
        python_is_312_or_newer "$PYTHON_BIN" || die "$PYTHON_BIN is Python $(python_version "$PYTHON_BIN"); Python 3.12 or newer is required"
        return
    fi

    if have python3.12 && python_is_312_or_newer python3.12; then
        PYTHON_BIN="python3.12"
    elif have python3 && python_is_312_or_newer python3; then
        PYTHON_BIN="python3"
    else
        die "Python 3.12 or newer was not found. Install python3.12/python3.12-venv for your Pi OS release, then rerun this script."
    fi

    log "Using Python $("$PYTHON_BIN" --version 2>&1)"
}

prepare_repo() {
    if [ ! -f "$INSTALL_DIR/requirements.txt" ] || [ ! -d "$APP_DIR" ]; then
        log "Repository not found at $INSTALL_DIR; cloning $REPO_URL"
        sudo_run install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0755 "$INSTALL_DIR"
        run_as_service_user git clone "$REPO_URL" "$INSTALL_DIR"
    elif [ "$UPDATE_REPO" = "1" ] && [ -d "$INSTALL_DIR/.git" ]; then
        log "Updating repository with git pull --ff-only"
        run_as_service_user git -C "$INSTALL_DIR" pull --ff-only
    fi

    [ -f "$INSTALL_DIR/requirements.txt" ] || die "requirements.txt not found in $INSTALL_DIR"
    [ -f "$APP_DIR/main.py" ] || die "soundcork/main.py not found in $APP_DIR"
}

prepare_directories() {
    case "$INSTALL_DIR $VENV_DIR $DATA_DIR $LOG_DIR" in
        *" "*) die "Paths with spaces are not supported by this installer. Use INSTALL_DIR, VENV_DIR, DATA_DIR, or LOG_DIR without spaces." ;;
    esac

    log "Creating data and log directories"
    sudo_run install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0755 "$DATA_DIR"
    sudo_run install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0755 "$LOG_DIR"
    sudo_run install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0755 "$LOG_DIR/unhandled"

    if [ "$(id -u)" -eq 0 ] && [ "$SERVICE_USER" != "root" ]; then
        sudo_run chown -R "$SERVICE_USER:$SERVICE_GROUP" "$INSTALL_DIR"
    fi
}

write_private_env() {
    local detected_ip base_url tmp_env
    detected_ip="$(detect_ip)"
    if [ -n "${BASE_URL:-}" ]; then
        base_url="$BASE_URL"
    elif [ -n "$detected_ip" ]; then
        base_url="http://${detected_ip}:${PORT}"
    else
        base_url="http://$(hostname).local:${PORT}"
    fi

    log "Writing private configuration to $APP_DIR/.env.private"
    tmp_env="$(mktemp)"
    cat > "$tmp_env" <<EOF
base_url = "$base_url"
data_dir = "$DATA_DIR"
unhandled_log_dir = "$LOG_DIR/unhandled"
EOF
    sudo_run install -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0600 "$tmp_env" "$APP_DIR/.env.private"
    rm -f "$tmp_env"
}

install_python_dependencies() {
    log "Creating virtual environment and installing Python dependencies"
    run_as_service_user "$PYTHON_BIN" -m venv "$VENV_DIR"
    run_as_service_user "$VENV_DIR/bin/python" -m pip install --upgrade pip wheel setuptools
    run_as_service_user "$VENV_DIR/bin/python" -m pip install -r "$INSTALL_DIR/requirements.txt"

    log "Checking SoundCork imports"
    run_as_service_user env PYTHONPATH="$INSTALL_DIR" APP_DIR="$APP_DIR" VENV_PY="$VENV_DIR/bin/python" bash -c 'cd "$APP_DIR" && "$VENV_PY" -c "import main; print(\"SoundCork import check ok\")"'
}

install_systemd_service() {
    if [ "$SKIP_SERVICE" = "1" ]; then
        log "Skipping systemd service installation because SKIP_SERVICE=1"
        return
    fi

    have systemctl || die "systemctl not found. Set SKIP_SERVICE=1 to install dependencies without creating a service."

    log "Installing systemd service $SERVICE_NAME"
    local tmp_service
    tmp_service="$(mktemp)"
    cat > "$tmp_service" <<EOF
[Unit]
Description=SoundCork Bose SoundTouch replacement server
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$APP_DIR
Environment=PYTHONPATH=$INSTALL_DIR
ExecStart=$VENV_DIR/bin/gunicorn -c gunicorn_conf.py -b $HOST:$PORT --access-logfile $LOG_DIR/access.log --error-logfile $LOG_DIR/error.log main:app
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    sudo_run install -m 0644 "$tmp_service" "$SERVICE_FILE"
    rm -f "$tmp_service"
    sudo_run systemctl daemon-reload
    sudo_run systemctl enable --now "$SERVICE_NAME"
}

verify_service() {
    if [ "$SKIP_SERVICE" = "1" ]; then
        return
    fi

    log "Verifying local HTTP endpoint"
    sleep 2
    if ! sudo_run systemctl is-active --quiet "$SERVICE_NAME"; then
        sudo_run systemctl --no-pager --full status "$SERVICE_NAME" || true
        die "$SERVICE_NAME did not start"
    fi

    if have curl; then
        curl -fsS "http://127.0.0.1:${PORT}/" >/dev/null || die "SoundCork service is running, but http://127.0.0.1:${PORT}/ did not respond"
    fi
}

main() {
    log "$APP_NAME Raspberry Pi installer"
    printf "Install dir : %s\n" "$INSTALL_DIR"
    printf "Service user: %s\n" "$SERVICE_USER"
    printf "Data dir    : %s\n" "$DATA_DIR"
    printf "Port        : %s\n" "$PORT"

    install_apt_packages
    choose_python
    prepare_repo
    prepare_directories
    write_private_env
    install_python_dependencies
    install_systemd_service
    verify_service

    local shown_host
    shown_host="$(detect_ip)"
    [ -n "$shown_host" ] || shown_host="$(hostname).local"

    log "Installation complete"
    printf "Open:   http://%s:%s/miniapp\n" "$shown_host" "$PORT"
    printf "Admin:  http://%s:%s/admin\n" "$shown_host" "$PORT"
    printf "Logs:   sudo journalctl -u %s -f\n" "$SERVICE_NAME"
    printf "Config: %s/.env.private\n" "$APP_DIR"
}

main "$@"
