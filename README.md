# G-Helper Linux

Inoffizieller Linux-Nachbau von [G-Helper](https://github.com/seerge/g-helper) 0.291.0
für Fedora (Wayland, GNOME/KDE). Geschrieben in Python 3, PyGObject, GTK4 und
Libadwaita, abgestimmt auf das ASUS ROG Zephyrus G16 GU606AP (Core Ultra 9 386H,
RTX 5070 Laptop, 240-Hz-OLED).

> **Hinweis:** Nicht verbunden mit seerge/G-Helper oder ASUS. Das Programm schreibt
> direkt Leistungs-, Lüfter- und Akku-Einstellungen in die Hardware. Getestet nur auf
> dem GU606AP – Nutzung auf eigene Gefahr (siehe Haftungsausschluss der GPL-3.0).

## Installation

Voraussetzung: Fedora (Workstation/KDE) auf einem ASUS-ROG-Laptop.

```bash
git clone https://github.com/Breitmann177/g-helper-linux.git
cd g-helper-linux
./install.sh
```

Danach einmal ab- und wieder anmelden (für Tray-Icon und Tastenrechte).

**Aktualisieren:** `cd g-helper-linux && git pull && ./install.sh`

**Ohne Git:** Unter [Releases](https://github.com/Breitmann177/g-helper-linux/releases)
das neueste Archiv laden, entpacken und darin `./install.sh` ausführen.

Das Skript installiert die Abhängigkeiten per dnf, den Root-Helfer, die
Polkit-Aktion und -Regel (Gruppe `wheel` → kein Passwort; der Helfer führt nur fest eingebaute, geprüfte Operationen aus), die udev-Regel für
die ROG-Taste, den systemd-Dienst fürs Ladelimit, den Desktop-Eintrag und
aktiviert die GNOME-AppIndicator-Erweiterung für das Tray-Icon.

Start: `g-helper-linux` (oder App-Menü „G-Helper“). Optionen:

| Option        | Wirkung                                                      |
|---------------|--------------------------------------------------------------|
| `--hidden`    | nur im Tray starten (so startet der Autostart)               |
| `--fans`      | gleich mit geöffnetem „Lüfter und Leistung“ starten           |
| `--no-apply`  | Testmodus: nichts an die Hardware schreiben                   |

Ohne Installation aus dem Quellordner testen: `python3 -m ghelper --no-apply`

Entfernen: `./uninstall.sh` (die Einstellungen in `~/.config/g-helper-linux/` bleiben).

## Struktur

```
g-helper-linux/
├── install.sh / uninstall.sh
├── helper/ghelper-helper          Root-Helfer (pkexec): nur feste Operationen mit Wertprüfung
├── data/                          Polkit-Policy + Regel, udev-Regel, systemd-Dienst, .desktop, Icon
└── ghelper/
    ├── app.py                     Adw.Application, Fenster, Tray, Hotkeys, Telemetrie (1 s)
    ├── controller.py              G-Helper-Logik: Modi, GPU, Bildschirm, Automatisierung
    ├── config.py                  Einstellungen + Profil „Leise“ (Kurven, Watt)
    ├── hardware.py                sysfs/hwmon/armoury lesen, asusctl, supergfxctl, Helfer-Queue
    ├── display.py, icc.py         Mutter-D-Bus (Hz, Gamma), kscreen-doctor, ICC + colord
    ├── tray.py                    StatusNotifierItem + DBusMenu (ohne GTK3)
    ├── hotkeys.py                 evdev-Listener (ROG/M4/Fn+F5/Copilot)
    ├── ui_main.py, ui_fans.py, ui_extra.py, widgets.py, style.css, icons/
```

## Welche Funktion was auf dem System schreibt

| Funktion | Schnittstelle |
|---|---|
| Leise / Ausbalanciert / Turbo | `/sys/class/platform-profile/<asus-wmi>/profile` (`quiet` gibt es nur dort, nicht im zusammengefassten ACPI-Profil) + SoC-Slider |
| Windows Power Mode | EPP aller Kerne (`energy_performance_preference`) |
| CPU-Boost | `intel_pstate/no_turbo` |
| PL1 / PL2 | `asus-armoury` `ppt_pl1_spl`/`ppt_pl2_sppt` **und** Intel RAPL `constraint_0/1_power_limit_uw` |
| GPU to CPU Dynamic Boost | `asus-armoury` `nv_dynamic_boost` (nicht im Eco-Modus) |
| CPU Temperature Limit | `tcc_offset_degree_celsius` (TjMax 100 °C → 97 °C = Offset 3) |
| Lüfterkurven | hwmon `asus_custom_fan_curve` `pwmN_auto_pointM_*`, danach `pwmN_enable=1` |
| GPU Eco / Standard / Ultimativ | `supergfxctl -m Integrated / Hybrid / AsusMuxDgpu` |
| Optimiert | UPower `OnBattery` → Akku: Eco, Netzteil: Standard (im Extra-Fenster auf Ultimativ umstellbar) |
| 60 / 240 Hz / Auto | GNOME: `org.gnome.Mutter.DisplayConfig.ApplyMonitorsConfig`; KDE: `kscreen-doctor` |
| Flackerfreies Dimmen, Cold/Warm, Eyecare, Vivid | Mutter `SetCrtcGamma` (Software-Rampe, Hardware-Helligkeit bleibt) |
| Gamut sRGB | erzeugtes ICC-Profil (Panel = P3 nativ) über `colormgr` → Farbmanagement klemmt auf sRGB |
| Tastatur Farbe/Modus/Helligkeit | USB-HID wie G-Helper `Aura.cs` (Report `0x5D`/`0x5A`) über den Root-Helfer, zusätzlich `asus::kbd_backlight` |
| Slash Lighting | USB-HID `0B05:19B6`, Feature-Report `0x5D`, Port von G-Helper `SlashDevice.cs` |
| Ladebegrenzung | `asusctl battery limit` + `BAT1/charge_control_end_threshold` + `/etc/g-helper-linux/charge_limit` (Wiederherstellung nach Boot/Resume per systemd) |
| Lädt/Entlädt W | `current_now × voltage_now` (BAT1 hat kein `power_now`) |

Der Root-Helfer lässt eine Kurve nur zu, wenn die Temperaturen streng steigen,
die Drehzahlen nicht fallen und der Lüfter ab 85 °C mit mindestens 20 % läuft.
Die mitgelieferten Leise-Kurven erfüllen das.

## Ehrliche Einschränkungen (Stand: Kernel 7.3.4, asusctl 6.3.8)

* **Slash Lighting:** asusd bietet für das GU606AP kein Slash-Interface. Die App
  sendet deshalb wie G-Helper selbst die HID-Pakete aus `SlashDevice.cs`. Das Protokoll
  ist von ASUS nicht dokumentiert und nur nachgebaut.
* **FN-Lock:** Dieser Kernel bietet keine schreibbare FN-Lock-Datei (nur
  `fnlock_default`, read-only). Der Button speichert den Zustand. Umschalten geht mit **Fn+Esc**.
* **Cross Loading Processor Power:** Das zugehörige ASUS-Attribut (`ppt_apu_sppt`)
  fehlt in `asus-armoury`. Der Regler ist ausgegraut und speichert nur.
* **PL1/PL2:** `asus-armoury` meldet die PPT-Werte derzeit als „unavailable“.
  Wirksam ist deshalb das parallel gesetzte Intel-RAPL-Limit.
* **Hysterese:** Die EC-Lüfterkurve hat keine Hysterese. Die Regler speichern nur.
* **Lüfter-RPM-Achse:** Wie bei G-Helper sind das Prozent der Maximaldrehzahl
  (6400/6400/10000 RPM), umgerechnet in PWM 0–255. Die echte Drehzahl kann abweichen.
* **Fenster:** Wie im Original öffnet „Lüfter + Leistung“ ein eigenes Fenster „Lüfter und
  Leistung“, und „Extra“ öffnet „Extra-Einstellungen“. Wayland lässt Apps keine Fenster
  positionieren, deshalb legt GNOME die Position fest (nicht automatisch links daneben).
* **NVIDIA-Treiber nicht geladen** (z. B. `module_blacklist=nvidia,…`): Ohne Treiber
  wäre „Ultimativ“ (MUX) ein schwarzes internes Display. Deshalb sind Standard, Ultimativ und
  Optimiert dann ausgegraut, und Eco schaltet direkt per `asus-armoury dgpu_disable=1`. Sobald der
  Treiber geladen ist, werden die Buttons automatisch freigegeben.
* **Overlay (OSD):** Ohne Layer-Shell gibt es unter GNOME keine echten Overlays.
  Stattdessen kommen Desktop-Benachrichtigungen.
* **Dimmen/Farbtemperatur:** GNOME-Nachtlicht überschreibt die Gamma-Rampe. Die App
  setzt sie alle 5 s wieder, also Nachtlicht besser ausschalten. Unter KDE gibt es kein Software-Dimmen.
* **tuned-ppd** (Fedora-Standard) kann Profil/EPP beim Netzteilwechsel
  überschreiben. Bei Bedarf im Extra-Fenster unter „ASUS-Dienste“ stoppen.

## Design-Herkunft

Layout, Farben (`RForm.cs`), Button- und Reglerstil (`RButton.cs`, `Slider.cs`), Diagrammfarben
(`Fans.cs`), deutsche Texte (`Strings.de.resx`) und alle Icons stammen aus dem Original
[seerge/g-helper](https://github.com/seerge/g-helper) (GPL-3.0). Die Icons sind Icons8-Grafiken
aus dem Original-Repo und werden wie im Dark Theme auf `#F0F0F0` eingefärbt
(siehe `ghelper/icons/LICENSE-ICONS.txt`, Icons von [Icons8](https://icons8.com)). Dieses Projekt steht daher ebenfalls unter GPL-3.0.

## Lizenz

Copyright (C) 2026 Breitmann177. Basiert auf G-Helper,
Copyright (C) seerge und Mitwirkende.

GPL-3.0 – siehe [LICENSE](LICENSE). Geändert gegenüber dem Original: komplette
Neuimplementierung in Python/GTK4 für Linux (2026).
