"""Persistente Einstellungen (~/.config/g-helper-linux/config.json)."""
import copy
import json
import os
from pathlib import Path

APP_ID = "io.github.ghelper_linux"
APP_NAME = "G-Helper"
VERSION = "0.291.0"

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "g-helper-linux"
CONFIG_FILE = CONFIG_DIR / "config.json"

# Skala der Lüfterkurven-Diagramme (G-Helper zeigt % der Maximaldrehzahl als RPM)
FAN_MAX_RPM = {"cpu": 6400, "gpu": 6400, "mid": 10000}
FANS = ("cpu", "gpu", "mid")

# ---------------------------------------------------------------------------
# Profil "Leise" des GU606AP (Zephyrus G16 2026) – nur dort voreingestellt.
# Andere Modelle starten mit den Werkskurven/-grenzen der Firmware.
# ---------------------------------------------------------------------------
QUIET_CURVES = {
    "cpu": [[30, 0], [50, 0], [60, 0], [65, 0], [70, 2600], [80, 3000], [90, 3300], [100, 3600]],
    "gpu": [[30, 0], [50, 0], [60, 0], [65, 0], [70, 2900], [80, 3250], [90, 3500], [100, 3750]],
    "mid": [[30, 0], [50, 0], [60, 0], [65, 0], [70, 3400], [80, 4000], [90, 4500], [100, 5000]],
}

# Ausbalanciert: moderat; Turbo: früh und bis 100 %
BALANCED_CURVES = {
    "cpu": [[30, 0], [50, 1300], [60, 1900], [65, 2400], [70, 2900], [80, 3600], [90, 4300], [100, 5000]],
    "gpu": [[30, 0], [50, 1300], [60, 1900], [65, 2400], [70, 2900], [80, 3600], [90, 4300], [100, 5000]],
    "mid": [[30, 0], [50, 2000], [60, 3000], [65, 3700], [70, 4400], [80, 5500], [90, 6700], [100, 7800]],
}
TURBO_CURVES = {
    "cpu": [[30, 1300], [50, 1900], [60, 2600], [65, 3200], [70, 3800], [80, 4800], [90, 5800], [100, 6400]],
    "gpu": [[30, 1300], [50, 1900], [60, 2600], [65, 3200], [70, 3800], [80, 4800], [90, 5800], [100, 6400]],
    "mid": [[30, 2000], [50, 3000], [60, 4000], [65, 5000], [70, 6000], [80, 7500], [90, 9000], [100, 10000]],
}

POWER_MODES = ["Power Saver", "Balanced", "Performance"]
CPU_BOOST = ["Disabled", "Enabled", "Aggressive", "Efficient Enabled", "Efficient Aggressive"]
HYSTERESIS = ["Niedrig", "Mittel", "Hoch"]


def _mode(name, base, power_mode, boost, limits, pl1, pl2, cross, dyn, temp, curves_on, curves):
    return {
        "name": name,
        "base": base,                 # quiet | balanced | performance (Plattformprofil)
        "power_mode": power_mode,     # → EPP
        "cpu_boost": boost,           # → intel_pstate/no_turbo (+ EPP-Verschiebung)
        "limits_enabled": limits,
        "pl1": pl1, "pl2": pl2, "cross": cross, "dyn_boost": dyn, "temp_limit": temp,
        "curves_enabled": curves_on,
        "curves": copy.deepcopy(curves),
        "hyst_up": 1, "hyst_down": 1,
    }


def _board():
    try:
        with open("/sys/class/dmi/id/board_name") as f:
            return f.read().strip()
    except OSError:
        return ""


TUNED_BOARD = _board().startswith("GU606")      # Modell, für das die Voreinstellungen gemacht sind


def _default_modes():
    if TUNED_BOARD:
        return {
            "quiet": _mode("Leise", "quiet", "Balanced", "Disabled",
                           True, 80, 80, 50, 10, 97, True, QUIET_CURVES),
            "balanced": _mode("Ausbalanciert", "balanced", "Balanced", "Enabled",
                              False, 55, 65, 45, 15, 95, False, BALANCED_CURVES),
            "turbo": _mode("Turbo", "performance", "Performance", "Aggressive",
                           False, 110, 140, 90, 25, 100, True, TURBO_CURVES),
        }
    # Alle anderen: nur Plattformprofil + EPP, Lüfter und Watt bleiben bei der Firmware,
    # bis man selbst Kurven/Grenzen setzt (wie G-Helper beim ersten Start).
    return {
        "quiet": _mode("Leise", "quiet", "Power Saver", "Enabled",
                       False, 35, 45, 35, 5, 95, False, QUIET_CURVES),
        "balanced": _mode("Ausbalanciert", "balanced", "Balanced", "Enabled",
                          False, 55, 65, 45, 15, 95, False, BALANCED_CURVES),
        "turbo": _mode("Turbo", "performance", "Performance", "Aggressive",
                       False, 80, 100, 80, 25, 100, False, TURBO_CURVES),
    }


