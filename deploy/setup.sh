#!/usr/bin/env bash
# One-time (and repeatable) server setup for VendorCheck on Ubuntu/Debian.
# Run on the server:  sudo bash setup.sh
# It installs Python, fetches the code, creates a locked-down service user and the systemd
# service. It does NOT start the agent and never touches .env or the data file: copy those
# yourself (see DEPLOY.md), after stopping the agent everywhere else.
set -euo pipefail

REPO="${REPO:-https://github.com/sree-dhana/vendor-check.git}"
APP=/opt/vendorcheck
DIR="$APP/agent"

apt-get update -y
apt-get install -y python3 python3-venv git

id vendorcheck >/dev/null 2>&1 || useradd --system --home-dir "$APP" --shell /usr/sbin/nologin vendorcheck
mkdir -p "$APP"

if [ -d "$DIR/.git" ]; then
    git -C "$DIR" pull --ff-only
else
    git clone "$REPO" "$DIR"
fi

python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install --upgrade pip
"$DIR/.venv/bin/pip" install -r "$DIR/requirements.txt"

cp "$DIR/deploy/vendorcheck.service" /etc/systemd/system/vendorcheck.service
chown -R vendorcheck:vendorcheck "$APP"
for f in "$DIR/.env" "$DIR"/agent*_data.json; do
    [ -e "$f" ] && chmod 600 "$f"
done

systemctl daemon-reload
systemctl enable vendorcheck

echo
echo "Setup done. Next (see DEPLOY.md):"
echo "  1. Stop the agent on your laptop."
echo "  2. Copy .env and agent*_data.json into $DIR (owner vendorcheck, mode 600)."
echo "  3. sudo -u vendorcheck $DIR/.venv/bin/python -m pytest -q   (in $DIR)"
echo "  4. sudo systemctl start vendorcheck && journalctl -u vendorcheck -f"
