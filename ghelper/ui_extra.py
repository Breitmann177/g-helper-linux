"""Extra-Einstellungen – Aufbau nach G-Helper Extra.Designer.cs / Extra.cs.

Abschnitte wie im Original: Tastenbelegung · Laptopbeleuchtung · Sonstiges ·
Zeile „ASUS Dienste laufen: N“ mit Starten/Beenden. Texte aus Strings.de.resx.
"""
from gi.repository import Gtk

from . import hardware as hw
from .widgets import Dropdown, Scale, SmallButton, check, icon, label

# Extra.cs customActions (Reihenfolge und deutsche Texte des Originals)
ACTIONS = {
    "none": "Standard",
    "volume_down": "Lautstärke verringern",
    "volume_up": "Lautstärke erhöhen",
    "mute": "Stummschalten",
    "screenshot": "Druck",
    "play_pause": "Wiedergabe / Pause",
    "aura": "Aura umschalten",
    "cycle_mode": "Leistungsmodus",
    "cycle_screen": "Bildschirm umschalten",
    "lock": "Bildschirm sperren",
    "fnlock": "FN Sperre umschalten",
    "mic_mute": "Mikrofon stummschalten",
    "toggle_window": "G-Helper Fenster öffnen",
    "command": "Benutzerdefiniert",
}
ACTION_BY_LABEL = {v: k for k, v in ACTIONS.items()}

# Extra.Designer.cs: labelM1 … labelM4 ("M4/ROG:"), labelFNF4 …
KEY_NAMES = {
    "KEY_PROG1": "M1:",
    "KEY_PROG2": "M2:",
    "KEY_PROG3": "M4/ROG:",
    "KEY_PROG4": "M4:",
    "KEY_FN_F5": "FN+F5:",
    "KEY_F23": "Copilot:",
}
SERVICES = ("asusd", "supergfxd")


def title(icon_name, text):
    row = Gtk.Box(spacing=6, margin_top=10, margin_bottom=2)
    row.append(icon(icon_name, 16))
    row.append(label(text, "gh-section-title"))
    return row


