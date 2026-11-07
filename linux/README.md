# predator-led — Predator Orion PO5-640 LED control for Linux

Control the RGB lighting (CPU cooler fan, case fans, motherboard) of an
**Acer Predator Orion PO5-640** desktop on Linux, without PredatorSense.

The package has three parts:

| Component | What it is |
|---|---|
| `predator_wmi` | Small kernel module that lets userspace call the BIOS lighting interface (ACPI-WMI). Built with DKMS. |
| `predator-led` (`predator_led.py`) | Command-line tool and Python library that knows the lighting protocol. |
| `predator-led-tray` (`predator_led_tray.py`) | System tray app (GTK3 + Ayatana AppIndicator) for everyday use. |

Everything below was reverse-engineered from PredatorSense V3.00.3062 (the
Windows software Acer ships for this machine) and then verified on a real
PO5-640 running Linux Mint 22.3 (kernel 6.14, XFCE).

---

## Quick start

```sh
cd linux
./build-deb.sh                                   # -> dist/predator-led_1.0.0_all.deb
sudo apt install ./dist/predator-led_1.0.0_all.deb
# log out and back in once (new group membership) - the tray icon then starts automatically
```

Command line:

```sh
predator-led off                         # all LEDs off
predator-led on --color FF0000           # everything static red
predator-led on 1 --color 00AEC7         # CPU fan only, Predator cyan
predator-led on 2 --effect 1             # case fan 1, breathing
predator-led off 5                       # motherboard LEDs off
```

---

## 1. How the lighting is controlled (findings)

### 1.1 Not USB — it is the BIOS

* PredatorSense contains a driver for a Cooler Master USB-HID "Acer RGB BOX"
  (`CMUOT_ACER.dll`, USB vendor `0x2516`, product IDs `0x006B 0x006D 0x0069
  0x0093 0x1011 0x004F 0x0105 0x0107 0x0109 0x010B`). **The PO5-640 does not
  have one**: `lsusb` shows no `2516:*` device. That path is for other models.
* `PredatorHID.dll` in the UWP bundle is for Predator gaming mice and isn't
  related to the case lighting.
* The PO5-640 lighting is driven by the **BIOS** through the ACPI-WMI class
  `AcerGamingFunction`. PredatorSense calls it by method name through
  `IWbemServices::ExecMethod` (`ROOT\WMI`, `SELECT * FROM AcerGamingFunction`).
  The newer .NET UI goes through the `predatorsense_service_namedpipe` pipe to
  the service, which makes the WMI call.

### 1.2 The WMI interface

| Item | Value |
|---|---|
| WMI GUID | `7A4DDFE7-5B5D-40B4-8595-4408E0CC7F56` (same GUID as the "gaming" interface in Linux `acer-wmi`) |
| ACPI object | `\_SB.WMID`, method `WMBH(instance, method_id, buffer)` |
| Implementation | `WMBH` copies the input into `WBUF`, writes `WCMD = method_id \| 0x40` and calls `PHSR()` (an SMI). The real logic lives in SMM firmware, so the DSDT doesn't show it. |
| Methods | 1 … 0x0B. Methods 1–8 go to the SMI handler; 9–0x0B return constant buffers; anything else returns `1`. |

`WMBH` dispatch from the DSDT, with the size of the returned buffer:

| Method | Returns | Meaning on the PO5-640 |
|---|---|---|
| 1 | 8 bytes | unknown getter (answers `0x3f0000000000` for area masks 1 and 2, `0x2` for others — probably *GetSupportedLightPatternsOfArea*) |
| 2 | 4 bytes | **not lighting** — probably *SetGamingSystemConfiguration* (fan modes). **Don't call it.** |
| 3 | 8 bytes | unknown getter (always `0x1c0000` in our tests) |
| 4 | 4 bytes | unknown setter |
| **5** | 8 bytes | **SetGamingLedBehavior** ✅ verified |
| 6 | 8 bytes | GetGamingLedBehavior (inferred) |
| **7** | 4 bytes | **SetGamingRgbSetting** ✅ verified |
| 8 | 8 bytes | GetGamingRgbSetting (inferred) |
| 9–11 | const | stubs |

