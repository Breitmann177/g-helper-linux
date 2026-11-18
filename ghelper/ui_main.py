"""Hauptfenster – Aufbau 1:1 nach G-Helper Settings.Designer.cs.

Reihenfolge (Dock=Top, oben → unten):
panelPerformance, panelGPU, panelScreen, panelGamma, panelMatrix (Slash Lighting),
panelKeyboard, panelBattery, panelStartup, panelVersion, panelFooter.
Maße aus dem Designer (192 DPI) halbiert: Breite 425, Padding 10, Kacheln 60 hoch.
"""
from gi.repository import Gdk, Gio, Gtk

from . import hardware as hw
from .config import VERSION
from .display import GAMUTS, SPLENDID, TEMPS
from .widgets import Debouncer, Dropdown, Scale, SmallButton, Tile, check, label, section_header

WIDTH = 425

# Settings.cs: buttonSilent.BorderColor = colorEco, Balanced = colorStandard, Turbo = colorTurbo
MODE_TILES = [("quiet", "Leise", "silent", "eco"),
              ("balanced", "Ausbalanciert", "balanced", "standard"),
              ("turbo", "Turbo", "turbo", "turbo")]
BASE_TO_TILE = {"quiet": "quiet", "balanced": "balanced", "performance": "turbo"}
BASE_ACCENT = {"quiet": "eco", "balanced": "standard", "performance": "turbo"}

# buttonEco = colorEco, Standard = colorStandard, Ultimate = colorTurbo, Optimized = colorEco
GPU_TILES = [("eco", "Eco", "eco", "eco", "dGPU zum Stromsparen abschalten"),
             ("standard", "Standard", "standard", "standard", "dGPU aktivieren"),
             ("ultimate", "Ultimativ", "ultimate", "turbo",
              "Verbindet den Bildschirm mit der dGPU für maximale Leistung."),
             ("optimized", "Optimiert", "optimized", "eco",
              "Eco-Modus im Batteriebetrieb. Standard, wenn eingesteckt.")]

AURA_LABELS = ["Statisch", "Atmend", "Farbzyklus", "Stroboskop"]
SLASH_BRIGHT = list(hw.SLASH_BRIGHTNESS)


def de(x, digits=1):
    return f"{x:.{digits}f}".replace(".", ",")


def panel():
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    box.add_css_class("gh-panel")
    return box


def grid(n):
    return Gtk.Box(spacing=0, homogeneous=True)


