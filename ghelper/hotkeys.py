"""
Hotkey-Listener (evdev, nur lesend – kein exklusives Greifen der Tastatur).

Überwacht das Gerät "Asus WMI hotkeys" (ROG-/Armoury-Taste, M4, Fn+F5 …) und
für die Copilot-Taste (KEY_F23) die interne Tastatur. Ausgelöst wird nur bei
Codes, die in der Tastenbelegung konfiguriert sind (oder im Lernmodus).
"""
import select
import threading

from gi.repository import GLib

try:
    import evdev
    from evdev import ecodes
except ImportError:  # python3-evdev nicht installiert
    evdev = None

HOTKEY_DEVICES = ("Asus WMI hotkeys",)
KEYBOARD_DEVICES = ("AT Raw Set 2 keyboard", "AT Translated Set 2 keyboard")


class Hotkeys:
    def __init__(self, get_bindings, on_key):
        self.get_bindings = get_bindings     # → dict KEY_NAME → binding
        self.on_key = on_key                 # (key_name) im GTK-Thread
        self.learn_cb = None
        self.devices = []
        self.available = evdev is not None
        self.error = None if self.available else "python3-evdev nicht installiert"
        if self.available:
            threading.Thread(target=self._loop, daemon=True).start()

    def learn(self, cb):
        """Nächste Taste des Hotkey-Geräts an cb(key_name) liefern."""
        self.learn_cb = cb

    def _open(self):
        devs = []
        for path in evdev.list_devices():
            try:
                d = evdev.InputDevice(path)
            except OSError:
                continue
            if d.name in HOTKEY_DEVICES:
                devs.append((d, False))
            elif d.name in KEYBOARD_DEVICES:
                devs.append((d, True))   # nur für Copilot/F23 & konfigurierte Codes
            else:
                d.close()
        if not any(not kb for _, kb in devs):
            self.error = "Hotkey-Gerät nicht lesbar (Gruppe 'input' oder udev-Regel fehlt)"
        return devs

    def _loop(self):
        self.devices = self._open()
        fds = {d.fd: (d, kb) for d, kb in self.devices}
        while fds:
            try:
                r, _, _ = select.select(list(fds), [], [], 5)
            except (OSError, ValueError):
                break
            for fd in r:
                dev, is_kbd = fds[fd]
                try:
                    events = list(dev.read())
                except OSError:
                    fds.pop(fd, None)
                    continue
                for ev in events:
                    if ev.type != ecodes.EV_KEY or ev.value != 1:
                        continue
                    name = ecodes.KEY.get(ev.code)
                    if isinstance(name, list):
                        name = name[0]
                    if not name:
                        continue
                    if self.learn_cb and not is_kbd:
                        cb, self.learn_cb = self.learn_cb, None
                        GLib.idle_add(cb, name)
                        continue
                    binding = self.get_bindings().get(name)
                    if not binding or binding.get("action", "none") == "none":
                        continue
                    if is_kbd and name != "KEY_F23":
                        continue
                    GLib.idle_add(self.on_key, name)
