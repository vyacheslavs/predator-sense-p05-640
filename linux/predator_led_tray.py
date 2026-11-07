#!/usr/bin/env python3
"""System tray controller for Predator Orion PO5-640 LEDs (GTK3 + Ayatana AppIndicator)."""
import copy
import json
import os
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3 as AppIndicator, GLib, Gtk  # noqa: E402

import predator_led as pl  # noqa: E402

APP_ID = "predator-led-tray"
CONFIG_DIR = os.path.join(GLib.get_user_config_dir(), "predator-led")
STATE_FILE = os.path.join(CONFIG_DIR, "state.json")
ICON_DIR = os.path.join(GLib.get_user_cache_dir(), "predator-led")

PRESET_COLORS = [
    ("Predator cyan", "00AEC7"), ("Red", "FF0000"), ("Green", "00FF00"),
    ("Blue", "0000FF"), ("White", "FFFFFF"), ("Purple", "8000FF"),
    ("Orange", "FF6000"), ("Yellow", "FFD000"), ("Pink", "FF0080"),
]
BRIGHTNESS = [25, 50, 75, 100]
SPEEDS = range(1, 10)

DEFAULT_AREA = {"on": True, "effect": 0, "color": "00AEC7", "brightness": 100, "speed": 5}
DEFAULT_STATE = {
    "restore_on_start": True,
    "areas": {str(a): dict(DEFAULT_AREA) for a in pl.AREAS},
}


def load_state():
    try:
        with open(STATE_FILE) as f:
            st = json.load(f)
    except (OSError, ValueError):
        return copy.deepcopy(DEFAULT_STATE)
    merged = copy.deepcopy(DEFAULT_STATE)
    merged["restore_on_start"] = st.get("restore_on_start", True)
    for k, v in st.get("areas", {}).items():
        if k in merged["areas"] and isinstance(v, dict):
            merged["areas"][k].update(v)
    return merged


def save_state(st):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=2)
    os.replace(tmp, STATE_FILE)


def icon_path(color, on):
    """Write (once) and return an SVG tray icon: a fan glyph filled with the LED colour."""
    os.makedirs(ICON_DIR, exist_ok=True)
    fill = f"#{color}" if on else "#555555"
    name = f"led-{color if on else 'off'}"
    path = os.path.join(ICON_DIR, name + ".svg")
    if not os.path.exists(path):
        blades = "".join(
            f'<path transform="rotate({r} 32 32)" d="M32 32 C30 18 36 8 46 10 C44 20 40 27 32 32Z"/>'
            for r in (0, 90, 180, 270))
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">'
               f'<circle cx="32" cy="32" r="29" fill="none" stroke="{fill}" stroke-width="5"/>'
               f'<g fill="{fill}">{blades}</g><circle cx="32" cy="32" r="5" fill="{fill}"/></svg>')
        with open(path, "w") as f:
            f.write(svg)
    return path