> ⚠️ **The method IDs differ from Acer laptops.** The Linux `acer-wmi` driver
> uses method 2 for *SetGamingLedBehavior* and 5 for *GetGamingSysInfo* on
> laptops. On this desktop, method 5 is the LED setter and method 2 is
> something else. The compressed binary MOF (`FOMB`/`DS` format) that maps
> names to IDs couldn't be decoded, so 5 and 7 were found by testing on the
> hardware. The return size is **not** a reliable hint for set vs get here.

### 1.3 SetGamingLedBehavior (method 5) — 64-bit input

From PredatorSense's `LightingPage_PO5_640.Set_WMI_LightingEffects_Behavior`
(.NET) and `PSAdminAgent.exe` (native, function at `0x140014f00`):

```
byte 0-1  area bitmask      1 << area
byte 2    status            1 = on, 0 = off
byte 3    effect            see effect table
byte 4    0x07              constant (older models use 0x05 / 0x06)
byte 5    speed             1..9, PredatorSense default 5
byte 6    duration          PredatorSense default 5
byte 7    type              0 normally; effects 19/20 force type = 1

on :  (1<<area) | 1<<16 | effect<<24 | 7<<32 | speed<<40 | duration<<48 | type<<56
off:  (1<<area)                      # bare mask, all other bytes 0
```

Example: area 0 static on = `0x0005050700010001`, area 0 off = `0x1`.

PredatorSense also clears some fields depending on the effect category
(`LightingEffectTypeCheck`): speed, duration or type are zeroed for effects
that don't use them. The BIOS doesn't seem to mind non-zero values.

### 1.4 SetGamingRgbSetting (method 7) — 64-bit input

```
byte 0-1  area bitmask      1 << area
byte 2    R
byte 3    G
byte 4    B
byte 5    brightness        0..100, PredatorSense always sends 100 (0x64)

value = (1<<area) | R<<16 | G<<24 | B<<32 | brightness<<40
```

PredatorSense only sends a colour when the effect takes one: effects
`0, 1, 2, 3, 9, 15, 17` with `type = 0`. The rainbow/random effects choose
their own colours.

### 1.5 Areas (zones)

PredatorSense sets `CONTROL_AREA_AMOUNT_PO5 = 5` (6 when a supported NVIDIA GPU
is present). The UI (`lightingpage_po5_640.baml`) shows one checkbox and icon
per area:

| Area | Mask | Zone |
|---|---|---|
| 0 | `0x01` | **Global** — all LEDs ("Global Control" mode) |
| 1 | `0x02` | CPU fan (air cooler) |
| 2 | `0x04` | Case fan |
| 3 | `0x08` | Case fan (icon inferred) |
| 4 | `0x10` | Case fan (icon inferred) |
| 5 | `0x20` | Motherboard |
| 6 | `0x40` | NVIDIA GPU lighting — PredatorSense uses separate GPU calls (`SetNVGPULighting/Color`) for this, not method 5/7 |

In "Area Control" mode PredatorSense sends areas 1–5 one at a time. Global
mode uses area 0 only. Turning area 0 off turns off every LED.

### 1.6 Effects

From the UI layout (`StackPanel_LightingEffectN` → `MUI_*` label):

| # | Effect | # | Effect |
|---|---|---|---|
| 0 | Static ✅ | 9 | Wave |
| 1 | Breathing ✅ | 10 | Risen |
| 2 | Heart beat | 11 | Rainbow stack |
| 3 | Blink | 12 | Extend |
| 4 | Breath random | 13 | Meteor shower |
| 5 | Blink random | 14 | Fluid magic light |
| 6 | Parallel rainbow | 15 | Music volume meter |
| 7 | Sequential rainbow | 16 | Music rock & roll |
| 8 | Breathing rainbow | 17 | Gaming interactive |

(The UI uses 100 internally to mean "off"; the BIOS gets status 0 instead.)

### 1.7 Machine configuration from PredatorSense

`plugs/0000000000000000/Predator PO5-640/`:

* `Feature.ini`: `MachineType Type=9`, `Lighting=1`, `SyncSupport=1`,
  `SupportMotherboardLED=1`, `LightingSupport` entries all 0 (old-style zone flags, not used for Type 9).
* `DefaultSetting.ini` (factory lighting): `DefaultAreaAmount=5`,
  area 0 effect 1 (breathing), other areas effect 0, speed 5, duration 5,
  colour `00AEC7` (Predator cyan).

