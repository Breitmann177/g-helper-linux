#!/usr/bin/env bash
# G-Helper Linux entfernen (Konfiguration in ~/.config/g-helper-linux bleibt erhalten)
set -euo pipefail
sudo systemctl disable g-helper-linux-charge-limit.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/g-helper-linux-charge-limit.service
sudo systemctl daemon-reload
sudo rm -rf /opt/g-helper-linux /usr/libexec/g-helper-linux /etc/g-helper-linux
sudo rm -f /usr/local/bin/g-helper-linux \
    /usr/share/polkit-1/actions/io.github.ghelper-linux.policy \
    /etc/polkit-1/rules.d/49-g-helper-linux.rules \
    /etc/udev/rules.d/90-g-helper-linux.rules \
    /usr/share/applications/g-helper-linux.desktop \
    /usr/share/icons/hicolor/scalable/apps/g-helper-linux.svg
sudo udevadm control --reload-rules
rm -f "$HOME/.config/autostart/g-helper-linux.desktop"
echo "G-Helper Linux entfernt."