DEFAULTS = {
    "version": 1,
    "current_mode": "quiet" if TUNED_BOARD else "balanced",
    "mode_order": ["quiet", "balanced", "turbo"],
    "modes": _default_modes(),
    "gpu_pref": "",                 # eco | standard | ultimate | optimized ("" = nur Hardware anzeigen)
    "ultimate_enabled": False,      # Ultimativ (MUX) bewusst deaktiviert → Button grau
    "optimized_ac_target": "standard",
    "screen_mode": "max",           # auto | min | max (niedrigste / höchste Rate des Panels)
    "dim": 100,
    "splendid": "Default",
    "color_temp": "Cold",
    "gamut": "DCIP3",
    "slash": {"brightness": "Dunkel", "mode": "Dark", "interval": 0,
              "off_on_battery": True, "off_on_lid": True,
              "show_on_boot": True, "show_on_shutdown": True,
              "show_on_sleep": False, "battery_warning": True},
    "kbd": {"mode": "Statisch", "color": "#ffffff", "brightness": "high"},
    "overlay": False,
    "fn_lock": True,
    "fans_snap": True,
    "autostart": True,
    "keybinds": {
        "KEY_PROG3": {"action": "toggle_window", "command": ""},   # ROG / Armoury-Taste
        "KEY_PROG4": {"action": "toggle_window", "command": ""},   # M4
        "KEY_PROG1": {"action": "toggle_window", "command": ""},
        "KEY_FN_F5": {"action": "cycle_mode", "command": ""},      # Fn+F5 Lüftertaste
        "KEY_F23": {"action": "none", "command": ""},              # Copilot
    },
    "automation": {
        "mode_on_ac": "",            # leer = nicht umschalten
        "mode_on_battery": "",
        "auto_eco_battery": False,
        "slash_off_battery": True,
    },
    "factory": {},                   # Werkskurven je Plattformprofil (PWM), automatisch erfasst
    "tcc_default": None,             # ursprünglicher TCC-Offset
}


def _merge(dst, src):
    for k, v in src.items():
        if k in dst and isinstance(dst[k], dict) and isinstance(v, dict) and k not in ("modes", "keybinds", "factory"):
            _merge(dst[k], v)
        else:
            dst[k] = v
    return dst


class Config:
    def __init__(self):
        self.data = copy.deepcopy(DEFAULTS)
        self.load()

    def load(self):
        try:
            with open(CONFIG_FILE) as f:
                stored = json.load(f)
            _merge(self.data, stored)
        except FileNotFoundError:
            self.save()
        except (OSError, json.JSONDecodeError) as e:
            print(f"[config] konnte {CONFIG_FILE} nicht lesen: {e} – Standardwerte")
        # fehlende Standardmodi ergänzen
        for mid, m in DEFAULTS["modes"].items():
            self.data["modes"].setdefault(mid, copy.deepcopy(m))
        for mid in self.data["modes"]:
            if mid not in self.data["mode_order"]:
                self.data["mode_order"].append(mid)
        if self.data["current_mode"] not in self.data["modes"]:
            self.data["current_mode"] = DEFAULTS["current_mode"]
        # alte Versionen speicherten feste Hz ("60"/"240") → min/max, passt für jedes Panel
        sm = str(self.data.get("screen_mode", "max"))
        if sm.isdigit():
            self.data["screen_mode"] = "min" if int(sm) <= 60 else "max"

    def save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_FILE.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, CONFIG_FILE)

    # bequeme Zugriffe ------------------------------------------------------
    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value
        self.save()

    @property
    def mode(self):
        return self.data["modes"][self.data["current_mode"]]

    def mode_by_id(self, mid):
        return self.data["modes"][mid]

    def add_custom_mode(self, base_mid):
        base = self.data["modes"][base_mid]
        n = 1
        while f"custom{n}" in self.data["modes"]:
            n += 1
        mid = f"custom{n}"
        m = copy.deepcopy(base)
        m["name"] = f"Benutzerdefiniert {n}"
        self.data["modes"][mid] = m
        self.data["mode_order"].append(mid)
        self.save()
        return mid

    def reset_curves(self, mid):
        default = DEFAULTS["modes"].get(mid)
        if default:
            self.data["modes"][mid]["curves"] = copy.deepcopy(default["curves"])
        self.save()


def rpm_to_pwm(fan, rpm):
    return max(0, min(255, round(min(rpm, FAN_MAX_RPM[fan]) / FAN_MAX_RPM[fan] * 255)))


def pwm_to_rpm(fan, pwm):
    return round(pwm / 255 * FAN_MAX_RPM[fan] / 50) * 50
