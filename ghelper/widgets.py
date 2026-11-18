"""Wiederverwendbare GTK4-Widgets im G-Helper-Stil."""
import math
from pathlib import Path

import cairo
import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

ICON_DIR = Path(__file__).resolve().parent / "icons"
_icon_cache = {}

# RForm.cs
FORE_MAIN = "#F0F0F0"
COLORS = {
    "eco": "#06B48A", "standard": "#3AAEEF", "turbo": "#FF2020", "custom": "#FF8000",
    "gray": "#A8A8A8", "chart_bg": "#232323", "chart_grid": "#464646",
}


def _tinted_texture(name, color):
    """Original-Icon laden und wie ControlHelper.AdjustImage einfärben (RGB = Farbe, Alpha bleibt)."""
    pb = GdkPixbuf.Pixbuf.new_from_file(str(ICON_DIR / f"{name}.png"))
    if not pb.get_has_alpha():
        pb = pb.add_alpha(False, 0, 0, 0)
    r, g, b = (int(color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    w, h, rs = pb.get_width(), pb.get_height(), pb.get_rowstride()
    src = pb.get_pixels()
    out = bytearray(src)
    for y in range(h):
        row = y * rs
        for x in range(w):
            o = row + x * 4
            out[o], out[o + 1], out[o + 2] = r, g, b
    tinted = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(bytes(out)), GdkPixbuf.Colorspace.RGB,
                                             True, 8, w, h, rs)
    return Gdk.Texture.new_for_pixbuf(tinted)


def icon(name, size=16, color=FORE_MAIN):
    key = (name, color)
    tex = _icon_cache.get(key)
    if tex is None:
        try:
            tex = _tinted_texture(name, color)
        except (OSError, GLib.Error) as e:
            print(f"[icon] {name}: {e}")
            tex = None
        _icon_cache[key] = tex
    img = Gtk.Image.new_from_paintable(tex) if tex else Gtk.Image.new_from_icon_name("image-missing")
    img.set_pixel_size(size)
    return img


def label(text, css=None, xalign=0.0, hexpand=False, wrap=False):
    lb = Gtk.Label(label=text, xalign=xalign, hexpand=hexpand)
    if wrap:
        lb.set_wrap(True)
    for c in (css or "").split():
        lb.add_css_class(c)
    return lb


class Tile(Gtk.Button):
    """Großer Kachel-Button mit Icon über Text, farbiger Rahmen wenn aktiv."""

    def __init__(self, text, icon_name=None, accent="eco", short=False, secondary=False):
        super().__init__(hexpand=True)
        self.add_css_class("gh-tile")
        self.accent = accent
        self.add_css_class(accent)
        if short:
            self.add_css_class("short")
        if secondary:
            self.add_css_class("secondary")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
        if icon_name:
            box.append(icon(icon_name, 22))
        self.text = Gtk.Label(label=text, justify=Gtk.Justification.CENTER, wrap=True)
        box.append(self.text)
        self.set_child(box)

    def set_active(self, active):
        (self.add_css_class if active else self.remove_css_class)("active")

    def set_busy(self, busy):
        (self.add_css_class if busy else self.remove_css_class)("busy")

    def set_accent(self, accent):
        self.remove_css_class(self.accent)
        self.accent = accent
        self.add_css_class(accent)


class SmallButton(Gtk.Button):
    def __init__(self, text, icon_name=None, tiny=False, main=False):
        super().__init__()
        self.add_css_class("gh-btn")
        if tiny:
            self.add_css_class("tiny")
        if main:
            self.add_css_class("main")
        box = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        if icon_name:
            box.append(icon(icon_name, 16))
        self.text = Gtk.Label(label=text)
        box.append(self.text)
        self.set_child(box)
        self.box = box

    def set_on(self, on, style="on-standard"):
        (self.add_css_class if on else self.remove_css_class)(style)


def section_header(icon_name, title):
    """→ (Box, Titel-Label, rechte Box)"""
    row = Gtk.Box(spacing=6)
    row.add_css_class("gh-section")
    if icon_name:
        row.append(icon(icon_name, 16))
    t = label(title, "gh-section-title")
    row.append(t)
    row.append(Gtk.Box(hexpand=True))
    right = Gtk.Box(spacing=6)
    row.append(right)
    return row, t, right


class Dropdown(Gtk.DropDown):
    def __init__(self, items, selected=None, on_change=None, hexpand=True):
        super().__init__(model=Gtk.StringList.new(list(items)), hexpand=hexpand)
        self.add_css_class("gh-drop")
        self.items = list(items)
        self._silent = False
        self._cb = on_change
        if selected in self.items:
            self.set_selected(self.items.index(selected))
        self.connect("notify::selected", self._changed)

    def _changed(self, *_):
        if not self._silent and self._cb:
            i = self.get_selected()
            if 0 <= i < len(self.items):
                self._cb(self.items[i])

    def set_value(self, value):
        if value in self.items:
            self._silent = True
            self.set_selected(self.items.index(value))
            self._silent = False

    def set_items(self, items, selected=None):
        self._silent = True
        self.items = list(items)
        self.set_model(Gtk.StringList.new(self.items))
        if selected in self.items:
            self.set_selected(self.items.index(selected))
        self._silent = False

    @property
    def value(self):
        i = self.get_selected()
        return self.items[i] if 0 <= i < len(self.items) else None


class Scale(Gtk.Scale):
    def __init__(self, lo, hi, step, value, on_change=None):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        self.set_range(lo, hi)
        self.set_increments(step, step)
        self.set_round_digits(0)
        self.set_draw_value(False)
        self.add_css_class("gh-scale")
        self.step = step
        self._silent = False
        self.set_value(value)
        self._cb = on_change
        self.connect("value-changed", self._changed)

    def _changed(self, *_):
        v = round(self.get_value() / self.step) * self.step
        if abs(v - self.get_value()) > 1e-6:
            self.set_value(v)
            return
        if not self._silent and self._cb:
            self._cb(int(v))

    def set_silent(self, v):
        self._silent = True
        self.set_value(v)
        self._silent = False


def check(text, active, on_toggle):
    cb = Gtk.CheckButton(label=text, active=active)
    cb.add_css_class("gh-check")
    cb.connect("toggled", lambda w: on_toggle(w.get_active()))
    return cb


class Debouncer:
    def __init__(self, ms, fn):
        self.ms, self.fn, self.src = ms, fn, None

    def __call__(self, *args):
        if self.src:
            GLib.source_remove(self.src)
        self.src = GLib.timeout_add(self.ms, self._fire, args)

    def _fire(self, args):
        self.src = None
        self.fn(*args)
        return False


def _hex(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))


