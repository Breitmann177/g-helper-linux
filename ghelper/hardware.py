"""
Hardware-Abstraktion für ASUS-Laptops (ROG/TUF/Zenbook/Vivobook) unter Linux.
Alles wird zur Laufzeit erkannt: Intel/AMD, NVIDIA/AMD/keine dGPU, 1–3 Lüfter, RGB oder nicht.

Lesen (ohne root):  sysfs/hwmon, power_supply, platform-profile, asus-armoury
Schreiben (root):   über den Polkit-Helfer /usr/libexec/g-helper-linux/ghelper-helper
Nutzer-Daemons:     asusctl (asusd, D-Bus) und supergfxctl (supergfxd, D-Bus)
"""
import glob
import json
import os
import queue
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

from gi.repository import GLib

ARMOURY = Path("/sys/class/firmware-attributes/asus-armoury/attributes")
NB_WMI = Path("/sys/devices/platform/asus-nb-wmi")
HELPER_INSTALLED = Path("/usr/libexec/g-helper-linux/ghelper-helper")

FAN_INDEX = {"cpu": 1, "gpu": 2, "mid": 3}


def read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def read_int(path, default=None):
    v = read(path)
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def find_hwmon(name):
    for d in glob.glob("/sys/class/hwmon/hwmon*"):
        if read(os.path.join(d, "name")) == name:
            return Path(d)
    return None


