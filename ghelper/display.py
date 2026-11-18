"""
Bildschirm: Bildwiederholrate (GNOME Mutter D-Bus / KDE kscreen-doctor),
flackerfreies Software-Dimmen + Farbtemperatur (Mutter SetCrtcGamma) und
Farbraum (ICC-Profil über colord).
"""
import math
import os
import re
import shutil
from pathlib import Path

from gi.repository import Gio, GLib

from . import icc
from .hardware import run

MUTTER_BUS = "org.gnome.Mutter.DisplayConfig"
MUTTER_PATH = "/org/gnome/Mutter/DisplayConfig"

# Weißpunkt relativ zu D65 (Schwarzkörper-Näherung, normiert auf 6500 K)
COLOR_TEMPS = {
    "Cold": (0.94, 0.97, 1.00),      # ≈ 7000 K
    "Neutral": (1.00, 1.00, 1.00),   # 6500 K
    "Warm": (1.00, 0.93, 0.85),      # ≈ 5500 K
}
EYECARE = (1.00, 0.86, 0.70)         # ≈ 4500 K

SPLENDID = ["Default", "Vivid", "Manual", "Eyecare"]
TEMPS = ["Cold", "Neutral", "Warm"]
GAMUTS = ["DCIP3", "sRGB", "Native", "Display P3"]


def _desktop():
    d = (os.environ.get("XDG_CURRENT_DESKTOP", "") + ":" + os.environ.get("DESKTOP_SESSION", "")).upper()
    if "KDE" in d or "PLASMA" in d:
        return "kde"
    if "GNOME" in d:
        return "gnome"
    return "other"


