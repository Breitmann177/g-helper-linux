"""Fenster "Lüfter und Leistung - <CPU>" (links neben dem Hauptfenster)."""
from gi.repository import Gtk

from .config import CPU_BOOST, FAN_MAX_RPM, HYSTERESIS, POWER_MODES
from .widgets import (Debouncer, Dropdown, FanCurveGraph, Scale, SmallButton, check, icon, label,
                      section_header)

# Fans.cs: seriesCPU = colorStandard, seriesGPU = colorTurbo, seriesMid = colorEco
GRAPHS = [("cpu", "CPU Lüfterprofil, RPM/°C", "#3AAEEF"),
          ("gpu", "GPU Lüfterprofil, RPM/°C", "#FF2020"),
          ("mid", "Mittleres Lüfterprofil, RPM/°C", "#06B48A")]

# (Schlüssel, Beschriftung, Einheit, Armoury-Attribut, Standardbereich)
LIMITS = [("pl1", "PL1 (CPU sustained)", "W", "ppt_pl1_spl", (5, 125)),
          ("pl2", "PL2 (CPU long boost)", "W", "ppt_pl2_sppt", (5, 175)),
          ("cross", "Cross Loading Processor Power", "W", "ppt_apu_sppt", (5, 125)),
          ("dyn_boost", "GPU to CPU Dynamic Boost", "W", "nv_dynamic_boost", (0, 25)),
          ("temp_limit", "CPU Temperature Limit", "°C", None, (75, 100))]