def run(cmd, timeout=15):
    """Befehl ausführen → (ok, ausgabe)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (r.stdout + ("\n" + r.stderr if r.stderr.strip() else "")).strip()
        return r.returncode == 0, out
    except FileNotFoundError:
        return False, f"{cmd[0]} nicht gefunden"
    except subprocess.TimeoutExpired:
        return False, f"{cmd[0]}: Zeitüberschreitung"


def run_async(func, callback=None, *args):
    """func(*args) in einem Thread ausführen, callback(result) im GTK-Thread."""
    def worker():
        try:
            res = func(*args)
        except Exception as e:  # noqa: BLE001
            res = e
        if callback:
            GLib.idle_add(callback, res)
    threading.Thread(target=worker, daemon=True).start()


# ---------------------------------------------------------------------------
# System-Infos
# ---------------------------------------------------------------------------

def model_title():
    fam = read("/sys/class/dmi/id/product_family") or "ASUS"
    board = read("/sys/class/dmi/id/board_name") or ""
    return f"{fam} {board}".strip()


def cpu_vendor():
    """→ "intel" | "amd" | "" """
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("vendor_id"):
                    v = line.split(":", 1)[1].strip()
                    return {"GenuineIntel": "intel", "AuthenticAMD": "amd"}.get(v, "")
    except OSError:
        pass
    return ""


def cpu_name():
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "CPU"


# ---------------------------------------------------------------------------
# Telemetrie
# ---------------------------------------------------------------------------

@dataclass
class Telemetry:
    cpu_temp: float | None = None
    gpu_temp: float | None = None
    cpu_fan: int | None = None
    gpu_fan: int | None = None
    mid_fan: int | None = None
    battery: float | None = None
    bat_status: str = ""
    bat_watts: float | None = None
    on_ac: bool = True
    charge_limit: int | None = None
    dgpu_active: bool = False
    extra: dict = field(default_factory=dict)


class Sensors:
    def __init__(self):
        self.fan_hwmon = find_hwmon("asus")
        self.curve_hwmon = find_hwmon("asus_custom_fan_curve")
        self.coretemp = find_hwmon("coretemp")
        self.pkg_temp = None
        if self.coretemp:
            for lab in self.coretemp.glob("temp*_label"):
                if (read(lab) or "").startswith("Package"):
                    self.pkg_temp = Path(str(lab).replace("_label", "_input"))
        k10 = find_hwmon("k10temp")             # AMD Ryzen: Tctl (bzw. Tdie)
        if self.pkg_temp is None and k10:
            labels = {read(l): Path(str(l).replace("_label", "_input")) for l in k10.glob("temp*_label")}
            self.pkg_temp = labels.get("Tdie") or labels.get("Tctl") or (k10 / "temp1_input")
        if self.pkg_temp is None:
            tz = find_hwmon("acpitz")
            self.pkg_temp = tz / "temp1_input" if tz else None
        self.fan_inputs = {}
        if self.fan_hwmon:
            for lab in self.fan_hwmon.glob("fan*_label"):
                name = (read(lab) or "").replace("_fan", "")
                self.fan_inputs[name] = Path(str(lab).replace("_label", "_input"))
        self.battery = None
        for d in sorted(glob.glob("/sys/class/power_supply/BAT*")):
            self.battery = Path(d)
            break
        self.ac = None
        for d in glob.glob("/sys/class/power_supply/*"):
            if read(os.path.join(d, "type")) == "Mains":
                self.ac = Path(d)
                break
        self.nvidia_pci = None
        for d in glob.glob("/sys/bus/pci/devices/*"):
            if read(os.path.join(d, "vendor")) == "0x10de" and (read(os.path.join(d, "class")) or "").startswith("0x03"):
                self.nvidia_pci = Path(d)
                break
        # AMD-dGPU (z. B. Radeon RX in G14/Strix): amdgpu-hwmon, dessen Gerät NICHT boot_vga ist
        self.amd_dgpu_temp = None
        for d in glob.glob("/sys/class/hwmon/hwmon*"):
            if read(os.path.join(d, "name")) == "amdgpu" and read(os.path.join(d, "device", "boot_vga")) == "0":
                self.amd_dgpu_temp = Path(d) / "temp1_input"
                break
        self._gpu_temp = None
        self._gpu_busy = False

    def dgpu_active(self):
        if not self.nvidia_pci or not self.nvidia_pci.exists():
            return False
        return read(self.nvidia_pci / "power" / "runtime_status") == "active"

    def _poll_gpu_temp(self):
        # nvidia-smi nur, wenn die dGPU ohnehin wach ist – sonst würde sie aufgeweckt
        ok, out = run(["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"], timeout=3)
        try:
            self._gpu_temp = float(out.splitlines()[0]) if ok else None
        except (ValueError, IndexError):
            self._gpu_temp = None
        self._gpu_busy = False

    def read(self):
        t = Telemetry()
        if self.pkg_temp:
            v = read_int(self.pkg_temp)
            t.cpu_temp = v / 1000 if v is not None else None
        for name, p in self.fan_inputs.items():
            setattr(t, f"{name}_fan", read_int(p))
        t.dgpu_active = self.dgpu_active()
        if t.dgpu_active and shutil.which("nvidia-smi"):
            if not self._gpu_busy:
                self._gpu_busy = True
                threading.Thread(target=self._poll_gpu_temp, daemon=True).start()
            t.gpu_temp = self._gpu_temp
        else:
            self._gpu_temp = None
        if t.gpu_temp is None and self.amd_dgpu_temp:
            v = read_int(self.amd_dgpu_temp)
            t.gpu_temp = v / 1000 if v is not None else None
        if self.battery:
            b = self.battery
            t.battery = None
            full, now = read_int(b / "charge_full"), read_int(b / "charge_now")
            if full and now is not None:
                t.battery = now / full * 100
            else:
                cap = read_int(b / "capacity")
                t.battery = float(cap) if cap is not None else None
            t.bat_status = read(b / "status", "")
            pw = read_int(b / "power_now")
            if pw is None:
                cur, volt = read_int(b / "current_now"), read_int(b / "voltage_now")
                pw = cur * volt / 1_000_000 if cur is not None and volt is not None else None
            if pw is not None:
                w = abs(pw) / 1_000_000
                t.bat_watts = -w if t.bat_status == "Discharging" else w
            t.charge_limit = read_int(b / "charge_control_end_threshold")
        if self.ac:
            t.on_ac = read(self.ac / "online") == "1"
        return t

    # -- Lüfterkurven der Firmware (für "Werkseinstellungen" / Referenzfläche)
    def read_curves_pwm(self):
        if not self.curve_hwmon:
            return None
        res, custom = {}, False
        for fan, i in FAN_INDEX.items():
            if not (self.curve_hwmon / f"pwm{i}_enable").exists():
                continue
            if read(self.curve_hwmon / f"pwm{i}_enable") == "1":
                custom = True
            pts = []
            for n in range(1, 9):
                tt = read_int(self.curve_hwmon / f"pwm{i}_auto_point{n}_temp")
                pp = read_int(self.curve_hwmon / f"pwm{i}_auto_point{n}_pwm")
                if tt is None or pp is None:
                    break
                pts.append([tt, pp])
            if len(pts) == 8:
                res[fan] = pts
        return res, custom


# ---------------------------------------------------------------------------
# Zustände lesen
# ---------------------------------------------------------------------------

def platform_profile():
    for d in glob.glob("/sys/class/platform-profile/*"):
        if read(os.path.join(d, "name")) == "asus-wmi":
            return read(os.path.join(d, "profile"))
    return read("/sys/firmware/acpi/platform_profile")


def armoury_attr(name):
    """→ dict(current, min, max) oder None, falls nicht vorhanden/verfügbar."""
    d = ARMOURY / name
    if not d.is_dir():
        legacy = NB_WMI / name
        if legacy.exists():
            return {"current": read_int(legacy), "min": None, "max": None}
        return None
    return {"current": read_int(d / "current_value"),
            "min": read_int(d / "min_value"), "max": read_int(d / "max_value")}


def epp():
    return read("/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference")


def cpu_boost_enabled():
    nt = read("/sys/devices/system/cpu/intel_pstate/no_turbo")
    if nt is not None:
        return nt == "0"
    b = read("/sys/devices/system/cpu/cpufreq/boost")
    return b == "1" if b is not None else None


def tcc_offset():
    for p in glob.glob("/sys/bus/pci/devices/*/tcc_offset_degree_celsius"):
        return read_int(p)
    return None


def fn_lock_path():
    for p in (NB_WMI / "fn_lock", ARMOURY / "fn_lock" / "current_value"):
        if p.exists():
            return p
    return None


def curve_fans():
    """Lüfter mit eigener Kurven-Schnittstelle (viele Modelle haben nur CPU + GPU, ohne „mid“)."""
    d = find_hwmon("asus_custom_fan_curve")
    if not d:
        return []
    return [fan for fan, i in FAN_INDEX.items() if (d / f"pwm{i}_enable").exists()]


def has_dgpu():
    """Gibt es überhaupt eine zweite GPU? Im Eco-Modus verschwindet sie vom PCI-Bus,
    deshalb zählen auch die ASUS-Schalter dgpu_disable / gpu_mux_mode."""
    if armoury_attr("dgpu_disable") or armoury_attr("gpu_mux_mode"):
        return True
    gpus = [d for d in glob.glob("/sys/bus/pci/devices/*")
            if (read(os.path.join(d, "class")) or "").startswith("0x03")]
    return len(gpus) > 1 or any(read(os.path.join(d, "vendor")) == "0x10de" for d in gpus)


def has_nvidia():
    """NVIDIA-dGPU vorhanden? Im Eco-Modus ist die Karte vom PCI-Bus verschwunden –
    dann zählt, ob es einen dGPU-Schalter gibt und der NVIDIA-Treiber installiert ist."""
    if Path("/sys/module/nvidia").exists():
        return True
    if has_dgpu() and not any((read(os.path.join(d, "class")) or "").startswith("0x03")
                              and read(os.path.join(d, "vendor")) == "0x1002"
                              and read(os.path.join(d, "boot_vga")) == "0"
                              for d in glob.glob("/sys/bus/pci/devices/*")):
        ok, out = run(["modinfo", "-n", "nvidia"], timeout=5)
        if ok and out.strip():
            return True
    return any(read(os.path.join(d, "vendor")) == "0x10de"
               and (read(os.path.join(d, "class")) or "").startswith("0x03")
               for d in glob.glob("/sys/bus/pci/devices/*"))


AURA_PIDS = {0x1A30, 0x1854, 0x1869, 0x1866, 0x19B6, 0x1822, 0x1837, 0x184A, 0x183D, 0x8502, 0x1807,
             0x17E0, 0x1ABE, 0x1B4C, 0x1B6E, 0x1B2C, 0x8854, 0x1CE7, 0x1CE6, 0x1BF2, 0x1BF3, 0x1CD7,
             0x1CD8, 0x4B42}     # wie im Root-Helfer (G-Helper AsusHid.cs)


def aura_supported():
    """RGB-Tastatur (ASUS-Aura-HID) vorhanden? Sonst gibt es nur Helligkeit."""
    for d in glob.glob("/sys/class/hidraw/hidraw*"):
        ue = read(os.path.join(d, "device", "uevent"), "") or ""
        hid = next((l.split("=", 1)[1] for l in ue.splitlines() if l.startswith("HID_ID=")), "")
        parts = hid.split(":")
        try:
            if len(parts) == 3 and int(parts[1], 16) == 0x0B05 and int(parts[2], 16) in AURA_PIDS:
                return True
        except ValueError:
            pass
    return False


KBD_LED = "/sys/class/leds/asus::kbd_backlight/brightness"


def capabilities():
    vendor = cpu_vendor()
    caps = {
        "cpu_vendor": vendor,
        "fan_curves": find_hwmon("asus_custom_fan_curve") is not None,
        "fans": curve_fans(),
        "tcc": bool(glob.glob("/sys/bus/pci/devices/*/tcc_offset_degree_celsius")),
        # AMD hat auch powercap/intel-rapl, aber nur zum Lesen – Limits gehen dort über asus-armoury
        "rapl": vendor == "intel" and bool(glob.glob("/sys/class/powercap/intel-rapl:0")),
        "dgpu": has_dgpu(),
        "nvidia": has_nvidia(),
        "aura": aura_supported(),
        "kbd_led": os.path.exists(KBD_LED),
        "epp": os.path.exists("/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference"),
        "boost": cpu_boost_enabled() is not None,
        "fn_lock": fn_lock_path() is not None,
        "asusctl": shutil.which("asusctl") is not None,
        "supergfxctl": shutil.which("supergfxctl") is not None,
        "helper_installed": HELPER_INSTALLED.exists(),
        "armoury": {},
    }
    for a in ("ppt_pl1_spl", "ppt_pl2_sppt", "ppt_pl3_fppt", "ppt_apu_sppt",
              "ppt_platform_sppt", "nv_dynamic_boost", "nv_temp_target", "gpu_mux_mode", "dgpu_disable"):
        info = armoury_attr(a)
        if info is not None and info["current"] is not None:
            caps["armoury"][a] = info
    return caps


# ---------------------------------------------------------------------------
# Root-Helfer (pkexec), serialisiert über eine Warteschlange
# ---------------------------------------------------------------------------

class RootHelper:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.q = queue.Queue()
        threading.Thread(target=self._loop, daemon=True).start()

    @property
    def path(self):
        # Nur den root-eigenen Helfer starten – nie eine vom Nutzer beschreibbare Kopie
        # aus dem Quellordner (die könnte jedes Programm des Nutzers verändern).
        return HELPER_INSTALLED

    @property
    def installed(self):
        return HELPER_INSTALLED.exists()

    def submit(self, ops, callback=None):
        """ops: Liste von dicts; callback(list[result]) im GTK-Thread."""
        if not ops:
            if callback:
                GLib.idle_add(callback, [])
            return
        self.q.put((ops, callback))

    def _loop(self):
        while True:
            ops, cb = self.q.get()
            res = self._run(ops)
            if cb:
                GLib.idle_add(cb, res)

    def _run(self, ops):
        if self.dry_run:
            print("[helper:dry-run]", json.dumps(ops, ensure_ascii=False))
            return [{"op": o.get("op"), "ok": True, "msg": "dry-run"} for o in ops]
        if not self.installed:
            return [{"op": o.get("op"), "ok": False, "msg": "Helfer nicht installiert (install.sh)"} for o in ops]
        cmd = ["pkexec", str(self.path)]
        try:
            r = subprocess.run(cmd, input=json.dumps({"ops": ops}), capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            return [{"op": o.get("op"), "ok": False, "msg": str(e)} for o in ops]
        if r.returncode in (126, 127):
            msg = "Autorisierung abgelehnt" if r.returncode == 126 else "Helfer nicht ausführbar"
            return [{"op": o.get("op"), "ok": False, "msg": msg} for o in ops]
        try:
            data = json.loads(r.stdout or "{}")
        except json.JSONDecodeError:
            data = {"error": (r.stderr or r.stdout).strip()}
        if "results" in data:
            return data["results"]
        return [{"op": o.get("op"), "ok": False, "msg": data.get("error", "unbekannter Fehler")} for o in ops]


# ---------------------------------------------------------------------------
# GPU (supergfxctl)
# ---------------------------------------------------------------------------

GFX_TO_UI = {"Integrated": "eco", "Hybrid": "standard", "AsusMuxDgpu": "ultimate",
             "NvidiaNoModeset": "standard", "Vfio": "eco", "AsusEgpu": "standard"}
UI_TO_GFX = {"eco": "Integrated", "standard": "Hybrid", "ultimate": "AsusMuxDgpu"}
GPU_SWITCH_TIMEOUT = 20      # Sekunden, danach gilt die Umschaltung als fehlgeschlagen


def nvidia_driver_loaded():
    return Path("/sys/module/nvidia").exists()


ECO_BLOCKED_MSG = ("Der NVIDIA-Treiber ist geladen (die dGPU wird z. B. von gnome-shell genutzt). "
                   "Eco ist nur möglich, wenn das Modul „nvidia“ nicht geladen ist – "
                   "z. B. nach einem Neustart mit module_blacklist=nvidia.")


def eco_blocked():
    """Sofortiges Abschalten (dgpu_disable) ist nur sicher, solange /sys/module/nvidia NICHT
    existiert. Ist der Treiber geladen, läuft Eco über supergfxctl und wird beim Abmelden fertig."""
    return nvidia_driver_loaded()


def gpu_pending_mode():
    """→ eco | standard | ultimate | None – von supergfxd vorgemerkter Moduswechsel."""
    ok, out = run(["supergfxctl", "-P"], timeout=5)
    if not ok:
        return None
    return GFX_TO_UI.get(out.strip().splitlines()[-1].strip()) if out.strip() else None


def nvidia_blacklisted():
    cmd = read("/proc/cmdline", "")
    for tok in cmd.split():
        if tok.startswith(("module_blacklist=", "modprobe.blacklist=")) and "nvidia" in tok.split("=", 1)[1].split(","):
            return True
    return False


def gpu_mode():
    """→ eco | standard | ultimate | None"""
    mux = armoury_attr("gpu_mux_mode")
    if mux and mux["current"] == 0:
        return "ultimate"
    dg = armoury_attr("dgpu_disable")
    if dg and dg["current"] == 1:
        return "eco"
    if has_dgpu() and (dg or mux):
        # direkt an der Hardware ablesen (supergfxd kann danebenliegen):
        # dGPU auf dem PCI-Bus → Standard, sonst Eco (Karte entfernt)
        on_bus = any((read(os.path.join(d, "class")) or "").startswith("0x03")
                     and read(os.path.join(d, "boot_vga")) == "0"
                     for d in glob.glob("/sys/bus/pci/devices/*"))
        return "standard" if on_bus else "eco"
    ok, out = run(["supergfxctl", "-g"], timeout=5)
    if ok:
        return GFX_TO_UI.get(out.strip().splitlines()[-1].strip())
    dg = armoury_attr("dgpu_disable")
    if dg and dg["current"] is not None:
        return "eco" if dg["current"] == 1 else "standard"
    return None


def gpu_pending():
    ok, out = run(["supergfxctl", "-P"], timeout=5)
    return out.strip() if ok else ""


def set_gpu_mode(ui_mode):
    """Blockierend – über run_async aufrufen. → (ok, msg, reboot_needed)"""
    target = UI_TO_GFX[ui_mode]
    ok, out = run(["supergfxctl", "-m", target], timeout=GPU_SWITCH_TIMEOUT)
    low = out.lower()
    reboot = "reboot" in low or "restart" in low or ui_mode == "ultimate"
    logout = "logout" in low or "log out" in low
    return ok, out, reboot, logout


# ---------------------------------------------------------------------------
# asusctl: Tastatur, Slash, Akku
# ---------------------------------------------------------------------------

AURA_MODES = {"Statisch": "static", "Atmend": "breathe", "Farbzyklus": "rainbow-cycle", "Stroboskop": "flash"}
KBD_LEVELS = {"off": "off", "low": "low", "med": "med", "high": "high"}

# G-Helper: comboMatrix (MatrixOff/Dim/Medium/Bright) und SlashDevice.Modes – deutsche Originaltexte
SLASH_BRIGHTNESS = {"Aus": 0, "Dunkel": 85, "Mittel": 170, "Hell": 255}
SLASH_MODES = ["Bounce", "Slash", "Loading", "Bit Stream", "Transmission", "Flow", "Flux", "Phantom",
               "Spectrum", "Hazard", "Interfacing", "Ramp", "Game Over", "Start", "Buzzer", "Static",
               "Ladezustand", "Audio Visualisierung", "Dark"]
# asusctl-Namen (rog-slash); "Dark" = Leiste aus
SLASH_ASUSCTL = {"Bounce": "Bounce", "Slash": "Slash", "Loading": "Loading", "Bit Stream": "BitStream",
                 "Transmission": "Transmission", "Flow": "Flow", "Flux": "Flux", "Phantom": "Phantom",
                 "Spectrum": "Spectrum", "Hazard": "Hazard", "Interfacing": "Interfacing", "Ramp": "Ramp",
                 "Game Over": "GameOver", "Start": "Start", "Buzzer": "Buzzer", "Static": "Static",
                 "Ladezustand": "BatteryLevel", "Audio Visualisierung": "Static"}


def kbd_aura(mode_label, color_hex):
    mode = AURA_MODES.get(mode_label, "static")
    c = color_hex.lstrip("#")
    if mode == "static":
        cmd = ["asusctl", "aura", "effect", "static", "-c", c]
    elif mode == "breathe":
        cmd = ["asusctl", "aura", "effect", "breathe", "--colour", c, "--colour2", "000000", "--speed", "med"]
    elif mode == "rainbow-cycle":
        cmd = ["asusctl", "aura", "effect", "rainbow-cycle", "--speed", "med"]
    else:
        cmd = ["asusctl", "aura", "effect", "flash", "-c", c]
    return run(cmd)


def kbd_brightness(level):
    return run(["asusctl", "leds", "set", KBD_LEVELS.get(level, "high")])


def kbd_brightness_get():
    ok, out = run(["asusctl", "leds", "get"], timeout=5)
    if ok:
        low = out.lower()
        for k in ("off", "low", "med", "high"):
            if low.rstrip().endswith(k):
                return k
    return None


_slash_supported = None


def slash_supported():
    """Wie G-Helper SlashDevice.Detect(): 0B05:19B6 mit Feature-Report 0x5D."""
    global _slash_supported
    if _slash_supported is None:
        _slash_supported = False
        for d in glob.glob("/sys/class/hidraw/hidraw*"):
            ue = read(os.path.join(d, "device", "uevent"), "") or ""
            if "HID_ID=0003:00000B05:000019B6" not in ue:
                continue
            try:
                with open(os.path.join(d, "device", "report_descriptor"), "rb") as f:
                    if bytes([0x85, 0x5D]) in f.read():
                        _slash_supported = True
            except OSError:
                pass
    return _slash_supported


def slash_long():
    """AppConfig.IsSlashLong(): GA405/GU405/GU606/GX651 → 35 LEDs, sonst 7."""
    board = read("/sys/class/dmi/id/board_name", "") or ""
    return any(m in board for m in ("GA405", "GU405", "GU606", "GX651"))


def slash_op(brightness_label, mode_label, opts, wake=False, battery=None, with_options=False):
    """→ Operation für den Root-Helfer (op_slash)."""
    op = {"op": "slash", "mode": mode_label,
          "brightness": list(SLASH_BRIGHTNESS).index(brightness_label) if brightness_label in SLASH_BRIGHTNESS else 0,
          "interval": int(opts.get("interval", 0)), "long": slash_long(), "wake": wake}
    if battery is not None:
        op["battery"] = round(battery, 1)
    if with_options:
        op["options"] = {k: bool(opts.get(k)) for k in
                         ("off_on_battery", "off_on_lid", "show_on_boot", "show_on_sleep", "battery_warning")}
    return op


def charge_oneshot():
    """G-Helper „Einmalig auf 100% aufladen“."""
    return run(["asusctl", "battery", "oneshot"])


def charge_limit_asusctl(value):
    return run(["asusctl", "battery", "limit", str(int(value))])


def versions():
    info = {}
    ok, out = run(["asusctl", "info"], timeout=5)
    for line in out.splitlines():
        if "Software version" in line:
            info["asusctl"] = line.split(":", 1)[1].strip()
    ok, out = run(["supergfxctl", "-v"], timeout=5)
    info["supergfxctl"] = out.strip() if ok else "–"
    info["kernel"] = os.uname().release
    return info


def check_updates():
    ok, out = run(["dnf", "-q", "check-update", "asusctl", "supergfxctl", "kernel"], timeout=120)
    # dnf check-update: rc 100 = Updates vorhanden, 0 = keine
    lines = [l for l in out.splitlines() if l.strip() and not l.startswith(("Last metadata", "Letzte"))]
    return lines