class Tray:
    def __init__(self):
        self.state = load_state()
        self.updating = False   # suppress handlers while syncing widgets
        self.widgets = {}       # area -> dict of widgets
        self.ind = AppIndicator.Indicator.new(
            APP_ID, icon_path("00AEC7", True), AppIndicator.IndicatorCategory.HARDWARE)
        self.ind.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.ind.set_title("Predator LEDs")
        self.ind.set_menu(self.build_menu())
        self.sync_widgets()
        if self.state["restore_on_start"]:
            GLib.idle_add(self.apply_all)

    # ---------- hardware ----------
    def area_state(self, area):
        return self.state["areas"][str(area)]

    def apply_area(self, area):
        s = self.area_state(area)
        try:
            if s["on"]:
                pl.set_on(area, s["effect"], s["color"], s["brightness"], s["speed"])
            else:
                pl.set_off(area)
        except pl.LedError as e:
            self.error(str(e))
            return False
        return True

    def apply_all(self):
        """Global first, then only the areas that differ from it."""
        if self.apply_area(0):
            g = self.area_state(0)
            for a in pl.AREAS:
                if a and self.area_state(a) != g:
                    if not self.apply_area(a):
                        break
        self.update_icon()
        return False

    def change(self, area, **kw):
        if self.updating:
            return
        if area == 0:
            # the global zone drives every LED: mirror it into all areas
            for a in pl.AREAS:
                self.area_state(a).update(kw)
        else:
            self.area_state(area).update(kw)
        self.apply_area(area)
        save_state(self.state)
        self.sync_widgets()
        self.update_icon()

    # ---------- UI ----------
    def error(self, msg):
        d = Gtk.MessageDialog(message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.OK,
                              text="Predator LEDs", secondary_text=msg)
        d.run()
        d.destroy()

    def update_icon(self):
        g = self.area_state(0)
        any_on = any(self.area_state(a)["on"] for a in pl.AREAS)
        color = g["color"] if g["on"] else next(
            (self.area_state(a)["color"] for a in pl.AREAS if self.area_state(a)["on"]), g["color"])
        self.ind.set_icon_full(icon_path(color, any_on), "Predator LEDs")

    def radio_submenu(self, label, items, on_toggle):
        """items: list of (key, text). Returns (MenuItem, {key: RadioMenuItem})."""
        item = Gtk.MenuItem(label=label)
        menu = Gtk.Menu()
        group, radios = None, {}
        for key, text in items:
            r = Gtk.RadioMenuItem.new_with_label_from_widget(group, text)
            group = r
            r.connect("toggled", lambda w, k=key: w.get_active() and on_toggle(k))
            menu.append(r)
            radios[key] = r
        item.set_submenu(menu)
        return item, radios

    def build_area_menu(self, area):
        menu = Gtk.Menu()
        w = {}

        w["on"] = Gtk.CheckMenuItem(label="On")
        w["on"].connect("toggled", lambda i: self.change(area, on=i.get_active()))
        menu.append(w["on"])
        menu.append(Gtk.SeparatorMenuItem())

        item, w["effect"] = self.radio_submenu(
            "Effect", list(pl.EFFECT_NAMES.items()), lambda k: self.change(area, effect=k, on=True))
        menu.append(item)

        item, w["color"] = self.radio_submenu(
            "Colour", [(c, n) for n, c in PRESET_COLORS] + [("custom", "Custom…")],
            lambda k: self.pick_color(area) if k == "custom" else self.change(area, color=k, on=True))
        menu.append(item)

        item, w["brightness"] = self.radio_submenu(
            "Brightness", [(b, f"{b}%") for b in BRIGHTNESS],
            lambda k: self.change(area, brightness=k))
        menu.append(item)

        item, w["speed"] = self.radio_submenu(
            "Speed (animated effects)", [(s, str(s)) for s in SPEEDS],
            lambda k: self.change(area, speed=k))
        menu.append(item)

        self.widgets[area] = w
        top = Gtk.MenuItem(label=pl.AREA_NAMES[area])
        top.set_submenu(menu)
        return top

    def build_menu(self):
        menu = Gtk.Menu()
        for label, on in (("All on", True), ("All off", False)):
            i = Gtk.MenuItem(label=label)
            i.connect("activate", lambda _w, o=on: self.change(0, on=o))
            menu.append(i)
        menu.append(Gtk.SeparatorMenuItem())
        for area in pl.AREAS:
            menu.append(self.build_area_menu(area))
        menu.append(Gtk.SeparatorMenuItem())

        self.restore_item = Gtk.CheckMenuItem(label="Restore these settings at login")
        self.restore_item.connect("toggled", self.on_restore_toggled)
        menu.append(self.restore_item)
        reapply = Gtk.MenuItem(label="Re-apply settings")
        reapply.connect("activate", lambda _w: self.apply_all())
        menu.append(reapply)
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda _w: Gtk.main_quit())
        menu.append(quit_item)
        menu.show_all()
        return menu

    def on_restore_toggled(self, item):
        if not self.updating:
            self.state["restore_on_start"] = item.get_active()
            save_state(self.state)

    def sync_widgets(self):
        """Reflect self.state in the check/radio items without triggering handlers."""
        self.updating = True
        try:
            self.restore_item.set_active(self.state["restore_on_start"])
            for area, w in self.widgets.items():
                s = self.area_state(area)
                w["on"].set_active(s["on"])
                for key in ("effect", "brightness", "speed"):
                    r = w[key].get(s[key])
                    if r:
                        r.set_active(True)
                w["color"].get(s["color"].upper(), w["color"]["custom"]).set_active(True)
                for c, r in w["color"].items():   # colour only matters for some effects
                    r.set_sensitive(s["effect"] in pl.COLOR_EFFECTS)
        finally:
            self.updating = False

    def pick_color(self, area):
        if self.updating:
            return
        s = self.area_state(area)
        dlg = Gtk.ColorChooserDialog(title=f"{pl.AREA_NAMES[area]} colour")
        rgba = dlg.get_rgba()
        rgba.parse(f"#{s['color']}")
        dlg.set_rgba(rgba)
        if dlg.run() == Gtk.ResponseType.OK:
            c = dlg.get_rgba()
            hexc = "%02X%02X%02X" % (round(c.red * 255), round(c.green * 255), round(c.blue * 255))
            dlg.destroy()
            self.change(area, color=hexc, on=True)
        else:
            dlg.destroy()
            self.sync_widgets()   # revert the radio selection


def main():
    Tray()
    try:
        Gtk.main()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
