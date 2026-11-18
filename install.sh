#!/usr/bin/env bash
# G-Helper Linux – Installer für Fedora (Wayland, GNOME/KDE)
# Ausführen als normaler Nutzer:  ./install.sh   (fragt einmal nach sudo)
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX=/opt/g-helper-linux
LIBEXEC=/usr/libexec/g-helper-linux

green() { printf '\e[1;32m==>\e[0m %s\n' "$*"; }
warn()  { printf '\e[1;33m!! \e[0m %s\n' "$*"; }

if [[ $EUID -eq 0 ]]; then
    echo "Bitte als normaler Nutzer starten (nicht mit sudo) – das Skript fragt selbst nach sudo."
    exit 1
fi
if ! grep -qi fedora /etc/os-release; then
    warn "Kein Fedora erkannt – das Skript ist für Fedora geschrieben, fahre trotzdem fort."
fi
if ! id -nG "$USER" | grep -qw wheel; then
    warn "Nutzer $USER ist nicht in der Gruppe 'wheel' – Hardware-Änderungen fragen dann nach dem Passwort."
fi

green "Abhängigkeiten installieren (dnf)"
PKGS=(python3 python3-gobject gtk4 libadwaita python3-evdev colord polkit
      gnome-shell-extension-appindicator wireplumber playerctl)
sudo dnf install -y "${PKGS[@]}"

if ! command -v asusctl >/dev/null || ! command -v supergfxctl >/dev/null; then
    green "asusctl/supergfxctl fehlen – asus-linux COPR aktivieren"
    sudo dnf copr enable -y lukenukem/asus-linux
    sudo dnf install -y asusctl supergfxctl
fi

green "Programmdateien nach $PREFIX kopieren"
sudo rm -rf "$PREFIX"
sudo install -d -m 0755 "$PREFIX"
sudo cp -r "$SRC/ghelper" "$PREFIX/"
sudo find "$PREFIX" -name '__pycache__' -prune -exec rm -rf {} +
sudo chown -R root:root "$PREFIX"
sudo chmod -R u=rwX,go=rX "$PREFIX"

green "Root-Helfer nach $LIBEXEC installieren"
sudo install -d -m 0755 "$LIBEXEC"
sudo install -m 0755 -o root -g root "$SRC/helper/ghelper-helper" "$LIBEXEC/ghelper-helper"

green "Starter /usr/local/bin/g-helper-linux"
sudo tee /usr/local/bin/g-helper-linux >/dev/null <<EOF
#!/bin/sh
export PYTHONPATH="$PREFIX\${PYTHONPATH:+:\$PYTHONPATH}"
exec /usr/bin/python3 -m ghelper "\$@"
EOF
sudo chmod 0755 /usr/local/bin/g-helper-linux

green "Polkit-Aktion und -Regel (kein Passwort für wheel)"
sudo install -m 0644 "$SRC/data/io.github.ghelper-linux.policy" /usr/share/polkit-1/actions/
sudo install -m 0644 "$SRC/data/49-g-helper-linux.rules" /etc/polkit-1/rules.d/

green "udev-Regel für ROG-/M4-Taste"
sudo install -m 0644 "$SRC/data/90-g-helper-linux.rules" /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=input || true

green "systemd-Dienst für das Ladelimit"
sudo install -m 0644 "$SRC/data/g-helper-linux-charge-limit.service" /etc/systemd/system/
sudo install -d -m 0755 /etc/g-helper-linux
if [[ ! -f /etc/g-helper-linux/charge_limit ]]; then
    for b in /sys/class/power_supply/BAT*/charge_control_end_threshold; do
        [[ -r $b ]] && sudo sh -c "cat '$b' > /etc/g-helper-linux/charge_limit" && break
    done
fi
sudo systemctl daemon-reload
sudo systemctl enable g-helper-linux-charge-limit.service

green "asusd / supergfxd aktivieren"
sudo systemctl enable --now asusd.service || warn "asusd konnte nicht gestartet werden"
sudo systemctl enable --now supergfxd.service || warn "supergfxd konnte nicht gestartet werden"

green "Desktop-Eintrag und Icon"
sudo install -m 0644 "$SRC/data/g-helper-linux.desktop" /usr/share/applications/
sudo install -D -m 0644 "$SRC/data/g-helper-linux.svg" /usr/share/icons/hicolor/scalable/apps/g-helper-linux.svg
sudo gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true

if [[ "${XDG_CURRENT_DESKTOP:-}" == *GNOME* ]] && command -v gnome-extensions >/dev/null; then
    green "GNOME AppIndicator-Erweiterung für das Tray-Icon aktivieren"
    gnome-extensions enable appindicatorsupport@rgcjonas.gmail.com 2>/dev/null \
        || warn "Erweiterung erst nach Ab-/Anmelden verfügbar – danach erneut: gnome-extensions enable appindicatorsupport@rgcjonas.gmail.com"
fi

if systemctl is-active -q tuned-ppd || systemctl is-active -q power-profiles-daemon; then
    warn "tuned-ppd / power-profiles-daemon ist aktiv und kann Leistungsmodus/EPP überschreiben."
    warn "Bei Konflikten im Extra-Fenster unter 'Dienste' stoppen."
fi

green "Fertig. Start mit:  g-helper-linux   (oder über das App-Menü 'G-Helper')"