class Display:
    def __init__(self):
        self.desktop = _desktop()
        self.proxy = None
        if self.desktop == "gnome":
            try:
                self.proxy = Gio.DBusProxy.new_for_bus_sync(
                    Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None,
                    MUTTER_BUS, MUTTER_PATH, MUTTER_BUS, None)
            except GLib.Error as e:
                print(f"[display] Mutter nicht erreichbar: {e.message}")
        self.internal = "eDP-1"
        self._gamma_wanted = None   # (r, g, b)-Rampen, die wir zuletzt gesetzt haben

    # ------------------------------------------------------------------ Mutter
    def _call(self, method, args=None):
        return self.proxy.call_sync(method, args, Gio.DBusCallFlags.NONE, 5000, None)

    def _state(self):
        serial, monitors, logical, props = self._call("GetCurrentState").unpack()
        return serial, monitors, logical, props

    def _internal_monitor(self, monitors):
        for spec, modes, mprops in monitors:
            if spec[0].startswith("eDP") or mprops.get("is-builtin"):
                self.internal = spec[0]
                return spec, modes, mprops
        return None

    def available_rates(self):
        if self.proxy:
            try:
                _, monitors, _, _ = self._state()
                mon = self._internal_monitor(monitors)
                if mon:
                    cur = self._current_mode(mon[1])
                    return sorted({round(m[3]) for m in mon[1] if (m[1], m[2]) == (cur[1], cur[2])})
            except GLib.Error:
                pass
        return [60, 240]

    @staticmethod
    def _current_mode(modes):
        for m in modes:
            if m[6].get("is-current"):
                return m
        return modes[0]

    def get_refresh(self):
        if self.proxy:
            try:
                _, monitors, _, _ = self._state()
                mon = self._internal_monitor(monitors)
                if mon:
                    return round(self._current_mode(mon[1])[3])
            except GLib.Error:
                return None
        if self.desktop == "kde" and shutil.which("kscreen-doctor"):
            ok, out = run(["kscreen-doctor", "-o"], timeout=5)
            m = re.search(r"eDP-\d.*?(\d+x\d+)@(\d+)\*", out, re.S)
            if m:
                return int(m.group(2))
        return None

    def set_refresh(self, hz):
        """→ (ok, msg)"""
        if self.proxy:
            return self._mutter_set_refresh(hz)
        if self.desktop == "kde" and shutil.which("kscreen-doctor"):
            ok, out = run(["kscreen-doctor", "-o"], timeout=5)
            m = re.search(r"Output: \d+ (eDP-\d)", out)
            name = m.group(1) if m else "eDP-1"
            r = re.search(rf"{name}.*?(\d+x\d+)@\d+\*", out, re.S)
            res = r.group(1) if r else "2560x1600"
            return run(["kscreen-doctor", f"output.{name}.mode.{res}@{hz}"], timeout=10)
        return False, "Kein unterstützter Wayland-Compositor (GNOME/KDE) erkannt"

    def _mutter_set_refresh(self, hz):
        try:
            serial, monitors, logical, props = self._state()
        except GLib.Error as e:
            return False, e.message
        current = {}
        target_id = None
        for spec, modes, mprops in monitors:
            cur = self._current_mode(modes)
            current[spec[0]] = cur[0]
            if spec[0] == self.internal or spec[0].startswith("eDP"):
                vrr = cur[0].endswith("+vrr")
                cands = [m for m in modes if (m[1], m[2]) == (cur[1], cur[2]) and m[0].endswith("+vrr") == vrr]
                if not cands:
                    return False, "keine passenden Modi gefunden"
                best = min(cands, key=lambda m: abs(m[3] - hz))
                if abs(best[3] - hz) > 2:
                    return False, f"{hz} Hz wird vom Panel nicht angeboten"
                target_id = best[0]
                current[spec[0]] = best[0]
        if target_id is None:
            return False, "internes Display nicht gefunden"
        lms = []
        for x, y, scale, transform, primary, mons, _lprops in logical:
            lms.append((x, y, scale, transform, primary,
                        [(m[0], current[m[0]], {}) for m in mons]))
        aprops = {}
        if "layout-mode" in props:
            aprops["layout-mode"] = GLib.Variant("u", props["layout-mode"])
        # Methode 1 = temporär: kein "Anzeigeeinstellungen beibehalten?"-Dialog der Shell.
        # Die App setzt die gewählte Rate beim Start erneut.
        args = GLib.Variant("(uua(iiduba(ssa{sv}))a{sv})", (serial, 1, lms, aprops))
        try:
            self._call("ApplyMonitorsConfig", args)
            return True, f"{hz} Hz"
        except GLib.Error as e:
            return False, e.message

    # ---------------------------------------------------- Gamma / DC-Dimming
    def _crtc(self):
        serial, crtcs, outputs, *_ = self._call("GetResources").unpack()
        for out in outputs:
            name, crtc = out[4], out[2]
            if (name == self.internal or name.startswith("eDP")) and crtc >= 0:
                return serial, crtcs[crtc][0] if crtc < len(crtcs) else crtc
        return serial, None

    def set_color(self, dim_pct, splendid, temp):
        """Software-Dimmung + Farbtemperatur. → (ok, msg)"""
        if not self.proxy:
            return False, "Software-Dimmen benötigt GNOME (Mutter)"
        try:
            serial, crtc = self._crtc()
            if crtc is None:
                return False, "CRTC des internen Displays nicht gefunden"
            r, g, b = self._call("GetCrtcGamma", GLib.Variant("(uu)", (serial, crtc))).unpack()
            size = len(r) or 256
            ramps = build_ramps(size, dim_pct, splendid, temp)
            self._call("SetCrtcGamma", GLib.Variant("(uuaqaqaq)", (serial, crtc, *ramps)))
            self._gamma_wanted = ramps
            return True, f"{dim_pct}%"
        except GLib.Error as e:
            return False, e.message

    def gamma_drifted(self):
        """True, wenn jemand (Nachtlicht, Mutter) unsere Rampe überschrieben hat."""
        if not self.proxy or self._gamma_wanted is None:
            return False
        try:
            serial, crtc = self._crtc()
            r, g, b = self._call("GetCrtcGamma", GLib.Variant("(uu)", (serial, crtc))).unpack()
            mid = len(r) * 3 // 4
            want = self._gamma_wanted
            return abs(r[mid] - want[0][mid]) > 300 or abs(b[mid] - want[2][mid]) > 300
        except (GLib.Error, IndexError):
            return False

    # -------------------------------------------------------- ICC / Farbraum
    def set_gamut(self, gamut):
        """sRGB → Panel-Profil mit nativen P3-Primärfarben (Farbmanagement klemmt auf sRGB).
        DCIP3/Display P3/Native → eigenes Profil entfernen (Panel zeigt nativen Farbraum)."""
        if not shutil.which("colormgr"):
            return False, "colormgr nicht installiert"
        dev = self._colord_device()
        if not dev:
            return False, "colord kennt das interne Display nicht"
        icc_dir = Path.home() / ".local/share/icc"
        icc_dir.mkdir(parents=True, exist_ok=True)
        ours = icc_dir / "g-helper-panel-native.icc"
        # eigene Profile vom Gerät lösen
        ok, out = run(["colormgr", "device-get-profiles", dev], timeout=5)
        for path in re.findall(r"Object Path:\s+(\S+)", out):
            ok2, pinfo = run(["colormgr", "profile-get-properties", path], timeout=5)
            if "g-helper" in pinfo:
                run(["colormgr", "device-remove-profile", dev, path], timeout=5)
        if gamut != "sRGB":
            return True, f"{gamut}: nativer Panel-Farbraum"
        ours.write_bytes(icc.display_profile("G-Helper Panel (DCI-P3 nativ)", icc.PRIMARIES["Display P3"]))
        ok, out = run(["colormgr", "import-profile", str(ours)], timeout=10)
        ok, out = run(["colormgr", "find-profile-by-filename", str(ours)], timeout=5)
        m = re.search(r"Object Path:\s+(\S+)", out)
        if not m:
            return False, "Profil konnte nicht in colord importiert werden"
        prof = m.group(1)
        run(["colormgr", "device-add-profile", dev, prof], timeout=5)
        ok, out = run(["colormgr", "device-make-profile-default", dev, prof], timeout=5)
        return ok, "sRGB-Emulation aktiv" if ok else out

    @staticmethod
    def _colord_device():
        ok, out = run(["colormgr", "get-devices-by-kind", "display"], timeout=5)
        if not ok:
            return None
        blocks = out.split("Object Path:")
        for b in blocks[1:]:
            if re.search(r"Embedded:\s+Yes", b):
                return b.split()[0]
        return blocks[1].split()[0] if len(blocks) > 1 else None


def build_ramps(size, dim_pct, splendid, temp):
    dim = max(0.10, min(1.0, dim_pct / 100))
    wr, wg, wb = EYECARE if splendid == "Eyecare" else COLOR_TEMPS.get(temp, (1, 1, 1))
    ramps = ([], [], [])
    for i in range(size):
        v = i / (size - 1)
        if splendid == "Vivid":
            v = min(1.0, max(0.0, v - 0.05 * math.sin(2 * math.pi * v)))  # leichte S-Kurve
        for ch, w in zip(ramps, (wr, wg, wb)):
            ch.append(int(min(1.0, v * w * dim) * 65535))
    return ramps
