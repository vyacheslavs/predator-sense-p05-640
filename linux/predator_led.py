#!/usr/bin/env python3
"""
predator-led - control Predator Orion PO5-640 case/fan lighting on Linux.

Talks to the BIOS through the AcerGamingFunction ACPI-WMI interface
(GUID 7A4DDFE7-..., ACPI method \\_SB.WMID.WMBH) exposed by the predator_wmi
kernel module (/sys/kernel/predator_wmi/call).

Value layout taken from PredatorSense 3.00.3062
LightingPage_PO5_640.Set_WMI_LightingEffects_Behavior / _Color:

  SetGamingLedBehavior  (WMI method 5 - verified on hardware)
     on : (1<<area) | 1<<16 | effect<<24 | 7<<32 | speed<<40 | duration<<48 | type<<56
     off: (1<<area)                       (bare mask, status byte = 0)

  SetGamingRgbSetting   (WMI method 7 - verified on hardware)
     (1<<area) | R<<16 | G<<24 | B<<32 | brightness<<40

Areas (icons in PredatorSense lightingpage_po5_640.baml):
  0 global (all)   1 CPU fan (air cooler)   2-4 case fans   5 motherboard   6 GPU (NVIDIA only)

Effects:
  0 static  1 breathing  2 heart beat  3 blink  4 breath random  5 blink random
  6 parallel rainbow  7 sequential rainbow  8 breathing rainbow  9 wave  10 risen
  11 rainbow stack  12 extend  13 meteor shower  14 fluid magic light
  15 music volume meter  16 music rock&roll  17 gaming interactive
"""
import argparse
import sys

SYSFS = "/sys/kernel/predator_wmi/call"
LED_METHOD = 5      # SetGamingLedBehavior (verified)
GET_LED_METHOD = 6  # GetGamingLedBehavior (inferred)
RGB_METHOD = 7      # SetGamingRgbSetting  (verified)
GET_RGB_METHOD = 8  # GetGamingRgbSetting  (inferred)
AREAS = range(6)

AREA_NAMES = {
    0: "All (global)",
    1: "CPU fan",
    2: "Case fan 1",
    3: "Case fan 2",
    4: "Case fan 3",
    5: "Motherboard",
}

EFFECT_NAMES = {
    0: "Static", 1: "Breathing", 2: "Heart beat", 3: "Blink", 4: "Breath random",
    5: "Blink random", 6: "Parallel rainbow", 7: "Sequential rainbow",
    8: "Breathing rainbow", 9: "Wave", 10: "Risen", 11: "Rainbow stack",
    12: "Extend", 13: "Meteor shower", 14: "Fluid magic light",
    15: "Music volume meter", 16: "Music rock & roll", 17: "Gaming interactive",
}
# PredatorSense only sends a colour for these effects (the others pick their own colours)
COLOR_EFFECTS = {0, 1, 2, 3, 9, 15, 17}


class LedError(Exception):
    pass


def wmi_call(method, value, size=8):
    """Call a WMBH method; returns the sysfs result line. Raises LedError."""
    try:
        with open(SYSFS, "w") as f:
            f.write(f"{method} {value:x} {size}\n")
        with open(SYSFS) as f:
            return f.read().strip()
    except FileNotFoundError:
        raise LedError("predator_wmi kernel module is not loaded")
    except PermissionError:
        raise LedError(f"no permission to write {SYSFS} (run as root or join the predator-led group)")
    except OSError as e:
        raise LedError(f"WMI method {method} in=0x{value:016x} failed: {e}")


def led_on_value(area, effect=0, speed=5, duration=5, typ=0):
    if effect in (19, 20):      # same normalisation PredatorSense does
        typ = 1
    return ((1 << area) | 1 << 16 | (effect & 0xFF) << 24 | 0x07 << 32
            | (speed & 0xFF) << 40 | (duration & 0xFF) << 48 | (typ & 0xFF) << 56)


def rgb_value(area, color, brightness=100):
    color = color.lstrip("#")
    r, g, b = (int(color[i:i + 2], 16) for i in (0, 2, 4))
    return (1 << area) | r << 16 | g << 24 | b << 32 | (brightness & 0xFF) << 40


def set_off(area):
    return [wmi_call(LED_METHOD, 1 << area)]


def set_on(area, effect=0, color=None, brightness=100, speed=5, duration=5, typ=0):
    res = [wmi_call(LED_METHOD, led_on_value(area, effect, speed, duration, typ))]
    if color and effect in COLOR_EFFECTS:
        res.append(wmi_call(RGB_METHOD, rgb_value(area, color, brightness)))
    return res


def parse_areas(s):
    return list(AREAS) if s == "all" else [int(x) for x in s.split(",")]


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    o = sub.add_parser("off", help="turn lighting off")
    o.add_argument("areas", nargs="?", default="all", help="'all' (default) or e.g. 0,2")

    s = sub.add_parser("on", help="turn lighting on with an effect (and optional colour)")
    s.add_argument("areas", nargs="?", default="0", help="default 0 (global)")
    s.add_argument("--effect", type=int, default=0, help="see effect list above (default 0 = static)")
    s.add_argument("--speed", type=int, default=5)
    s.add_argument("--duration", type=int, default=5)
    s.add_argument("--type", type=int, default=0)
    s.add_argument("--color", help="RRGGBB, e.g. 00AEC7")
    s.add_argument("--brightness", type=int, default=100)

    g = sub.add_parser("get", help="read LED behaviour + colour of areas (methods 6/8)")
    g.add_argument("areas", nargs="?", default="0")

    r = sub.add_parser("raw", help="raw WMI call: METHOD HEXVALUE [SIZE] "
                                   "(methods other than 5-8 need allow_any_method=1)")
    r.add_argument("method", type=int)
    r.add_argument("value", type=lambda v: int(v, 16))
    r.add_argument("size", type=int, nargs="?", default=8)

    a = p.parse_args()
    out = []
    try:
        if a.cmd == "off":
            for area in parse_areas(a.areas):
                out += set_off(area)
        elif a.cmd == "on":
            for area in parse_areas(a.areas):
                out += set_on(area, a.effect, a.color, a.brightness, a.speed, a.duration, a.type)
        elif a.cmd == "get":
            for area in parse_areas(a.areas):
                out.append(f"--- area {area}")
                out.append(wmi_call(GET_LED_METHOD, 1 << area, 4))
                out.append(wmi_call(GET_RGB_METHOD, 1 << area, 4))
        elif a.cmd == "raw":
            out.append(wmi_call(a.method, a.value, a.size))
    except LedError as e:
        print("\n".join(out))
        sys.exit(f"error: {e}")
    print("\n".join(out))


if __name__ == "__main__":
    main()
