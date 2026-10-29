#!/bin/sh
set -eu
test "$(id -u)" = 0
test "$(pwd -P)" = /opt/fruitsatelier-discord
if systemctl is-active --quiet fruitsatelier-qabot; then
    echo 'Stop the legacy service before installing this replacement.' >&2
    exit 1
fi
getent group fruitsatelier-qabot >/dev/null || groupadd --system fruitsatelier-qabot
if ! id fruitsatelier-qabot >/dev/null 2>&1; then
    useradd --system --gid fruitsatelier-qabot --home-dir /var/lib/fruitsatelier-discord --shell /usr/sbin/nologin fruitsatelier-qabot
fi
install -d -o root -g fruitsatelier-qabot -m 0750 /etc/fruitsatelier-discord
install -d -o fruitsatelier-qabot -g fruitsatelier-qabot -m 0700 /var/lib/fruitsatelier-discord
if [ ! -f /etc/fruitsatelier-discord/discord-token ] && [ -f /etc/fruitsatelier-qabot/discord-token ]; then
    install -o root -g fruitsatelier-qabot -m 0640 /etc/fruitsatelier-qabot/discord-token /etc/fruitsatelier-discord/discord-token
fi
python3 -m venv venv
venv/bin/python -m pip install -v -r requirements.txt
install -o root -g root -m 0644 deploy/fruitsatelier-discord.service /etc/systemd/system/fruitsatelier-discord.service
systemctl daemon-reload
echo 'Installed. The service remains stopped until credentials are configured.'