class FanCurveGraph(Gtk.DrawingArea):
    """Interaktives 8-Punkt-Lüfterkurven-Diagramm (Temperatur → RPM)."""
    T_MIN, T_MAX = 20, 110
    PAD_L, PAD_R, PAD_T, PAD_B = 44, 14, 24, 20

    def __init__(self, title, color, max_rpm, on_changed):
        super().__init__(hexpand=True)
        self.add_css_class("gh-graph")
        self.set_content_height(168)
        self.set_content_width(480)
        self.title = title
        self.rgb = _hex(color)
        self.max_rpm = max_rpm
        self.on_changed = on_changed
        self.points = [[30, 0]] * 8
        self.factory = None
        self.current_temp = None
        self.applied = True        # Fans.cs: Serien grau, wenn Lüfterkurve nicht angewendet
        self.snap = True
        self.drag_idx = None
        self.hover_idx = None
        self._drag_start = (0, 0)
        self.set_draw_func(self._draw)

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        drag.connect("drag-end", self._drag_end)
        self.add_controller(drag)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._motion)
        motion.connect("leave", lambda *_: self._set_hover(None))
        self.add_controller(motion)

    # ------------------------------------------------------------ Geometrie
    def _geom(self):
        w, h = self.get_width(), self.get_height()
        return w - self.PAD_L - self.PAD_R, h - self.PAD_T - self.PAD_B

    def t2x(self, t):
        pw, _ = self._geom()
        return self.PAD_L + (t - self.T_MIN) / (self.T_MAX - self.T_MIN) * pw

    def r2y(self, r):
        _, ph = self._geom()
        return self.PAD_T + ph - min(r, self.max_rpm) / self.max_rpm * ph

    def x2t(self, x):
        pw, _ = self._geom()
        return self.T_MIN + (x - self.PAD_L) / pw * (self.T_MAX - self.T_MIN)

    def y2r(self, y):
        _, ph = self._geom()
        return (self.PAD_T + ph - y) / ph * self.max_rpm

    def set_points(self, pts, factory=None):
        self.points = [list(p) for p in pts]
        self.factory = factory
        self.queue_draw()

    def set_current_temp(self, t):
        if t != self.current_temp:
            self.current_temp = t
            self.queue_draw()

    # ------------------------------------------------------------ Interaktion
    def _nearest(self, x, y, radius=14):
        best, bd = None, radius
        for i, (t, r) in enumerate(self.points):
            d = math.hypot(self.t2x(t) - x, self.r2y(r) - y)
            if d <= bd:
                best, bd = i, d
        return best

    def _set_hover(self, idx):
        if idx != self.hover_idx:
            self.hover_idx = idx
            self.set_cursor(Gdk.Cursor.new_from_name("pointer" if idx is not None else "default"))
            self.queue_draw()

    def _motion(self, ctrl, x, y):
        if self.drag_idx is None:
            self._set_hover(self._nearest(x, y))

    def _drag_begin(self, g, x, y):
        self.drag_idx = self._nearest(x, y)
        self._drag_start = (x, y)
        if self.drag_idx is None:
            g.set_state(Gtk.EventSequenceState.DENIED)

    def _drag_update(self, g, ox, oy):
        if self.drag_idx is None:
            return
        x, y = self._drag_start[0] + ox, self._drag_start[1] + oy
        self._move(self.drag_idx, self.x2t(x), self.y2r(y))
        self.queue_draw()

    def _drag_end(self, g, ox, oy):
        if self.drag_idx is not None:
            self.drag_idx = None
            self.queue_draw()
            self.on_changed(self.points)

    def _move(self, i, t, r):
        pts = self.points
        if self.snap:
            t, r = round(t / 5) * 5, round(r / 200) * 200
        else:
            t, r = round(t), round(r / 50) * 50
        lo = pts[i - 1][0] + 1 if i > 0 else self.T_MIN
        hi = pts[i + 1][0] - 1 if i < 7 else self.T_MAX
        t = max(lo, min(hi, t))
        r = max(0, min(self.max_rpm, r))
        pts[i] = [t, r]
        for j in range(i + 1, 8):
            pts[j][1] = max(pts[j][1], r)
        for j in range(i):
            pts[j][1] = min(pts[j][1], r)

    # ------------------------------------------------------------ Zeichnen
    def _draw(self, area, cr, w, h):
        pw, ph = self._geom()
        L, T = self.PAD_L, self.PAD_T
        base = T + ph
        r, g, b = self.rgb if self.applied else (0.5, 0.5, 0.5)
        # Diagrammkasten (chartMain) über die ganze Fläche inkl. Achsen, abgerundet
        cr.set_source_rgb(*_hex(COLORS["chart_bg"]))
        rad = 5
        cr.new_sub_path()
        cr.arc(w - rad, rad, rad, -math.pi / 2, 0)
        cr.arc(w - rad, h - rad, rad, 0, math.pi / 2)
        cr.arc(rad, h - rad, rad, math.pi / 2, math.pi)
        cr.arc(rad, rad, rad, math.pi, 3 * math.pi / 2)
        cr.close_path()
        cr.fill()
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)

        # Titel
        cr.set_font_size(11.5)
        cr.set_source_rgb(*_hex("#F0F0F0"))
        ext = cr.text_extents(self.title)
        cr.move_to(L + pw / 2 - ext.width / 2, 13)
        cr.show_text(self.title)

        # Raster + Achsen
        cr.set_font_size(9.5)
        cr.set_line_width(1)
        for t in range(self.T_MIN, self.T_MAX + 1, 10):
            x = round(self.t2x(t)) + 0.5
            cr.set_source_rgb(*_hex(COLORS["chart_grid"]))
            cr.move_to(x, T)
            cr.line_to(x, base)
            cr.stroke()
            cr.set_source_rgb(*_hex("#F0F0F0"))
            s = str(t)
            e = cr.text_extents(s)
            cr.move_to(x - e.width / 2, base + 13)
            cr.show_text(s)
        steps = 8
        for i in range(steps + 1):
            rpm = self.max_rpm * i / steps
            y = round(self.r2y(rpm)) + 0.5
            cr.set_source_rgb(*_hex(COLORS["chart_grid"]))
            cr.move_to(L, y)
            cr.line_to(L + pw, y)
            cr.stroke()
            cr.set_source_rgb(*_hex("#F0F0F0"))
            s = "OFF" if i == 0 else f"{int(rpm)}"
            e = cr.text_extents(s)
            cr.move_to(L - e.width - 5, y + 3.5)
            cr.show_text(s)

        def curve_path(pts):
            cr.move_to(L, self.r2y(pts[0][1]))
            for t, rpm in pts:
                cr.line_to(self.t2x(t), self.r2y(rpm))
            cr.line_to(L + pw, self.r2y(pts[-1][1]))

        # Werkskurve als Referenzfläche
        if self.factory:
            fpts = sorted(self.factory)
            curve_path(fpts)
            cr.line_to(L + pw, base)
            cr.line_to(L, base)
            cr.close_path()
            cr.set_source_rgba(r, g, b, 0.10)
            cr.fill()
            curve_path(fpts)
            cr.set_source_rgba(r, g, b, 0.35)
            cr.set_dash([4, 3])
            cr.stroke()
            cr.set_dash([])

        # eigene Kurve
        curve_path(self.points)
        cr.line_to(L + pw, base)
        cr.line_to(L, base)
        cr.close_path()
        cr.set_source_rgba(r, g, b, 64 / 255)
        cr.fill()
        curve_path(self.points)
        cr.set_source_rgb(r, g, b)
        cr.set_line_width(2)
        cr.stroke()

        # aktuelle Temperatur
        if self.current_temp is not None and self.T_MIN <= self.current_temp <= self.T_MAX:
            x = self.t2x(self.current_temp)
            cr.set_source_rgba(1, 1, 1, 0.35)
            cr.set_line_width(1)
            cr.set_dash([3, 3])
            cr.move_to(x, T)
            cr.line_to(x, base)
            cr.stroke()
            cr.set_dash([])

        # Punkte
        for i, (t, rpm) in enumerate(self.points):
            x, y = self.t2x(t), self.r2y(rpm)
            rad = 6 if i in (self.hover_idx, self.drag_idx) else 4.5
            cr.arc(x, y, rad, 0, 2 * math.pi)
            cr.set_source_rgb(r, g, b)
            cr.fill_preserve()
            cr.set_source_rgba(1, 1, 1, 0.9 if i in (self.hover_idx, self.drag_idx) else 0.0)
            cr.set_line_width(1.5)
            cr.stroke()

        # Wert-Anzeige beim Ziehen/Hovern
        idx = self.drag_idx if self.drag_idx is not None else self.hover_idx
        if idx is not None:
            t, rpm = self.points[idx]
            s = f"{t}°C, " + ("OFF" if rpm == 0 else f"{rpm} RPM")
            cr.set_font_size(11)
            e = cr.text_extents(s)
            x = min(max(self.t2x(t) - e.width / 2, L), L + pw - e.width - 8)
            y = max(self.r2y(rpm) - 14, T + 12)
            cr.set_source_rgba(0.08, 0.08, 0.08, 0.92)
            cr.rectangle(x - 4, y - e.height - 3, e.width + 8, e.height + 7)
            cr.fill()
            cr.set_source_rgb(1, 1, 1)
            cr.move_to(x, y)
            cr.show_text(s)