class FansPanel(Gtk.Box):
    def __init__(self, ctl):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("gh-root")
        self.ctl = ctl
        self.cfg = ctl.cfg
        self._apply_limits = Debouncer(700, ctl.apply_limits)
        self._apply_fans = Debouncer(400, ctl.apply_fans)

        cols = Gtk.Box(spacing=0, vexpand=True)
        cols.append(self._build_left())
        cols.append(self._build_right())
        self.append(cols)
        ctl.connect(self._on_event)
        self.load_mode()

    # =============================================================== links
    def _build_left(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class("gh-fans-left")
        box.set_size_request(300, -1)

        tab = Gtk.Button(label="CPU", halign=Gtk.Align.START, margin_start=10, margin_top=8)
        tab.add_css_class("gh-tab")          # buttonCPU.BorderColor = colorStandard
        box.append(tab)

        row, _t, _r = section_header("perf", "Windows Power Mode")
        box.append(row)
        self.power_drop = Dropdown(POWER_MODES, None, lambda v: self._set("power_mode", v, reapply=True))
        self.power_drop.set_tooltip_text("Linux: Energy Performance Preference (EPP) aller Kerne")
        self.power_drop.set_margin_start(14)
        self.power_drop.set_margin_end(14)
        box.append(self.power_drop)

        row, _t, _r = section_header("boost", "CPU-Boost")
        box.append(row)
        self.boost_drop = Dropdown(CPU_BOOST, None, lambda v: self._set("cpu_boost", v, reapply=True))
        self.boost_drop.set_tooltip_text("Disabled: Turbo aus (intel_pstate/no_turbo=1) – kühl und leise")
        self.boost_drop.set_margin_start(14)
        self.boost_drop.set_margin_end(14)
        box.append(self.boost_drop)

        row, _t, _r = section_header("cpu", "Leistungsbegrenzung")
        row.set_margin_top(8)
        box.append(row)
        arm = self.ctl.caps["armoury"]
        self.limit_widgets = {}
        for key, text, unit, attr, (lo, hi) in LIMITS:
            info = arm.get(attr) if attr else None
            if info and info.get("min") is not None and info.get("max") is not None:
                lo, hi = info["min"], info["max"]
            available = (attr is None and self.ctl.caps["tcc"]) or (attr is not None and attr in arm) \
                or (key in ("pl1", "pl2") and self.ctl.caps["rapl"])
            head = Gtk.Box(spacing=6, margin_start=14, margin_end=14, margin_top=4)
            name = label(text, "gh-dim-label" if not available else None, hexpand=True)
            val = label("", "gh-value")
            head.append(name)
            head.append(val)
            sc = Scale(lo, hi, 1, lo, lambda v, k=key, u=unit: self._limit_changed(k, v, u))
            sc.set_margin_start(14)
            sc.set_margin_end(14)
            if not available:
                tip = "Vom aktuellen Kernel nicht bereitgestellt – Wert wird nur gespeichert"
                name.set_tooltip_text(tip)
                sc.set_tooltip_text(tip)
            box.append(head)
            box.append(sc)
            self.limit_widgets[key] = (sc, val, unit)

        box.append(Gtk.Box(vexpand=True))
        self.limits_cb = check("Leistungsgrenzen anwenden", False, self._limits_toggled)
        self.limits_cb.set_margin_start(14)
        self.limits_cb.set_margin_bottom(12)
        box.append(self.limits_cb)
        return box

    # ============================================================== rechts
    def _build_right(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        box.set_size_request(520, -1)
        head = Gtk.Box(spacing=6, margin_start=14, margin_end=14, margin_top=8)
        head.append(icon("fan", 16))
        head.append(label("BIOS Lüfterkurven", "gh-section-title"))
        head.append(Gtk.Box(hexpand=True))
        self.mode_drop = Dropdown([], None, self._mode_selected, hexpand=False)
        self.mode_drop.set_size_request(150, -1)
        head.append(self.mode_drop)
        plus = SmallButton("", "add", main=True)
        plus.set_tooltip_text("Eigenes Profil auf Basis des aktuellen anlegen")
        plus.connect("clicked", self._add_mode)
        head.append(plus)
        box.append(head)

        self.snap_cb = check("Einrasten", self.cfg["fans_snap"], self._snap_toggled)
        self.snap_cb.set_margin_start(14)
        box.append(self.snap_cb)

        self.graphs = {}
        shown = self.ctl.caps["fans"] or ["cpu", "gpu"]     # 2-Lüfter-Modelle haben kein „mid“
        for fan, title, color in GRAPHS:
            if fan not in shown:
                continue
            g = FanCurveGraph(title, color, FAN_MAX_RPM[fan], lambda pts, f=fan: self._curve_changed(f, pts))
            g.snap = self.cfg["fans_snap"]
            g.set_margin_start(8)
            g.set_margin_end(14)
            self.graphs[fan] = g
            box.append(g)
        if not self.ctl.caps["fan_curves"]:
            box.append(label("Kernel stellt keine asus_custom_fan_curve bereit – Kurven werden nur gespeichert",
                             "gh-dim-label"))

        self.hyst = {}
        for key, text in (("hyst_up", "Hysterese hoch"), ("hyst_down", "Hysterese runter")):
            row = Gtk.Box(spacing=8, margin_start=14, margin_end=14)
            name = label(text)
            name.set_size_request(120, -1)
            row.append(name)
            sc = Scale(0, 2, 1, 1, lambda v, k=key: self._hyst_changed(k, v))
            sc.set_tooltip_text("Die Lüftersteuerung des EC unterstützt unter Linux keine Hysterese – "
                                "der Wert wird gespeichert, aber nicht an die Firmware übergeben.")
            val = label("Mittel", "gh-value")
            val.set_size_request(60, -1)
            row.append(sc)
            row.append(val)
            box.append(row)
            self.hyst[key] = (sc, val)

        bottom = Gtk.Box(spacing=8, margin_start=14, margin_end=14, margin_top=6, margin_bottom=12)
        reset = SmallButton("Werkseinstellungen")
        reset.connect("clicked", self._reset)
        bottom.append(reset)
        bottom.append(Gtk.Box(hexpand=True))
        self.curves_cb = check("Lüfterkurve anwenden", False, self._curves_toggled)
        bottom.append(self.curves_cb)
        box.append(bottom)
        return box

    # ============================================================ Laden
    def load_mode(self):
        cfg = self.cfg
        mid = cfg["current_mode"]
        m = cfg.mode
        names = [cfg.mode_by_id(i)["name"] for i in cfg["mode_order"]]
        self.mode_drop.set_items(names, m["name"])
        self.power_drop.set_value(m["power_mode"])
        self.boost_drop.set_value(m["cpu_boost"])
        for key, (sc, val, unit) in self.limit_widgets.items():
            sc.set_silent(m[key])
            val.set_label(f"{m[key]}{unit}")
        self.limits_cb.set_active(m["limits_enabled"])
        factory = self.ctl.factory_curves_rpm(mid)
        for fan, g in self.graphs.items():
            g.applied = m["curves_enabled"]
            g.set_points(m["curves"][fan], factory.get(fan))
        for key, (sc, val) in self.hyst.items():
            sc.set_silent(m.get(key, 1))
            val.set_label(HYSTERESIS[m.get(key, 1)])
        self.curves_cb.set_active(m["curves_enabled"])

    # ============================================================ Handler
    def _set(self, key, value, reapply=False):
        self.cfg.mode[key] = value
        self.cfg.save()
        if reapply:
            self.ctl.apply_mode()
            self.ctl.emit("mode", self.cfg["current_mode"])

    def _limit_changed(self, key, v, unit):
        self.limit_widgets[key][1].set_label(f"{v}{unit}")
        self.cfg.mode[key] = v
        self.cfg.save()
        if self.cfg.mode["limits_enabled"]:
            self._apply_limits()
        else:
            self.ctl.emit("mode_label", self.cfg["current_mode"])

    def _limits_toggled(self, active):
        if self.cfg.mode["limits_enabled"] == active:
            return
        self._set("limits_enabled", active, reapply=True)

    def _curves_toggled(self, active):
        if self.cfg.mode["curves_enabled"] == active:
            return
        self.cfg.mode["curves_enabled"] = active
        self.cfg.save()
        for g in self.graphs.values():
            g.applied = active
            g.queue_draw()
        self.ctl.apply_fans()

    def _curve_changed(self, fan, pts):
        self.cfg.mode["curves"][fan] = [list(p) for p in pts]
        self.cfg.save()
        if self.cfg.mode["curves_enabled"]:
            self._apply_fans()

    def _snap_toggled(self, active):
        self.cfg["fans_snap"] = active
        for g in self.graphs.values():
            g.snap = active

    def _hyst_changed(self, key, v):
        self.hyst[key][1].set_label(HYSTERESIS[v])
        self._set(key, v)

    def _reset(self, *_):
        self.ctl.reset_curves(self.cfg["current_mode"])
        self.load_mode()
        if self.cfg.mode["curves_enabled"]:
            self.ctl.apply_fans()

    def _mode_selected(self, name):
        for mid in self.cfg["mode_order"]:
            if self.cfg.mode_by_id(mid)["name"] == name and mid != self.cfg["current_mode"]:
                self.ctl.set_mode(mid)
                return

    def _add_mode(self, *_):
        mid = self.cfg.add_custom_mode(self.cfg["current_mode"])
        self.ctl.set_mode(mid)

    def _on_event(self, ev, data):
        if ev == "mode":
            self.load_mode()
        elif ev == "applied":
            # Werkskurven wurden evtl. gerade erfasst → Referenzfläche aktualisieren
            factory = self.ctl.factory_curves_rpm(self.cfg["current_mode"])
            for fan, g in self.graphs.items():
                g.factory = factory.get(fan)
                g.queue_draw()
        elif ev == "telemetry":
            temps = {"cpu": data.cpu_temp, "mid": data.cpu_temp, "gpu": data.gpu_temp}
            for fan, g in self.graphs.items():
                v = temps.get(fan)
                g.set_current_temp(round(v) if v else None)
