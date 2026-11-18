"""Zentrale Logik: verbindet Konfiguration, Hardware und UI (wie G-Helpers Program/Modes)."""
import copy
import os
from pathlib import Path

from gi.repository import Gio, GLib

from . import hardware as hw
from .config import DEFAULTS, FANS, pwm_to_rpm, rpm_to_pwm
from .display import Display

EPP_SCALE = ["power", "balance_power", "balance_performance", "performance"]
POWER_MODE_EPP = {"Power Saver": 1, "Balanced": 2, "Performance": 3}
BOOST_EPP_SHIFT = {"Disabled": 0, "Enabled": 0, "Aggressive": 1, "Efficient Enabled": -1, "Efficient Aggressive": 0}

GPU_LABELS = {"eco": "Nur iGPU", "standard": "iGPU + dGPU", "ultimate": "Nur dGPU", None: "–"}
AUTOSTART = Path.home() / ".config/autostart/g-helper-linux.desktop"


class Controller:
    def __init__(self, cfg, dry_run=False):
        self.cfg = cfg
        self.dry_run = dry_run
        self.helper = hw.RootHelper(dry_run)
        self.sensors = hw.Sensors()
        self.display = Display()
        self.caps = hw.capabilities()
        self.listeners = []
        self.gpu_hw = None              # tatsächlicher GPU-Modus
        # NVIDIA-Treiber per Kernel-Parameter gesperrt → nur Eco ist sicher (kein MUX/Hybrid-Wechsel)
        self.nvidia_ok = hw.nvidia_driver_loaded() and not hw.nvidia_blacklisted()
        self.gpu_busy = False
        self.on_ac = True
        self.telemetry = self.sensors.read()
        self.toast = lambda msg: print("[toast]", msg)
        self.osd = lambda title, body="": None
        self.ask_reboot = lambda msg: None
        self._upower = None

    # ------------------------------------------------------------- Ereignisse
    def connect(self, fn):
        self.listeners.append(fn)

    def emit(self, event, data=None):
        for fn in list(self.listeners):
            fn(event, data)

    def _report(self, results, what):
        errs = [f"{r.get('op')}: {r.get('msg')}" for r in results if not r.get("ok")]
        if errs:
            self.toast(f"{what}: " + "; ".join(errs[:3]))
        return not errs

    # ------------------------------------------------------------------ Start
    def startup(self, apply=True):
        t = self.sensors.read()
        self.telemetry, self.on_ac = t, t.on_ac
        self._capture_factory()
        self._watch_power()
        self.refresh_gpu()
        if not apply:
            return
        if self.cfg["autostart"] and not self.autostart_enabled():
            self.set_autostart(True)
        if self.helper.installed or self.dry_run:
            self.apply_mode()
            self.apply_kbd()
            GLib.timeout_add_seconds(8, lambda: (self.apply_kbd(), False)[1])
            if self.cfg["fn_lock"] and self.caps["fn_lock"]:
                self.set_fn_lock(True, quiet=True)
        else:
            self.toast("Helfer nicht installiert – bitte install.sh ausführen (Modus wird nicht automatisch gesetzt)")
        self.apply_screen()
        c = self.cfg
        if c["dim"] != 100 or c["splendid"] != "Default" or c["color_temp"] != "Neutral":
            self.set_color(apply_only=True)
        if c["gamut"] == "sRGB":
            self.set_gamut("sRGB")
        if hw.slash_supported():
            self.apply_slash()

    # ------------------------------------------------------------------ Modi
    def mode_label(self, mid=None):
        mid = mid or self.cfg["current_mode"]
        m = self.cfg.mode_by_id(mid)
        plus = "+" if (m["limits_enabled"] or m["curves_enabled"]) and mid != "turbo" else ""
        watt = f" {m['pl1']}W" if m["limits_enabled"] else ""
        return f"{m['name']}{plus}{watt}"

    def set_mode(self, mid, osd=True):
        if mid not in self.cfg["modes"]:
            return
        self.cfg["current_mode"] = mid
        self.apply_mode()
        self.emit("mode", mid)
        if osd:
            self.osd("Leistungsmodus", self.mode_label())

    def cycle_mode(self):
        order = self.cfg["mode_order"]
        cur = self.cfg["current_mode"]
        nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else order[0]
        self.set_mode(nxt)

    def _epp_for(self, m):
        idx = POWER_MODE_EPP.get(m["power_mode"], 2) + BOOST_EPP_SHIFT.get(m["cpu_boost"], 0)
        return EPP_SCALE[max(0, min(3, idx))]

    def _limit_ops(self, m):
        ops = []
        if not m["limits_enabled"]:
            default = self.cfg["tcc_default"]
            if self.caps["tcc"] and default is not None:
                ops.append({"op": "temp_limit", "value": self._tjmax() - default})
            return ops
        arm = self.caps["armoury"]
        if "ppt_pl1_spl" in arm:
            ops.append({"op": "armoury", "name": "ppt_pl1_spl", "value": m["pl1"]})
        if "ppt_pl2_sppt" in arm:
            ops.append({"op": "armoury", "name": "ppt_pl2_sppt", "value": m["pl2"]})
        # "Cross Loading Processor Power" = ASUS-Attribut 0x001200B0 (Kernel: ppt_apu_sppt).
        # Auf Kerneln ohne dieses Attribut wird der Wert nur gespeichert.
        if "ppt_apu_sppt" in arm:
            ops.append({"op": "armoury", "name": "ppt_apu_sppt", "value": m["cross"]})
        if "nv_dynamic_boost" in arm and self.gpu_hw != "eco":
            ops.append({"op": "armoury", "name": "nv_dynamic_boost", "value": m["dyn_boost"]})
        if self.caps["rapl"]:
            ops.append({"op": "rapl", "pl1": m["pl1"], "pl2": m["pl2"]})
        if self.caps["tcc"]:
            ops.append({"op": "temp_limit", "value": m["temp_limit"]})
        return ops

    def _fan_ops(self, m):
        if not self.caps["fan_curves"]:
            return []
        ops = []
        for fan in FANS:
            if m["curves_enabled"]:
                pts = [[t, rpm_to_pwm(fan, r)] for t, r in m["curves"][fan]]
                ops.append({"op": "fan_curve", "fan": fan, "points": pts, "enable": True})
            else:
                ops.append({"op": "fan_curve", "fan": fan, "enable": False})
        return ops

    def apply_mode(self, mid=None):
        mid = mid or self.cfg["current_mode"]
        m = self.cfg.mode_by_id(mid)
        if self.cfg["tcc_default"] is None:
            off = hw.tcc_offset()
            if off is not None:
                self.cfg["tcc_default"] = off
        ops = [{"op": "platform_profile", "value": m["base"]},
               {"op": "epp", "value": self._epp_for(m)},
               {"op": "cpu_boost", "enabled": m["cpu_boost"] != "Disabled"}]
        ops += self._limit_ops(m) + self._fan_ops(m)

        def done(res):
            self._report(res, "Leistungsmodus")
            if not m["curves_enabled"]:
                self._capture_factory()
            self.emit("applied", mid)
        self.helper.submit(ops, done)

    def apply_limits(self):
        m = self.cfg.mode
        self.helper.submit(self._limit_ops(m), lambda r: self._report(r, "Leistungsgrenzen"))
        self.emit("mode_label", self.cfg["current_mode"])

    def apply_fans(self):
        m = self.cfg.mode
        self.helper.submit(self._fan_ops(m), lambda r: (self._report(r, "Lüfterkurve"), self._capture_factory()))
        self.emit("mode_label", self.cfg["current_mode"])

    @staticmethod
    def _tjmax():
        cc = hw.find_hwmon("coretemp")
        if cc:
            for lab in cc.glob("temp*_label"):
                if (hw.read(lab) or "").startswith("Package"):
                    v = hw.read_int(str(lab).replace("_label", "_crit"))
                    if v:
                        return v // 1000
        return 100

    # -- Werkskurven
    def _capture_factory(self):
        got = self.sensors.read_curves_pwm()
        if not got:
            return
        curves, custom = got
        prof = hw.platform_profile()
        if custom or not curves or not prof:
            return
        fac = self.cfg["factory"]
        fac[prof] = curves
        self.cfg["factory"] = fac

    def factory_curves_rpm(self, mid):
        m = self.cfg.mode_by_id(mid)
        fac = self.cfg["factory"].get(m["base"])
        if fac:
            return {fan: [[t, pwm_to_rpm(fan, p)] for t, p in fac[fan]] for fan in fac}
        dm = DEFAULTS["modes"].get(mid) or DEFAULTS["modes"]["balanced"]
        return copy.deepcopy(dm["curves"])

    def reset_curves(self, mid):
        m = self.cfg.mode_by_id(mid)
        fac = self.factory_curves_rpm(mid)
        for fan in FANS:
            if fan in fac:
                pts = sorted(fac[fan])
                # streng steigende Temperaturen sicherstellen (Firmware-Kurven beginnen oft bei 0 °C)
                for i in range(1, 8):
                    if pts[i][0] <= pts[i - 1][0]:
                        pts[i][0] = pts[i - 1][0] + 1
                    pts[i][1] = max(pts[i][1], pts[i - 1][1])
                m["curves"][fan] = pts
        self.cfg.save()

    # ------------------------------------------------------------------- GPU
    def refresh_gpu(self, cb=None):
        def done(mode):
            self.gpu_hw = None if isinstance(mode, Exception) else mode
            self.emit("gpu", self.gpu_hw)
            if cb:
                cb()
        hw.run_async(hw.gpu_mode, done)

    def gpu_label(self):
        return GPU_LABELS.get(self.gpu_hw, "–")

    def gpu_allowed(self, ui_mode):
        return ui_mode == "eco" or self.nvidia_ok

    def set_gpu(self, ui_mode, from_auto=False):
        if not self.gpu_allowed(ui_mode):
            self.toast("NVIDIA-Treiber ist gesperrt (module_blacklist) – nur Eco ist sicher.")
            return
        if ui_mode == "optimized":
            self.cfg["gpu_pref"] = "optimized"
            target = self.cfg["optimized_ac_target"] if self.on_ac else "eco"
            self.emit("gpu", self.gpu_hw)
            if target != self.gpu_hw:
                self._switch_gpu(target)
            return
        if not from_auto:
            self.cfg["gpu_pref"] = ui_mode
        if ui_mode != self.gpu_hw:
            self._switch_gpu(ui_mode)
        else:
            self.emit("gpu", self.gpu_hw)

    def _switch_gpu(self, target):
        if self.gpu_busy:
            self.toast("GPU-Umschaltung läuft bereits …")
            return
        if self.dry_run:
            self.toast(f"[dry-run] GPU → {target}")
            return
        self.gpu_busy = True
        self.emit("gpu_busy", target)
        if not self.nvidia_ok:
            # Ohne NVIDIA-Treiber: Eco direkt über asus-armoury dgpu_disable (nur bei MUX = Hybrid)
            def done_eco(res):
                self.gpu_busy = False
                self._report(res, "GPU")
                self.refresh_gpu()
            mux = hw.armoury_attr("gpu_mux_mode")
            if target != "eco" or (mux and mux["current"] == 0):
                self.gpu_busy = False
                self.toast("Ohne NVIDIA-Treiber ist nur Eco möglich (MUX muss auf Hybrid stehen).")
                self.refresh_gpu()
                return
            self.helper.submit([{"op": "armoury", "name": "dgpu_disable", "value": 1}], done_eco)
            return

        def done(res):
            self.gpu_busy = False
            if isinstance(res, Exception):
                self.toast(f"GPU-Umschaltung fehlgeschlagen: {res}")
            else:
                ok, msg, reboot = res
                if not ok:
                    self.toast(f"GPU: {msg}")
                elif reboot or target == "ultimate" or self.gpu_hw == "ultimate":
                    self.ask_reboot("Für den GPU-Modus „%s“ ist ein Neustart erforderlich." % GPU_LABELS[target])
                elif msg:
                    self.osd("GPU Modus", GPU_LABELS[target])
            self.refresh_gpu()
        hw.run_async(hw.set_gpu_mode, done, target)

    # ------------------------------------------------------------ Bildschirm
    def set_screen(self, mode):
        self.cfg["screen_mode"] = mode
        self.apply_screen()

    def apply_screen(self):
        mode = self.cfg["screen_mode"]
        hz = (240 if self.on_ac else 60) if mode == "auto" else int(mode)
        rates = self.display.available_rates()
        if rates and hz not in rates:
            hz = max(r for r in rates if r <= hz) if any(r <= hz for r in rates) else rates[-1]
        cur = self.display.get_refresh()
        if cur != hz:
            ok, msg = self.display.set_refresh(hz)
            if not ok:
                self.toast(f"Bildschirm: {msg}")
        self.emit("screen", hz)

    def set_color(self, dim=None, splendid=None, temp=None, apply_only=False):
        c = self.cfg
        if not apply_only:
            if dim is not None:
                c.data["dim"] = int(dim)
            if splendid is not None:
                c.data["splendid"] = splendid
            if temp is not None:
                c.data["color_temp"] = temp
            c.save()
        ok, msg = self.display.set_color(c["dim"], c["splendid"], c["color_temp"])
        if not ok:
            self.toast(f"Darstellung: {msg}")

    def reassert_color(self):
        if self.display.gamma_drifted():
            self.set_color(apply_only=True)

    def set_gamut(self, gamut):
        self.cfg["gamut"] = gamut

        def done(res):
            if isinstance(res, Exception):
                self.toast(f"Farbraum: {res}")
            elif not res[0]:
                self.toast(f"Farbraum: {res[1]}")
        hw.run_async(self.display.set_gamut, done, gamut)

    # ----------------------------------------------------- Slash / Tastatur
    def apply_slash(self, with_options=False):
        """AniMatrixControl.SetSlash(): Modus/Helligkeit per HID über den Root-Helfer."""
        if not hw.slash_supported():
            return
        s = self.cfg["slash"]
        brightness, mode = s["brightness"], s["mode"]
        if not self.on_ac and self.cfg["automation"]["slash_off_battery"]:
            brightness = "Aus"
        wake = not getattr(self, "_slash_woken", False)
        self._slash_woken = True
        op = hw.slash_op(brightness, mode, s, wake=wake, battery=self.telemetry.battery,
                         with_options=with_options or wake)
        self.helper.submit([op], lambda r: self._report(r, "Slash Lighting"))

    def set_slash(self, **kw):
        s = self.cfg["slash"]
        s.update(kw)
        self.cfg["slash"] = s
        option_keys = {"off_on_battery", "off_on_lid", "show_on_boot", "show_on_sleep", "battery_warning"}
        self.apply_slash(with_options=bool(option_keys & set(kw)))

    # Aura.cs: Statisch = AuraStatic 0, Atmend = AuraBreathe 1, Farbzyklus = AuraColorCycle 2, Stroboskop = AuraStrobe 10
    AURA_MODE = {"Statisch": 0, "Atmend": 1, "Farbzyklus": 2, "Stroboskop": 10}
    KBD_LEVEL = {"off": 0, "low": 1, "med": 2, "high": 3}

    def apply_kbd(self, with_brightness=True):
        """Tastaturfarbe, -modus und -helligkeit setzen (beim Start, nach Standby, bei Änderung)."""
        k = self.cfg["kbd"]
        c = k.get("color", "#ffffff").lstrip("#")
        op = {"op": "aura", "mode": self.AURA_MODE.get(k.get("mode"), 0),
              "r": int(c[0:2], 16), "g": int(c[2:4], 16), "b": int(c[4:6], 16), "speed": 0xEB}
        if with_brightness:
            op["brightness"] = self.KBD_LEVEL.get(k.get("brightness", "high"), 3)
            self._kbd_restoring = 4      # ~4 s Telemetrie-Ticks ignorieren
            self._kbd_seen = op["brightness"]
        self.helper.submit([op], lambda r: self._report(r, "Tastatur"))

    def set_kbd(self, mode=None, color=None, brightness=None):
        k = self.cfg["kbd"]
        if mode:
            k["mode"] = mode
        if color:
            k["color"] = color
        if brightness:
            k["brightness"] = brightness
        self.cfg["kbd"] = k
        self.apply_kbd(with_brightness=bool(brightness))

    def set_fn_lock(self, enabled, quiet=False):
        self.cfg["fn_lock"] = enabled
        if not self.caps["fn_lock"]:
            if not quiet:
                self.toast("Dieser Kernel bietet keine FN-Lock-Schnittstelle – bitte Fn+Esc drücken")
            self.emit("fn_lock", enabled)
            return
        self.helper.submit([{"op": "fn_lock", "enabled": enabled}],
                           lambda r: self._report(r, "FN-Lock"))
        self.emit("fn_lock", enabled)
        if not quiet:
            self.osd("FN-Lock", "an" if enabled else "aus")

    # ----------------------------------------------------------------- Akku
    def set_charge_limit(self, value):
        value = int(value)

        def after_asusctl(res):
            self.helper.submit([{"op": "charge_limit", "value": value}],
                               lambda r: self._report(r, "Ladebegrenzung"))
        if self.caps["asusctl"] and not self.dry_run:
            hw.run_async(hw.charge_limit_asusctl, after_asusctl, value)
        else:
            after_asusctl(None)

    def charge_full(self):
        def done(res):
            ok, msg = (False, str(res)) if isinstance(res, Exception) else res
            self.toast("Einmalig auf 100% aufladen" if ok else f"Akku: {msg}")
        hw.run_async(hw.charge_oneshot, done)

    def set_autostart(self, enabled):
        self.cfg["autostart"] = enabled
        if enabled:
            AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
            AUTOSTART.write_text(
                "[Desktop Entry]\nType=Application\nName=G-Helper\nExec=g-helper-linux --hidden\n"
                "Icon=g-helper-linux\nX-GNOME-Autostart-enabled=true\nX-GNOME-Autostart-Delay=3\n"
                "NoDisplay=false\nTerminal=false\n")
        elif AUTOSTART.exists():
            AUTOSTART.unlink()

    def autostart_enabled(self):
        return AUTOSTART.exists()

    # --------------------------------------------------- Stromquelle (UPower)
    def _watch_sleep(self):
        """Nach dem Aufwachen Tastatur, Slash und Modus erneut setzen (Firmware setzt sie teils zurück)."""
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            bus.signal_subscribe("org.freedesktop.login1", "org.freedesktop.login1.Manager",
                                 "PrepareForSleep", "/org/freedesktop/login1", None,
                                 Gio.DBusSignalFlags.NONE, self._on_sleep)
        except GLib.Error as e:
            print(f"[sleep] logind nicht verfügbar: {e.message}")

    def _on_sleep(self, conn, sender, path, iface, signal, params):
        going_to_sleep = params.unpack()[0]
        if not going_to_sleep:
            GLib.timeout_add_seconds(2, self._after_resume)

    def _after_resume(self):
        self.apply_mode()
        self.apply_kbd()
        GLib.timeout_add_seconds(6, lambda: (self.apply_kbd(), False)[1])
        self._slash_woken = False
        self.apply_slash()
        return False

    def _watch_power(self):
        self._watch_sleep()
        try:
            self._upower = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None,
                "org.freedesktop.UPower", "/org/freedesktop/UPower", "org.freedesktop.UPower", None)
            ob = self._upower.get_cached_property("OnBattery")
            if ob is not None:
                self.on_ac = not ob.unpack()
            self._upower.connect("g-properties-changed", self._on_upower)
        except GLib.Error as e:
            print(f"[power] UPower nicht verfügbar ({e.message}) – nutze sysfs-Abfrage")
            self._upower = None

    def _on_upower(self, proxy, changed, _inv):
        changed = changed.unpack()
        if "OnBattery" in changed:
            self.power_changed(not changed["OnBattery"])

    def poll_power(self, on_ac):
        """Fallback, falls UPower fehlt: aus der Telemetrie aufgerufen."""
        if self._upower is None and on_ac != self.on_ac:
            self.power_changed(on_ac)

    def power_changed(self, on_ac):
        if on_ac == self.on_ac:
            return
        self.on_ac = on_ac
        auto = self.cfg["automation"]
        target = auto["mode_on_ac"] if on_ac else auto["mode_on_battery"]
        if target and target in self.cfg["modes"] and target != self.cfg["current_mode"]:
            self.set_mode(target)
        else:
            self.apply_mode()  # Firmware setzt Grenzen beim Netzteilwechsel teils zurück
        if self.cfg["screen_mode"] == "auto":
            self.apply_screen()
        pref = self.cfg["gpu_pref"]
        if pref == "optimized":
            self.set_gpu("optimized")
        elif auto["auto_eco_battery"] and not on_ac and self.gpu_hw == "standard":
            self.set_gpu("eco", from_auto=True)
        if auto["slash_off_battery"] and hw.slash_supported():
            self.apply_slash()
        self.emit("power", on_ac)
        self.osd("Stromquelle", "Netzteil" if on_ac else "Akku")

    # ------------------------------------------------------------- Telemetrie
    KBD_LED = "/sys/class/leds/asus::kbd_backlight/brightness"
    LEVEL_NAME = {0: "off", 1: "low", 2: "med", 3: "high"}

    def _track_kbd_brightness(self):
        """Fn-Tasten ändern die Helligkeit am Kernel vorbei → Wert übernehmen und speichern."""
        lvl = hw.read_int(self.KBD_LED)
        if lvl is None:
            return
        last = getattr(self, "_kbd_seen", None)
        self._kbd_seen = lvl
        if last is None or lvl == last or getattr(self, "_kbd_restoring", 0) > 0:
            return
        name = self.LEVEL_NAME.get(lvl)
        k = self.cfg["kbd"]
        if name and k.get("brightness") != name:
            k["brightness"] = name
            self.cfg["kbd"] = k          # sofort in config.json

    def tick(self):
        self._ticks = getattr(self, "_ticks", 0) + 1
        if getattr(self, "_kbd_restoring", 0) > 0:
            self._kbd_restoring -= 1
        self._track_kbd_brightness()
        if self._ticks % 60 == 0 and self.cfg["slash"]["mode"] == "Ladezustand":
            self.apply_slash()
        self.telemetry = t = self.sensors.read()
        self.poll_power(t.on_ac)
        self.emit("telemetry", t)
        return True

    # ------------------------------------------------------------- Dienste
    def service(self, name, action, cb=None):
        def done(res):
            self._report(res, f"Dienst {name}")
            if cb:
                cb()
        self.helper.submit([{"op": "service", "name": name, "action": action}], done)

    @staticmethod
    def service_state(name):
        ok, out = hw.run(["systemctl", "is-active", f"{name}.service"], timeout=5)
        return out.strip().splitlines()[0] if out.strip() else "unknown"


def env_flag(name):
    return os.environ.get(name, "") not in ("", "0", "false")
