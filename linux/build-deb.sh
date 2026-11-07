#!/bin/sh
# Builds predator-led_<version>_all.deb in ./dist
set -e
umask 022
cd "$(dirname "$0")"
VERSION=${VERSION:-1.0.0}
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
chmod 755 "$STAGE"
OUT=dist/predator-led_${VERSION}_all.deb

sub() { sed "s/@VERSION@/$VERSION/g" "$1"; }

# kernel module source for DKMS
SRC=$STAGE/usr/src/predator-wmi-$VERSION
install -d "$SRC"
install -m 644 predator_wmi.c "$SRC/"
printf 'obj-m += predator_wmi.o\n' > "$SRC/Makefile"
sub packaging/dkms.conf > "$SRC/dkms.conf"

# python tool + tray
install -d "$STAGE/usr/lib/predator-led" "$STAGE/usr/bin"
install -m 755 predator_led.py predator_led_tray.py "$STAGE/usr/lib/predator-led/"
ln -s ../lib/predator-led/predator_led.py "$STAGE/usr/bin/predator-led"
ln -s ../lib/predator-led/predator_led_tray.py "$STAGE/usr/bin/predator-led-tray"

# systemd, menu entry, autostart
install -D -m 644 predator-led.service "$STAGE/usr/lib/systemd/system/predator-led.service"
install -D -m 644 predator-led-tray.desktop "$STAGE/usr/share/applications/predator-led-tray.desktop"
install -D -m 644 predator-led-tray.desktop "$STAGE/etc/xdg/autostart/predator-led-tray.desktop"

# docs
DOC=$STAGE/usr/share/doc/predator-led
install -d "$DOC"
install -m 644 packaging/copyright "$DOC/copyright"
install -m 644 README.md "$DOC/README.md"
gzip -9n < packaging/changelog > "$DOC/changelog.gz"

# package metadata
install -d "$STAGE/DEBIAN"
sub packaging/control > "$STAGE/DEBIAN/control"
for s in postinst prerm postrm; do
    sub packaging/$s > "$STAGE/DEBIAN/$s"
    chmod 755 "$STAGE/DEBIAN/$s"
done
echo /etc/xdg/autostart/predator-led-tray.desktop > "$STAGE/DEBIAN/conffiles"
echo "Installed-Size: $(du -sk --exclude=DEBIAN "$STAGE" | cut -f1)" >> "$STAGE/DEBIAN/control"

mkdir -p dist
dpkg-deb --root-owner-group -Zxz --build "$STAGE" "$OUT" >/dev/null
echo "built $OUT"
