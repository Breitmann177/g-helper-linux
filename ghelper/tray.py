"""
System-Tray über das StatusNotifierItem-Protokoll (KDE nativ, GNOME mit der
AppIndicator-Erweiterung). Reine Gio-D-Bus-Implementierung – keine GTK3-
Abhängigkeit, daher mit GTK4 im selben Prozess nutzbar.
"""
import os

from gi.repository import Gio, GLib

SNI_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus"><arg name="status" type="s"/></signal>
  </interface>
</node>"""

MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg type="i" name="parentId" direction="in"/>
      <arg type="i" name="recursionDepth" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="u" name="revision" direction="out"/>
      <arg type="(ia{sv}av)" name="layout" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="a(ia{sv})" name="properties" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="name" direction="in"/>
      <arg type="v" name="value" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="eventId" direction="in"/>
      <arg type="v" name="data" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" name="events" direction="in"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg type="i" name="id" direction="in"/>
      <arg type="b" name="needUpdate" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="ai" name="updatesNeeded" direction="out"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg type="a(ia{sv})" name="updatedProps"/>
      <arg type="a(ias)" name="removedProps"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg type="u" name="revision"/>
      <arg type="i" name="parent"/>
    </signal>
  </interface>
</node>"""

SNI_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"


class Tray:
    """items: Liste von dicts {id, label, type: normal|separator|radio, checked, action}"""

    def __init__(self, icon_name, icon_theme_path, title, on_activate, build_items):
        self.icon_name = icon_name
        self.icon_theme_path = icon_theme_path
        self.title = title
        self.tooltip = title
        self.on_activate = on_activate
        self.build_items = build_items
        self.items = []
        self.revision = 1
        self.bus = None
        self.bus_name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self._ids = []
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error as e:
            print(f"[tray] Session-Bus nicht verfügbar: {e.message}")
            return
        sni = Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0]
        menu = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
        self._ids.append(self.bus.register_object(SNI_PATH, sni, self._sni_call, self._sni_prop, None))
        self._ids.append(self.bus.register_object(MENU_PATH, menu, self._menu_call, self._menu_prop, None))
        Gio.bus_own_name_on_connection(self.bus, self.bus_name, Gio.BusNameOwnerFlags.NONE, None, None)
        Gio.bus_watch_name_on_connection(self.bus, "org.kde.StatusNotifierWatcher",
                                         Gio.BusNameWatcherFlags.NONE, self._watcher_appeared, None)
        self.refresh()

    # ------------------------------------------------------------- Watcher
    def _watcher_appeared(self, conn, name, owner):
        conn.call("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher",
                  "org.kde.StatusNotifierWatcher", "RegisterStatusNotifierItem",
                  GLib.Variant("(s)", (self.bus_name,)), None, Gio.DBusCallFlags.NONE, -1, None,
                  self._registered)

    @staticmethod
    def _registered(conn, res):
        try:
            conn.call_finish(res)
        except GLib.Error as e:
            print(f"[tray] Registrierung fehlgeschlagen: {e.message}")

    # ----------------------------------------------------------------- SNI
    def _sni_prop(self, conn, sender, path, iface, prop):
        return {
            "Category": GLib.Variant("s", "Hardware"),
            "Id": GLib.Variant("s", "g-helper-linux"),
            "Title": GLib.Variant("s", self.title),
            "Status": GLib.Variant("s", "Active"),
            "WindowId": GLib.Variant("i", 0),
            "IconName": GLib.Variant("s", self.icon_name),
            "IconThemePath": GLib.Variant("s", self.icon_theme_path),
            "OverlayIconName": GLib.Variant("s", ""),
            "AttentionIconName": GLib.Variant("s", ""),
            "ToolTip": GLib.Variant("(sa(iiay)ss)", (self.icon_name, [], self.title, self.tooltip)),
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", MENU_PATH),
        }.get(prop)

    def _sni_call(self, conn, sender, path, iface, method, params, inv):
        if method in ("Activate", "SecondaryActivate"):
            GLib.idle_add(self.on_activate)
        inv.return_value(None)

    def set_tooltip(self, text):
        if self.bus and text != self.tooltip:
            self.tooltip = text
            self.bus.emit_signal(None, SNI_PATH, "org.kde.StatusNotifierItem", "NewToolTip", None)

    # ---------------------------------------------------------------- Menü
    def refresh(self):
        self.items = self.build_items()
        self.revision += 1
        if self.bus:
            self.bus.emit_signal(None, MENU_PATH, "com.canonical.dbusmenu", "LayoutUpdated",
                                 GLib.Variant("(ui)", (self.revision, 0)))

    @staticmethod
    def _props(item):
        p = {}
        if item.get("type") == "separator":
            p["type"] = GLib.Variant("s", "separator")
            return p
        p["label"] = GLib.Variant("s", item["label"])
        p["enabled"] = GLib.Variant("b", item.get("enabled", True))
        p["visible"] = GLib.Variant("b", True)
        if item.get("type") == "radio":
            p["toggle-type"] = GLib.Variant("s", "radio")
            p["toggle-state"] = GLib.Variant("i", 1 if item.get("checked") else 0)
        return p

    def _layout(self):
        children = [GLib.Variant("(ia{sv}av)", (it["id"], self._props(it), [])) for it in self.items]
        root = (0, {"children-display": GLib.Variant("s", "submenu")}, children)
        return GLib.Variant("(u(ia{sv}av))", (self.revision, root))

    def _menu_prop(self, conn, sender, path, iface, prop):
        return {
            "Version": GLib.Variant("u", 3),
            "TextDirection": GLib.Variant("s", "ltr"),
            "Status": GLib.Variant("s", "normal"),
            "IconThemePath": GLib.Variant("as", [self.icon_theme_path]),
        }.get(prop)

    def _menu_call(self, conn, sender, path, iface, method, params, inv):
        args = params.unpack()
        if method == "GetLayout":
            inv.return_value(self._layout())
        elif method == "GetGroupProperties":
            ids = args[0]
            res = [(it["id"], self._props(it)) for it in self.items if not ids or it["id"] in ids]
            inv.return_value(GLib.Variant("(a(ia{sv}))", (res,)))
        elif method == "GetProperty":
            item = next((it for it in self.items if it["id"] == args[0]), None)
            val = self._props(item).get(args[1]) if item else None
            inv.return_value(GLib.Variant("(v)", (val or GLib.Variant("s", ""),)))
        elif method == "Event":
            if args[1] == "clicked":
                self._clicked(args[0])
            inv.return_value(None)
        elif method == "EventGroup":
            for ev in args[0]:
                if ev[1] == "clicked":
                    self._clicked(ev[0])
            inv.return_value(GLib.Variant("(ai)", ([],)))
        elif method == "AboutToShow":
            inv.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup":
            inv.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            inv.return_value(None)

    def _clicked(self, item_id):
        item = next((it for it in self.items if it["id"] == item_id), None)
        if item and item.get("action"):
            GLib.idle_add(lambda: (item["action"](), False)[1])