### 1.8 Raw observations (for future digging)

| Call | Output |
|---|---|
| `5  0x0005050701010001` (on) | `0x0005050700000000` |
| `5  0x1` / `7 …` (off, colour) | `0` |
| `6  0x0000640000ff0001` | `0x0000010303010100` |
| `1  mask 1 or 2` (4-byte input) | `0x00003f0000000000` |
| `1  mask 4…0x20` | `0x2` |
| `3  any` | `0x00000000001c0000` |
| `8  any` (before any colour was set) | `0x000000000000ff00` |

### 1.9 Where it was found in PredatorSense

| File | What's there |
|---|---|
| `PredatorSense Service/PSAdminAgent.exe` | Native lighting code: `WindowsMessageLoop::Lighting_effects_setting`, `Reset_all_settings`, value packing, WMI calls (`SetGamingLedBehavior`, `SetGamingRgbSetting`, `input`/`output` params) |
| `PredatorSense Service/PSSvc.exe` | Service: named pipe, WMI wrappers, `kSvcCmdWMI*` commands |
| `PredatorSense Service/CMUOT_ACER.dll` | Cooler Master RGB-box USB-HID driver (not used on PO5-640) |
| UWP bundle → `Win32/PredatorSense.exe` (.NET) | `LightingPage_PO5_640` (exact value layout), `WMIFunction`, `lightingpage_po5_640.baml` (area icons, effect names) |

Tools used: `objdump` (x86-64 disassembly of the native binaries), the
`acpica-tools` `iasl` (DSDT decompile), and a small Mono.Cecil IL dumper for
the .NET app (`monodis` crashes on its WPF references). The Linux `acer-wmi.ko`
disassembly confirmed the bitmask/status layout (`0x10001` on, `0x1` off).

---

## 2. Kernel module `predator_wmi`

A minimal bridge: it calls `wmi_evaluate_method()` on the Acer gaming GUID and
exposes one sysfs file.

```
/sys/kernel/predator_wmi/call
  write:  "<method_id> <hex_value> [input_size]"     input_size = 8 (default) or 4
  read:   method=<id> in=0x… out=0x… out_type=<acpi type> status=<ACPI status>
```

```sh
echo "5 1" > /sys/kernel/predator_wmi/call     # area 0 off
cat /sys/kernel/predator_wmi/call
```

* **Safety:** by default only methods **5–8** (the lighting methods) are
  accepted; anything else returns `EPERM`. This is what makes it reasonably
  safe to give the file to an unprivileged group. To experiment with other
  methods: `sudo modprobe predator_wmi allow_any_method=1` (careful: method 2
  probably changes fan settings).
* The module refuses to load (`ENODEV`) if the GUID isn't present.
* The "last result" is global, so simultaneous callers can see each other's
  results. That's fine for this use.

Manual build (without the package):

```sh
make
sudo insmod predator_wmi.ko
```

---

## 3. `predator-led` command-line tool

`predator_led.py` is both a CLI and the library the tray app imports.

```
predator-led off [areas]                      # default: all areas (0..5)
predator-led on  [areas] [--effect N] [--color RRGGBB] [--brightness 0-100]
                         [--speed N] [--duration N] [--type N]
predator-led get [areas]                      # read back (methods 6/8, inferred)
predator-led raw METHOD HEXVALUE [4|8]        # raw call (5-8 unless allow_any_method=1)
predator-led --help                           # includes area + effect tables
```

`areas` is `all` or a comma list such as `0` or `1,3`. `on` defaults to area 0
(global) and effect 0 (static). Each call prints the module's result line.

Library API:

```python
import predator_led as pl
pl.set_off(area)
pl.set_on(area, effect=0, color="FF0000", brightness=100, speed=5, duration=5)
pl.wmi_call(method, value, size=8)         # raises pl.LedError
pl.AREA_NAMES, pl.EFFECT_NAMES, pl.COLOR_EFFECTS
```

Access: root, or a member of the `predator-led` group (set up by the systemd
service, see below).

---

## 4. System tray app `predator-led-tray`

GTK3 + Ayatana AppIndicator. Works on XFCE, Cinnamon, MATE, KDE, and GNOME
with the AppIndicator extension.

