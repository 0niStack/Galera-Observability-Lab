#!/usr/bin/env bash
set -euo pipefail

# Run from the extracted project directory on each Galera node.
# This installs the collector and timer. Filebeat installation/configuration
# is intentionally separate because package/version choice depends on your
# OpenSearch compatibility requirements.

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo: sudo $0"
  exit 1
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

install -o root -g root -m 0755 \
  "$PROJECT_DIR/scripts/galera_status.py" \
  /usr/local/bin/galera_status.py

install -o root -g root -m 0644 \
  "$PROJECT_DIR/config/galera-status.service" \
  /etc/systemd/system/galera-status.service

install -o root -g root -m 0644 \
  "$PROJECT_DIR/config/galera-status.timer" \
  /etc/systemd/system/galera-status.timer

install -o root -g root -m 0644 \
  "$PROJECT_DIR/config/galera-status.logrotate" \
  /etc/logrotate.d/galera-status

install -o root -g root -m 0750 -d /var/log/galera
touch /var/log/galera/galera-status.json
chown root:adm /var/log/galera/galera-status.json
chmod 0640 /var/log/galera/galera-status.json

if [[ ! -f /etc/galera-monitor.cnf ]]; then
  install -o root -g root -m 0600 \
    "$PROJECT_DIR/config/mariadb-monitor.cnf.example" \
    /etc/galera-monitor.cnf
  echo "Created /etc/galera-monitor.cnf. Edit it and set the MariaDB monitor credentials."
else
  echo "Keeping existing /etc/galera-monitor.cnf"
fi

systemctl daemon-reload
systemctl enable --now galera-status.timer

echo
echo "Collector installed. Configure /etc/galera-monitor.cnf, then test:"
echo "  systemctl start galera-status.service"
echo "  journalctl -u galera-status.service -n 30 --no-pager"
echo "  tail -n 2 /var/log/galera/galera-status.json"
