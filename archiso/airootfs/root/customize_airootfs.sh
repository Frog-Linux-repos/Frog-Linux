#!/usr/bin/env bash
# Runs inside the pacstrapped chroot via mkarchiso's _make_customize_airootfs,
# after pacstrap and after the overlay has been copied in. mkarchiso deletes
# this script afterwards, so nothing here ships on the ISO.
set -euo pipefail

# Live user's groups.
# This cannot move into the build driver, which runs before pacstrap when
# /etc/group does not exist yet. Creating it early makes pacman treat
# filesystem's /etc/group as a conflicting backup file and divert it to
# group.pacnew, which _cleanup_pacstrap_dir then deletes. By now /etc/group is
# the real one: filesystem's root entry plus whatever systemd-sysusers built
# from /usr/lib/sysusers.d, at canonical GIDs.
#
# UID 1500 keeps the live user clear of the 1000+ range Calamares hands to the
# first real account.
getent group liveuser >/dev/null || groupadd -g 1500 liveuser
getent group wheel    >/dev/null || groupadd -r wheel
usermod -g liveuser -aG wheel liveuser

# The package archive was verified by the builder before staging. Skip its
# scriptlet here because mkarchiso deliberately leaves the target keyring empty.
cat > /tmp/frog-keyring-pacman.conf <<'EOF'
[options]
SigLevel = Never
EOF
pacman --config /tmp/frog-keyring-pacman.conf -U --noscriptlet --noconfirm \
    /root/cachyos-keyring.pkg.tar.zst
pacman-key --init
pacman-key --populate archlinux cachyos

# Hooks are staged outside active hook directories until pacstrap is done.
cp -a /root/frog-package-hooks/. /
rm -rf /root/frog-package-hooks /root/cachyos-keyring.pkg.tar.zst \
    /tmp/frog-keyring-pacman.conf

# Package transactions ran before the Archiso overlay was present, so make the
# live initramfs now using the profile's Archiso preset.
rm -f /boot/initramfs-linux-cachyos-fallback.img
/usr/bin/mkinitcpio -p linux-cachyos

# Catch preset or config drift here rather than as a kernel panic at boot. A
# non-archiso initramfs contains no paths matching 'archiso'. The `|| true`
# keeps a failing lsinitcpio from masking the real result under pipefail.
initramfs_listing="$(lsinitcpio /boot/initramfs-linux-cachyos.img 2>/dev/null || true)"
if ! grep -q 'archiso' <<< "$initramfs_listing"; then
    echo "ERROR: /boot/initramfs-linux-cachyos.img has no archiso hooks." >&2
    echo "It cannot mount the live squashfs and will panic on boot." >&2
    exit 1
fi