class ExtraWindow(Gtk.Window):
    def __init__(self, parent, ctl, hotkeys):
        super().__init__(title="Extra-Einstellungen", transient_for=parent, modal=False,
                         default_width=500, default_height=760)
        self.add_css_class("ghelper")
        hb = Gtk.HeaderBar(show_title_buttons=True, decoration_layout=":close")
        hb.set_title_widget(Gtk.Box())
        hb.pack_start(label("Extra-Einstellungen", "title"))
        self.set_titlebar(hb)
        self.ctl, self.cfg, self.hotkeys = ctl, ctl.cfg, hotkeys
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                           margin_start=10, margin_end=10, margin_bottom=10)
        self.box.add_css_class("gh-root")
        scroll.set_child(self.box)
        self.set_child(scroll)
        self._bindings()
        self._backlight()
        self._other()
        self._services()

    # ------------------------------------------------------- Tastenbelegung
    def _bindings(self):
        head = title("keyboard", "Tastenbelegung")
        head.append(Gtk.Box(hexpand=True))
        learn = SmallButton("Taste erkennen", "add", tiny=True)
        learn.connect("clicked", self._learn)
        head.append(learn)
        reset = SmallButton("Zurücksetzen", tiny=True)
        reset.connect("clicked", self._reset)
        head.append(reset)
        self.box.append(head)
        self.keys_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.box.append(self.keys_box)
        self.learn_info = label("", "gh-dim-label")
        self.box.append(self.learn_info)
        self._fill_keys()
        if not self.hotkeys.available or self.hotkeys.error:
            self.learn_info.set_label(self.hotkeys.error or "")

    def _fill_keys(self):
        while (c := self.keys_box.get_first_child()):
            self.keys_box.remove(c)
        for key in self.cfg["keybinds"]:
            self._key_row(key)

    def _key_row(self, key):
        b = self.cfg["keybinds"][key]
        row = Gtk.Box(spacing=4)
        name = label(KEY_NAMES.get(key, key + ":"))
        name.set_size_request(80, -1)
        name.set_tooltip_text(key)
        row.append(name)
        entry = Gtk.Entry(text=b.get("command", ""), placeholder_text="Aktion", hexpand=True,
                          sensitive=b.get("action") == "command")
        entry.add_css_class("gh-entry")

        def changed(v, k=key, e=entry):
            act = ACTION_BY_LABEL[v]
            kb = self.cfg["keybinds"]
            kb[k] = {"action": act, "command": e.get_text()}
            self.cfg["keybinds"] = kb
            e.set_sensitive(act == "command")

        def cmd_changed(e, k=key):
            kb = self.cfg["keybinds"]
            kb[k]["command"] = e.get_text()
            self.cfg["keybinds"] = kb
        drop = Dropdown(list(ACTIONS.values()), ACTIONS.get(b.get("action", "none"), "Standard"), changed)
        drop.set_size_request(190, -1)
        drop.set_hexpand(False)
        entry.connect("changed", cmd_changed)
        row.append(drop)
        row.append(entry)
        self.keys_box.append(row)

    def _learn(self, *_):
        if not self.hotkeys.available:
            self.learn_info.set_label("python3-evdev fehlt")
            return
        self.learn_info.set_label("Jetzt die Taste drücken …")

        def got(name):
            self.learn_info.set_label(f"Erkannt: {name}")
            kb = self.cfg["keybinds"]
            if name not in kb:
                kb[name] = {"action": "toggle_window", "command": ""}
                self.cfg["keybinds"] = kb
                self._key_row(name)
            return False
        self.hotkeys.learn(got)

    def _reset(self, *_):
        from .config import DEFAULTS
        import copy
        self.cfg["keybinds"] = copy.deepcopy(DEFAULTS["keybinds"])
        self._fill_keys()

    # ---------------------------------------------------- Laptopbeleuchtung
    def _backlight(self):
        self.box.append(title("gamma", "Laptopbeleuchtung"))
        p = self.cfg["kbd"].setdefault("power", {"awake": True, "boot": True, "sleep": False, "shutdown": False})
        row = Gtk.Box(spacing=8)
        name = label("Tastatur")
        name.set_size_request(80, -1)
        row.append(name)
        for key, text in (("awake", "Aktiv"), ("boot", "Start"), ("sleep", "Standby"), ("shutdown", "Herunterfahren")):
            row.append(check(text, p.get(key, False), lambda a, k=key: self._power(k, a)))
        self.box.append(row)

        row = Gtk.Box(spacing=8)
        name = label("Helligkeit")
        name.set_size_request(80, -1)
        row.append(name)
        levels = ["off", "low", "med", "high"]
        cur = levels.index(self.cfg["kbd"].get("brightness", "high")) if self.cfg["kbd"].get("brightness") in levels else 3
        sc = Scale(0, 3, 1, cur, lambda v: self.ctl.set_kbd(brightness=levels[v]))
        row.append(sc)
        self.box.append(row)

    def _power(self, key, active):
        k = self.cfg["kbd"]
        k.setdefault("power", {})[key] = active
        self.cfg["kbd"] = k
        p = k["power"]
        flags = [f"--{n}" for n in ("boot", "awake", "sleep", "shutdown") if p.get(n)]

        def done(res):
            if isinstance(res, Exception) or not res[0]:
                self.ctl.toast(f"Laptopbeleuchtung: {res if isinstance(res, Exception) else res[1].splitlines()[-1]}")
        hw.run_async(lambda: hw.run(["asusctl", "aura", "power", "keyboard", *flags]), done)

    # -------------------------------------------------------------- Sonstiges
    def _other(self):
        self.box.append(title("settings", "Sonstiges"))
        a = self.cfg["automation"]

        def flag(key):
            def f(active):
                aa = self.cfg["automation"]
                aa[key] = active
                self.cfg["automation"] = aa
            return f
        # Originaloptionen, soweit unter Linux umsetzbar
        boot = hw.armoury_attr("boot_sound")
        cb = check("Boot-Sound", bool(boot and boot["current"] == 1), self._boot_sound)
        cb.set_sensitive(boot is not None)
        self.box.append(cb)
        self.box.append(check("dGPU beim Akkubetrieb abschalten (Auto-Eco)", a["auto_eco_battery"],
                              flag("auto_eco_battery")))
        self.box.append(check("Slash Lighting: Im Batteriebetrieb abschalten", a["slash_off_battery"],
                              flag("slash_off_battery")))

        names = ["Nicht ändern"] + [self.cfg.mode_by_id(m)["name"] for m in self.cfg["mode_order"]]
        ids = [""] + list(self.cfg["mode_order"])

        def setter(key):
            def f(v):
                aa = self.cfg["automation"]
                aa[key] = ids[names.index(v)]
                self.cfg["automation"] = aa
            return f
        for key, text in (("mode_on_ac", "Leistungsmodus am Netzteil"),
                          ("mode_on_battery", "Leistungsmodus im Akkubetrieb")):
            row = Gtk.Box(spacing=6)
            row.append(label(text, hexpand=True))
            sel = names[ids.index(a[key])] if a[key] in ids else names[0]
            d = Dropdown(names, sel, setter(key), hexpand=False)
            d.set_size_request(170, -1)
            row.append(d)
            self.box.append(row)

    def _boot_sound(self, active):
        self.ctl.helper.submit([{"op": "armoury", "name": "boot_sound", "value": 1 if active else 0}],
                               lambda r: self.ctl._report(r, "Boot-Sound"))

    # ------------------------------------------------------- ASUS Dienste
    def _services(self):
        row = Gtk.Box(spacing=6, margin_top=14)
        self.services_label = label("", "gh-section-title", hexpand=True)
        row.append(self.services_label)
        self.services_btn = SmallButton("", main=True)
        self.services_btn.connect("clicked", self._toggle_services)
        row.append(self.services_btn)
        self.box.append(row)
        self._refresh_services()

    def _running(self):
        return sum(1 for s in SERVICES if self.ctl.service_state(s) == "active")

    def _refresh_services(self):
        n = self._running()
        self.services_label.set_label(f"ASUS Dienste laufen: {n}")
        self.services_btn.text.set_label("Beenden" if n else "Starten")

    def _toggle_services(self, *_):
        action = "stop" if self._running() else "start"
        self.services_label.set_label("Beende Dienste" if action == "stop" else "Starte Dienste")
        for s in SERVICES:
            self.ctl.service(s, action, self._refresh_services)