class MainPanel(Gtk.Box):
    def __init__(self, ctl, toggle_fans, open_extra, hide_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("gh-root")
        self.set_size_request(WIDTH, -1)
        self.ctl = ctl
        self.cfg = ctl.cfg
        self.toggle_fans = toggle_fans
        self.open_extra = open_extra
        self.hide_window = hide_window
        self._charge_apply = Debouncer(700, ctl.set_charge_limit)
        self._dim_apply = Debouncer(120, lambda v: ctl.set_color(dim=v))

        self._build_performance()
        self.gpu_tiles = {}
        if ctl.caps["dgpu"]:              # Zenbook/Vivobook ohne zweite GPU: kein GPU-Bereich
            self._build_gpu()
        self._build_screen()
        self._build_gamma()
        self._build_slash()
        self._build_keyboard()
        self._build_battery()
        self._build_startup()
        self._build_version()
        self._build_footer()

        ctl.connect(self._on_event)
        self.refresh_mode()
        self.refresh_gpu()

    # ======================================================= panelPerformance
    def _build_performance(self):
        p = panel()
        row, self.mode_title, right = section_header("perf", "Leistungsmodus")
        self.cpu_info = label("", "gh-section-right")
        right.append(self.cpu_info)
        p.append(row)
        tiles = grid(4)
        self.mode_tiles = {}
        for mid, text, ic, acc in MODE_TILES:
            t = Tile(text, ic, acc)
            t.connect("clicked", lambda _b, m=mid: self.ctl.set_mode(m))
            self.mode_tiles[mid] = t
            tiles.append(t)
        # buttonFans: BackColor ControlLight (= buttonSecond), Rahmenfarbe nach Basis-Modus
        self.fans_tile = Tile("Lüfter + Leistung", "fans", "standard", secondary=True)
        self.fans_tile.connect("clicked", lambda *_: self.toggle_fans())
        tiles.append(self.fans_tile)
        p.append(tiles)
        self.append(p)

    def refresh_mode(self):
        mid = self.cfg["current_mode"]
        base = self.cfg.mode["base"]
        custom = mid not in self.mode_tiles
        for m, t in self.mode_tiles.items():
            t.set_active(m == mid)
        self.fans_tile.set_accent(BASE_ACCENT.get(base, "standard"))
        self.fans_tile.set_active(custom)
        self.mode_title.set_label(f"Leistungsmodus: {self.ctl.mode_label()}")

    # ============================================================== panelGPU
    def _build_gpu(self):
        p = panel()
        row, self.gpu_title, right = section_header("gpu", "GPU Modus")
        self.gpu_info = label("", "gh-section-right")
        right.append(self.gpu_info)
        p.append(row)
        tiles = grid(4)
        self.gpu_tiles = {}
        for gid, text, ic, acc, tip in GPU_TILES:
            t = Tile(text, ic, acc)
            t.set_tooltip_text(tip)
            t.connect("clicked", lambda _b, g=gid: self.ctl.set_gpu(g))
            if not self.ctl.gpu_allowed(gid):
                t.set_sensitive(False)
                if gid == "ultimate" and not hw.armoury_attr("gpu_mux_mode"):
                    why = "Dieses Modell hat keinen MUX-Schalter."
                else:
                    why = ("NVIDIA-Treiber ist nicht geladen oder per Kernel-Parameter "
                           "(module_blacklist) deaktiviert – nur Eco ist sicher.")
                t.set_tooltip_text(tip + "\n\nGesperrt: " + why)
            self.gpu_tiles[gid] = t
            tiles.append(t)
        p.append(tiles)
        self.append(p)

    def refresh_gpu(self, busy_target=None):
        if not self.gpu_tiles:
            return
        hwmode = self.ctl.gpu_hw
        pref = self.cfg["gpu_pref"]
        for g, t in self.gpu_tiles.items():
            if g == "optimized":
                t.set_active(pref == "optimized")
            else:
                t.set_active(g == hwmode and pref != "optimized")
            t.set_busy(g == busy_target)
        txt = "Wechseln …" if busy_target else self.ctl.gpu_label()
        self.gpu_title.set_label(f"GPU Modus: {txt}")

    # =========================================================== panelScreen
    def _build_screen(self):
        p = panel()
        row, self.screen_title, right = section_header("screen", "Bildschirm")
        self.mid_info = label("", "gh-section-right")
        right.append(self.mid_info)
        p.append(row)
        tiles = grid(4)
        lo, hi = self.ctl.screen_rates()
        self.screen_tiles = {}
        # buttonScreenAuto / button60Hz / button120Hz: BorderColor = colorGray
        # Tasten zeigen die echten Raten des Panels (60/120, 60/144, 60/165, 60/240 …)
        for key, text in (("auto", "Auto"), ("min", f"{lo}Hz"), ("max", f"{hi}Hz")):
            t = Tile(text, None, "gray", short=True)
            t.connect("clicked", lambda _b, k=key: self.ctl.set_screen(k))
            self.screen_tiles[key] = t
            tiles.append(t)
        tiles.append(Gtk.Box())  # 4er-Raster wie im Original (4. Spalte = Miniled, hier leer)
        p.append(tiles)
        self.append(p)
        self.refresh_screen(self.ctl.display.get_refresh())

    def refresh_screen(self, hz):
        mode = self.cfg["screen_mode"]
        for k, t in self.screen_tiles.items():
            t.set_active(k == mode)
        self.screen_title.set_label(f"Bildschirm: {hz}Hz" if hz else "Bildschirm")

    # ============================================================ panelGamma
    def _build_gamma(self):
        p = panel()
        row, _t, right = section_header("gamma", "Flackerfreies Dimmen / Darstellungsmodus")
        self.dim_value = label(f"{self.cfg['dim']}%", "gh-section-right")
        right.append(self.dim_value)
        p.append(row)
        self.dim_scale = Scale(10, 100, 1, self.cfg["dim"], self._on_dim)
        p.append(self.dim_scale)
        drops = grid(3)
        drops.append(Dropdown(SPLENDID, self.cfg["splendid"], lambda v: self.ctl.set_color(splendid=v)))
        drops.append(Dropdown(TEMPS, self.cfg["color_temp"], lambda v: self.ctl.set_color(temp=v)))
        gam = [f"Gamut: {g}" for g in GAMUTS]
        drops.append(Dropdown(gam, f"Gamut: {self.cfg['gamut']}",
                              lambda v: self.ctl.set_gamut(v.split(": ", 1)[1])))
        p.append(drops)
        self.append(p)

    def _on_dim(self, v):
        self.dim_value.set_label(f"{v}%")
        self._dim_apply(v)

    # ===================================================== panelMatrix (Slash)
    def _build_slash(self):
        p = panel()
        row, _t, _r = section_header("slash", "Slash Lighting")
        p.append(row)
        box = grid(3)
        s = self.cfg["slash"]
        box.append(Dropdown(SLASH_BRIGHT, s["brightness"], lambda v: self.ctl.set_slash(brightness=v)))
        box.append(Dropdown(hw.SLASH_MODES, s["mode"], lambda v: self.ctl.set_slash(mode=v)))
        btn = SmallButton("Slash", main=True)
        btn.connect("clicked", self._slash_popover)
        box.append(btn)
        p.append(box)
        self.append(p)

    def _slash_popover(self, btn):
        pop = Gtk.Popover()
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=8, margin_bottom=8,
                    margin_start=10, margin_end=10)
        if not hw.slash_supported():
            v.append(label("Slash-Gerät (0B05:19B6) nicht gefunden", "gh-dim-label"))
        s = self.cfg["slash"]
        # Texte aus Strings.de.resx (Slash.cs)
        opts = [("show_on_boot", "Start- und Herunterfahranimation"),
                ("show_on_sleep", "Standby-Animation"),
                ("battery_warning", "Warnung bei niedrigem Akkustand"),
                ("off_on_battery", "Im Batteriebetrieb abschalten"),
                ("off_on_lid", "Deckel")]
        for key, text in opts:
            v.append(check(text, s.get(key, False), lambda a, k=key: self.ctl.set_slash(**{k: a})))
        row = Gtk.Box(spacing=6)
        row.append(label("Intervall"))
        sc = Scale(0, 5, 1, s.get("interval", 0), lambda x: self.ctl.set_slash(interval=x))
        sc.set_size_request(140, -1)
        row.append(sc)
        v.append(row)
        pop.set_child(v)
        pop.set_parent(btn)
        pop.connect("closed", lambda p: p.unparent())
        pop.popup()

    # ========================================================= panelKeyboard
    def _build_keyboard(self):
        p = panel()
        row, _t, right = section_header("keyboard", "Laptoptastatur")
        self.overlay_btn = SmallButton("Overlay", tiny=True)
        self.overlay_btn.connect("clicked", self._toggle_overlay)
        self.fnlock_btn = SmallButton("FN-Lock", tiny=True)
        self.fnlock_btn.set_tooltip_text("Fn+F Funktionen ohne Fn Taste nutzen")
        self.fnlock_btn.connect("clicked", lambda *_: self.ctl.set_fn_lock(not self.cfg["fn_lock"]))
        right.append(self.overlay_btn)
        right.append(self.fnlock_btn)
        p.append(row)
        box = grid(3)
        k = self.cfg["kbd"]
        aura_drop = Dropdown(AURA_LABELS, k["mode"], lambda v: self.ctl.set_kbd(mode=v))
        box.append(aura_drop)
        color_btn = SmallButton("Farbe", main=True)
        if not self.ctl.caps["aura"]:     # Tastatur ohne RGB: nur Helligkeit (Fn-Tasten / Extra)
            for w in (aura_drop, color_btn):
                w.set_sensitive(False)
                w.set_tooltip_text("Diese Tastatur hat keine RGB-Beleuchtung")
        self.swatch = Gtk.DrawingArea(content_width=12, content_height=12, valign=Gtk.Align.CENTER)
        self.swatch.add_css_class("gh-swatch")
        self.swatch.set_draw_func(self._draw_swatch)
        color_btn.box.append(self.swatch)       # Farbquadrat rechts vom Text wie im Original
        color_btn.connect("clicked", self._pick_color)
        box.append(color_btn)
        extra = SmallButton("Extra", "settings", main=True)   # buttonKeyboard: icons8_settings_32
        extra.connect("clicked", lambda *_: self.open_extra())
        box.append(extra)
        p.append(box)
        self.append(p)
        self.refresh_toggles()

    def _draw_swatch(self, area, cr, w, h):
        rgba = Gdk.RGBA()
        rgba.parse(self.cfg["kbd"]["color"])
        cr.set_source_rgb(rgba.red, rgba.green, rgba.blue)
        cr.rectangle(0, 0, w, h)
        cr.fill()

    def _pick_color(self, btn):
        dlg = Gtk.ColorDialog(title="Farbe", with_alpha=False)
        rgba = Gdk.RGBA()
        rgba.parse(self.cfg["kbd"]["color"])

        def done(d, res):
            try:
                c = d.choose_rgba_finish(res)
            except Exception:  # abgebrochen
                return
            hexc = "#{:02x}{:02x}{:02x}".format(round(c.red * 255), round(c.green * 255), round(c.blue * 255))
            self.ctl.set_kbd(color=hexc)
            self.swatch.queue_draw()
        dlg.choose_rgba(self.get_root(), rgba, None, done)

    def _toggle_overlay(self, *_):
        self.cfg["overlay"] = not self.cfg["overlay"]
        self.refresh_toggles()

    def refresh_toggles(self):
        # Settings.cs: buttonOverlay.BackColor = colorEco (an), buttonFnLock.BackColor = colorStandard (an)
        self.overlay_btn.set_on(self.cfg["overlay"], "on-eco")
        self.fnlock_btn.set_on(self.cfg["fn_lock"], "on-standard")

    # ========================================================== panelBattery
    def _build_battery(self):
        p = panel()
        cur = self.ctl.telemetry.charge_limit or 100
        row, self.battery_title, right = section_header("battery", f"Ladebegrenzung: {cur}%")
        self.charge_info = label("", "gh-section-right")
        right.append(self.charge_info)
        p.append(row)
        box = Gtk.Box(spacing=4)
        self.charge_scale = Scale(40, 100, 5, max(40, cur), self._on_charge)
        box.append(self.charge_scale)
        self.full_btn = SmallButton("100%", tiny=True)
        self.full_btn.set_tooltip_text("Einmalig auf 100% aufladen")
        self.full_btn.connect("clicked", lambda *_: self.ctl.charge_full())
        box.append(self.full_btn)
        p.append(box)
        self.append(p)

    def _on_charge(self, v):
        self.battery_title.set_label(f"Ladebegrenzung: {v}%")
        self._charge_apply(v)

    # ========================================================== panelStartup
    def _build_startup(self):
        row = Gtk.Box(spacing=6, margin_start=10, margin_end=10, margin_top=2)
        self.autostart_cb = check("Beim Start ausführen", self.ctl.autostart_enabled() or self.cfg["autostart"],
                                  self.ctl.set_autostart)
        row.append(self.autostart_cb)
        row.append(Gtk.Box(hexpand=True))
        self.bat_info = label("", "gh-section-right")
        row.append(self.bat_info)
        self.append(row)

    # ========================================================== panelVersion
    def _build_version(self):
        ver = Gtk.Label(label=f"Version: {VERSION}", xalign=0, margin_start=10, margin_top=4)
        ver.add_css_class("gh-version")
        click = Gtk.GestureClick()
        click.connect("released", lambda *_: Gio.AppInfo.launch_default_for_uri(
            "https://github.com/seerge/g-helper/releases", None))
        ver.add_controller(click)
        ver.set_cursor(Gdk.Cursor.new_from_name("pointer"))
        self.version_label = ver
        self.append(ver)

    # =========================================================== panelFooter
    def _build_footer(self):
        bar = Gtk.Box(spacing=0, homogeneous=True, margin_start=8, margin_end=8, margin_top=4, margin_bottom=8)
        donate = SmallButton("Spenden", "donate")
        donate.connect("clicked", lambda *_: Gio.AppInfo.launch_default_for_uri(
            "https://github.com/seerge/g-helper", None))
        upd = SmallButton("Aktualisierungen", "updates")
        upd.connect("clicked", self._updates)
        close = SmallButton("Schließen", "quit")
        close.connect("clicked", lambda *_: self.hide_window())
        for b in (donate, upd, close):
            bar.append(b)
        self.append(bar)

    def _updates(self, btn):
        btn.set_sensitive(False)
        btn.text.set_label("Aktualisierungen …")

        def work():
            return hw.versions(), hw.check_updates()

        def done(res):
            btn.set_sensitive(True)
            btn.text.set_label("Aktualisierungen")
            if isinstance(res, Exception):
                self.ctl.toast(f"Aktualisierungen: {res}")
                return
            ver, ups = res
            body = (f"G-Helper Linux {VERSION}\nasusctl {ver.get('asusctl', '–')}\n"
                    f"supergfxctl {ver.get('supergfxctl', '–')}\nKernel {ver.get('kernel')}\n\n")
            body += ("Neue Updates:\n" + "\n".join(ups[:12])) if ups else "Keine Updates"
            if ups:
                self.version_label.add_css_class("update")   # labelVersion.ForeColor = colorTurbo
                body += "\n\nsudo dnf upgrade asusctl supergfxctl kernel"
            Gtk.AlertDialog(message="Aktualisierungen", detail=body).show(self.get_root())
        hw.run_async(work, done)

    # ============================================================ Ereignisse
    def _on_event(self, ev, data):
        if ev in ("mode", "mode_label", "applied"):
            self.refresh_mode()
        elif ev == "gpu":
            self.refresh_gpu()
        elif ev == "gpu_busy":
            self.refresh_gpu(busy_target=data)
        elif ev == "screen":
            self.refresh_screen(data)
        elif ev == "fn_lock":
            self.refresh_toggles()
        elif ev == "telemetry":
            self._telemetry(data)

    def _telemetry(self, t):
        # Settings.cs: "CPU" + cpuTemp + "  " + "Lüfter: …"
        fan = lambda v: f"  Lüfter: {v}RPM" if v is not None else ""
        cpu_temp = f": {t.cpu_temp:.0f}°C" if t.cpu_temp is not None else ""
        self.cpu_info.set_label(f"CPU{cpu_temp}{fan(t.cpu_fan)}")
        gpu_temp = f": {t.gpu_temp:.0f}°C" if t.gpu_temp is not None else ""
        if self.gpu_tiles:
            self.gpu_info.set_label(f"GPU{gpu_temp}{fan(t.gpu_fan)}")
        self.mid_info.set_label(f"Mid Lüfter: {t.mid_fan}RPM" if t.mid_fan is not None else "")
        if t.bat_watts is not None and t.bat_status in ("Charging", "Discharging"):
            word = "Lädt" if t.bat_status == "Charging" else "Entlädt"
            self.charge_info.set_label(f"{word}: {de(abs(t.bat_watts))}W")
        else:
            self.charge_info.set_label("")
        if t.battery is not None:
            self.bat_info.set_label(f"Akkustand: {de(t.battery)}%")
