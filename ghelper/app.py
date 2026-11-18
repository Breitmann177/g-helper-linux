"""Adw.Application: Fenster, Tray, Hotkeys, Telemetrie-Takt."""
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gtk  # noqa: E402

from . import hardware as hw  # noqa: E402
from .config import APP_ID, Config  # noqa: E402
from .controller import Controller  # noqa: E402
from .hotkeys import Hotkeys  # noqa: E402
from .tray import Tray  # noqa: E402
from .ui_extra import ExtraWindow  # noqa: E402
from .ui_fans import FansPanel  # noqa: E402
from .ui_main import MainPanel  # noqa: E402

HERE = Path(__file__).resolve().parent


class GHelperApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.add_main_option("hidden", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Nur im System-Tray starten", None)
        self.add_main_option("no-apply", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Testmodus: nichts an die Hardware schreiben", None)
        self.add_main_option("fans", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Mit geöffnetem Fenster „Lüfter und Leistung“ starten", None)
        self.add_main_option("extra", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Mit geöffnetem Extra-Fenster starten", None)
        self.add_main_option("screenshot", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             "Fenster als PNG speichern und beenden (Debug)", "DATEI")
        self.window = None
        self.ctl = None
        self.extra = None
        self.fans_window = None
        self.tray = None

    # ---------------------------------------------------------------- Start
    def do_command_line(self, cmdline):
        opts = cmdline.get_options_dict().end().unpack()
        if self.window is None:
            self._setup(dry_run=bool(opts.get("no-apply")) or bool(opts.get("screenshot")))
            if not opts.get("hidden") or opts.get("screenshot"):
                self.window.present()
            if opts.get("fans"):
                self.toggle_fans()
            if opts.get("extra"):
                self.open_extra()
            if opts.get("screenshot"):
                GLib.timeout_add(2500, self._screenshot, opts["screenshot"])
        else:
            self.toggle_window(force_show=True)
        return 0

    def _setup(self, dry_run):
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        css = Gtk.CssProvider()
        css.load_from_path(str(HERE / "style.css"))
        # Über Nutzer-Themes (~/.config/gtk-4.0/gtk.css = Priorität USER 800) legen,
        # damit z. B. Fluent/WhiteSur das G-Helper-Design nicht überschreiben
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_USER + 100)
        self.hold()  # läuft im Tray weiter, wenn das Fenster geschlossen wird

        self.cfg = Config()
        self.ctl = ctl = Controller(self.cfg, dry_run=dry_run)
        ctl.toast = self.toast
        ctl.osd = self.osd
        ctl.ask_reboot = self.ask_reboot

        win = Adw.ApplicationWindow(application=self, resizable=False)
        win.add_css_class("ghelper")
        win.set_title(f"G-Helper - {hw.model_title()}")
        win.connect("close-request", self._on_close)
        self.window = win

        self.toasts = Adw.ToastOverlay()
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.add_css_class("gh-root")
        outer.append(self._header(win.get_title()))
        self.main = MainPanel(ctl, self.toggle_fans, self.open_extra, self.hide_window)
        outer.append(self.main)
        self.toasts.set_child(outer)
        win.set_content(self.toasts)

        self.hotkeys = Hotkeys(lambda: self.cfg["keybinds"], self.on_hotkey)

        icon_path = str(HERE / "icons" / "tray")
        self.tray = Tray("g-helper-linux", icon_path, "G-Helper", self.toggle_window, self._tray_items)
        ctl.connect(self._on_event)

        ctl.startup(apply=not dry_run)
        GLib.timeout_add_seconds(1, ctl.tick)
        GLib.timeout_add_seconds(5, lambda: (ctl.reassert_color(), True)[1])
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])

    @staticmethod
    def _header(title):
        """G-Helper-Titelleiste: Titel linksbündig, nur ✕."""
        header = Adw.HeaderBar(decoration_layout=":close", centering_policy=Adw.CenteringPolicy.LOOSE)
        lb = Gtk.Label(label=title, css_classes=["title"], halign=Gtk.Align.START, hexpand=True,
                       margin_start=4, ellipsize=3)  # Pango.EllipsizeMode.END
        header.set_title_widget(lb)
        return header

    # --------------------------------------------------------------- Fenster
    def _on_close(self, win):
        win.set_visible(False)
        return True

    def hide_window(self):
        self.window.set_visible(False)

    def toggle_window(self, force_show=False):
        if self.window.get_visible() and not force_show:
            self.window.set_visible(False)
        else:
            self.window.present()
        return False

    def toggle_fans(self):
        """Wie G-Helper: Button „Lüfter + Leistung“ öffnet/schließt das Fenster „Lüfter und Leistung“."""
        if self.fans_window is None:
            w = Adw.Window(title=f"Lüfter und Leistung - {hw.cpu_name()}", resizable=False)
            w.add_css_class("ghelper")
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            box.add_css_class("gh-root")
            box.append(self._header(w.get_title()))
            self.fans = FansPanel(self.ctl)
            box.append(self.fans)
            w.set_content(box)
            w.connect("close-request", lambda win: (win.set_visible(False), True)[1])
            self.fans_window = w
        if self.fans_window.get_visible():
            self.fans_window.set_visible(False)
        else:
            self.fans_window.present()

    def open_extra(self):
        if self.extra is None:
            self.extra = ExtraWindow(self.window, self.ctl, self.hotkeys)
            self.extra.connect("close-request", lambda w: (setattr(self, "extra", None), False)[1])
        self.extra.present()

    # ---------------------------------------------------------- Rückmeldungen
    def toast(self, msg):
        print("[G-Helper]", msg, file=sys.stderr)
        t = Adw.Toast(title=GLib.markup_escape_text(msg), timeout=4)
        self.toasts.add_toast(t)

    def osd(self, title, body=""):
        if not self.cfg["overlay"]:
            return
        n = Gio.Notification.new(title)
        n.set_body(body)
        n.set_priority(Gio.NotificationPriority.LOW)
        self.send_notification("osd", n)

    def ask_reboot(self, msg):
        dlg = Adw.AlertDialog(heading="Neustart erforderlich", body=msg)
        dlg.add_response("later", "Später")
        dlg.add_response("reboot", "Jetzt neu starten")
        dlg.set_response_appearance("reboot", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("later")

        def resp(d, r):
            if r == "reboot":
                subprocess.Popen(["systemctl", "reboot"])
        dlg.connect("response", resp)
        self.window.present()
        dlg.present(self.window)

    # ------------------------------------------------------------------- Tray
    def _tray_items(self):
        if not self.ctl:
            return []
        cur = self.cfg["current_mode"]
        items = [{"id": 1, "label": "G-Helper öffnen", "action": lambda: self.toggle_window(force_show=True)},
                 {"id": 2, "type": "separator"}]
        nid = 10
        for mid in self.cfg["mode_order"]:
            items.append({"id": nid, "label": self.cfg.mode_by_id(mid)["name"], "type": "radio",
                          "checked": mid == cur, "action": lambda m=mid: self.ctl.set_mode(m)})
            nid += 1
        items.append({"id": 3, "type": "separator"})
        pref = self.cfg["gpu_pref"]
        gpus = (("eco", "Eco"), ("standard", "Standard"), ("optimized", "Optimiert")) if self.ctl.caps["dgpu"] else ()
        for gid, text in gpus:
            checked = pref == "optimized" if gid == "optimized" else (self.ctl.gpu_hw == gid and pref != "optimized")
            items.append({"id": nid, "label": text, "type": "radio", "checked": checked,
                          "action": lambda g=gid: self.ctl.set_gpu(g)})
            nid += 1
        items += [{"id": 4, "type": "separator"},
                  {"id": 5, "label": "Beenden", "action": self.quit}]
        return items

    def _on_event(self, ev, data):
        if ev in ("mode", "gpu", "power"):
            self.tray.refresh()
        if ev == "telemetry":
            t = data
            tip = f"{self.ctl.mode_label()} · GPU: {self.ctl.gpu_label()}"
            if t.cpu_temp is not None:
                tip += f" · CPU {t.cpu_temp:.0f}°C"
            if t.battery is not None:
                tip += f" · Akku {t.battery:.0f}%"
            self.tray.set_tooltip(tip)

    # ---------------------------------------------------------------- Hotkeys
    def on_hotkey(self, key):
        b = self.cfg["keybinds"].get(key, {})
        act = b.get("action", "none")
        if act == "toggle_window":
            self.toggle_window()
        elif act == "cycle_mode":
            self.ctl.cycle_mode()
        elif act.startswith("mode_"):
            self.ctl.set_mode(act[5:])
        elif act == "cycle_screen":
            order = ["auto", "min", "max"]
            cur = self.cfg["screen_mode"]
            self.ctl.set_screen(order[(order.index(cur) + 1) % 3] if cur in order else "auto")
        elif act == "mic_mute":
            subprocess.Popen(["wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@", "toggle"])
            self.osd("Mikrofon stummschalten", "")
        elif act == "mute":
            subprocess.Popen(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"])
        elif act in ("volume_up", "volume_down"):
            subprocess.Popen(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "5%+" if act == "volume_up" else "5%-"])
        elif act == "screenshot":
            if shutil.which("gnome-screenshot"):
                subprocess.Popen(["gnome-screenshot", "-i"])
        elif act == "aura":
            subprocess.Popen(["asusctl", "aura", "effect", "--next-mode"])
        elif act == "lock":
            subprocess.Popen(["loginctl", "lock-session"])
        elif act == "fnlock":
            self.ctl.set_fn_lock(not self.cfg["fn_lock"])
        elif act == "play_pause" and shutil.which("playerctl"):
            subprocess.Popen(["playerctl", "play-pause"])
        elif act == "command" and b.get("command"):
            subprocess.Popen(shlex.split(b["command"]), start_new_session=True)
        return False

    # ----------------------------------------------------------------- Debug
    def _screenshot(self, path):
        try:
            self._save_png(self.window, path)
            if self.fans_window is not None and self.fans_window.get_visible():
                self._save_png(self.fans_window, path.replace(".png", "-fans.png"))
            if self.extra is not None:
                self._save_png(self.extra, path.replace(".png", "-extra.png"))
        finally:
            self.quit()
        return False

    def _save_png(self, w, path):
        width, height = w.get_width(), w.get_height()
        paintable = Gtk.WidgetPaintable.new(w)
        snap = Gtk.Snapshot()
        paintable.snapshot(snap, width, height)
        node = snap.to_node()
        if node is not None:
            rect = Graphene.Rect().init(0, 0, width, height)
            tex = w.get_renderer().render_texture(node, rect)
            tex.save_to_png(path)
            print(f"Screenshot gespeichert: {path} ({width}x{height})")


def main():
    app = GHelperApp()
    return app.run(sys.argv)