Menu:

* **All on / All off** — global switch (area 0).
* One submenu per area (**All, CPU fan, Case fan 1–3, Motherboard**), each with:
  * **On** checkbox
  * **Effect** — all 18 effects
  * **Colour** — presets (Predator cyan, red, green, blue, white, purple,
    orange, yellow, pink) + **Custom…** colour picker. Greyed out for effects
    that choose their own colours.
  * **Brightness** — 25 / 50 / 75 / 100 %
  * **Speed** — 1–9 (animated effects)
* **Restore these settings at login** (on by default)
* **Re-apply settings**, **Quit**

Behaviour:

* Settings are saved to `~/.config/predator-led/state.json`.
* At start it applies the saved settings: global (area 0) first, then only the
  areas that differ from it.
* Changing **All** copies the setting to every area, which matches what the
  hardware does.
* The tray icon is a fan drawn in the current LED colour (grey when everything
  is off). Icons are cached in `~/.cache/predator-led/`.
* Errors (module not loaded, no permission) are shown in a dialog.

Autostart: `/etc/xdg/autostart/predator-led-tray.desktop` (installed by the
package). It's also in the applications menu as **Predator LEDs**.

---

## 5. Packaging, boot and kernel upgrades

`./build-deb.sh` (optionally `VERSION=1.0.1 ./build-deb.sh`) builds
`dist/predator-led_<version>_all.deb`. Layout:

| Path | Content |
|---|---|
| `/usr/src/predator-wmi-<ver>/` | module source + `dkms.conf` (`AUTOINSTALL=yes`) |
| `/usr/lib/predator-led/` | `predator_led.py`, `predator_led_tray.py` |
| `/usr/bin/predator-led`, `/usr/bin/predator-led-tray` | symlinks |
| `/usr/lib/systemd/system/predator-led.service` | boot service |
| `/usr/share/applications/`, `/etc/xdg/autostart/` | tray `.desktop` file |

Install script (`postinst`):

1. creates the system group `predator-led` and adds the user who ran `sudo apt install`;
2. `dkms add/build/install` for every kernel with headers installed;
3. enables and starts `predator-led.service`.

**Kernel upgrades:** DKMS rebuilds the module automatically when a new kernel
(with headers) is installed. Check with `dkms status`.

**Boot sequence:** `predator-led.service` loads the module, runs `chgrp
predator-led` and `chmod 0660` on the sysfs file, and turns all LEDs **off**.
After you log in, the tray app restores your saved settings.

Remove: `sudo apt remove predator-led` (stops the service, removes the DKMS
module). `purge` also deletes the group.

Dependencies: `dkms`, `python3`, `python3-gi`, `gir1.2-gtk-3.0`,
`gir1.2-ayatanaappindicator3-0.1`, `systemd`, and kernel headers for DKMS.

---

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| `predator_wmi kernel module is not loaded` | `sudo systemctl restart predator-led`; check `dkms status` and that `linux-headers-$(uname -r)` is installed |
| `no permission to write …` | log out and back in after installing (group membership); check `id` shows `predator-led` |
| `modprobe: FATAL: Module predator_wmi not found` after a kernel update | install headers for the new kernel, then `sudo dkms autoinstall` |
| Calls return `AE_OK` but nothing changes | make sure you're using method 5/7 (not 2); try area 0 (global) first |
| `raw` returns `Operation not permitted` | method outside 5–8; load the module with `allow_any_method=1` |
| Fans behave oddly after experiments | method 2 was called; reboot or load BIOS defaults |
| Tray icon doesn't appear | the desktop needs AppIndicator/StatusNotifier support (XFCE: the "Status Tray Plugin" panel item) |

---

## 7. Open questions

* Exact meaning of getter methods 1, 3, 6, 8 and of the reply bytes.
* Method 2 and 4 (likely system configuration / profile) — untested, keep out.
* Whether areas 3 and 4 are exactly the case fans the UI icons suggest
  (verify by lighting them one at a time).
* NVIDIA GPU lighting (area 6) — PredatorSense uses separate GPU calls; not implemented.
* Decoding the BIOS binary MOF (`/sys/bus/wmi/devices/05901221-…/bmof`,
  DS-compressed) would confirm every method name ↔ ID mapping.
