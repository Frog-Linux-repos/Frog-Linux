#!/usr/bin/env bash
# Strips live-ISO state from a freshly installed target. Wired in as
# shellprocess@cleanup, directly after unpackfs.
#
# unpackfs copies the whole live root filesystem to disk, so without this
# everything that makes the ISO a live system persists on the installed one.
#
# Position in the sequence is load-bearing. It needs a populated target, so it
# cannot run before unpackfs. It has to beat initcpio, which would otherwise
# build an archiso initramfs, and users, which runs useradd -m and copies
# /etc/skel.
set -euo pipefail

# Put a kernel back in /boot.
# mkarchiso empties /boot (_cleanup_pacstrap_dir) after copying the kernel out
# for the ISO but before building the squashfs, so the target arrives with no
# kernel at all. Only the copy under /usr survives.
for vmlinuz in /usr/lib/modules/*/vmlinuz; do
    [ -e "$vmlinuz" ] || continue
    install -Dm644 "$vmlinuz" /boot/vmlinuz-linux-cachyos
    break
done
if [ ! -e /boot/vmlinuz-linux-cachyos ]; then
    echo "ERROR: no kernel under /usr/lib/modules/*/vmlinuz, nothing to boot." >&2
    exit 1
fi

# Point mkinitcpio at a normal config instead of the archiso one.
# Removing archiso.conf without replacing the preset leaves archiso_config=
# dangling and mkinitcpio fails outright, so both have to change together.
rm -f /etc/mkinitcpio.conf.d/archiso.conf
cat > /etc/mkinitcpio.d/linux-cachyos.preset <<'EOF'
ALL_config="/etc/mkinitcpio.conf"
ALL_kver="/boot/vmlinuz-linux-cachyos"

PRESETS=('default' 'fallback')

default_image="/boot/initramfs-linux-cachyos.img"

fallback_image="/boot/initramfs-linux-cachyos-fallback.img"
fallback_options="-S autodetect"
EOF
rm -f /boot/initramfs-linux-cachyos.img

# Give pacman a working keyring.
# mkarchiso pacstraps with -G and releng keeps the live keyring on a tmpfs, so
# /etc/pacman.d/gnupg in the squashfs is empty and nothing verifies.
rm -f /etc/systemd/system/etc-pacman.d-gnupg.mount
rm -f /etc/systemd/system/multi-user.target.wants/pacman-init.service
rm -f /etc/systemd/system/pacman-init.service
pacman-key --init
pacman-key --populate archlinux cachyos

# The ISO pins webkit2gtk to keep the Tauri compat layer valid. An installed
# system needs to be able to take security updates for it.
sed -i '/^IgnorePkg = webkit2gtk/d' /etc/pacman.conf

# The host-side prepare-network step replaces releng's /run symlink with the
# live environment's resolver so package installs work in this chroot.
if ! grep -Eq '^[[:space:]]*nameserver[[:space:]]+' /etc/resolv.conf; then
    echo "ERROR: target has no working DNS resolver." >&2
    exit 1
fi

# Credentials and autologin that only make sense on a live image.
rm -f  /etc/sudoers.d/g_wheel
rm -f /home/liveuser/.config/kwalletrc
usermod --lock liveuser
gpasswd --delete liveuser wheel
rm -rf /etc/systemd/system/getty@tty1.service.d
rm -f /etc/plasmalogin.conf.d/10-frog-live.conf
rm -f  /etc/ssh/sshd_config.d/10-archiso.conf
rm -f  /root/.automated_script.sh /root/.zlogin
rm -f /etc/systemd/system/display-manager.service

# Limine's packaged mkinitcpio wrapper prompts during Calamares, and its
# package hook cannot write boot entries until the installed ESP is configured.
if [ -f /etc/pacman.d/hooks/90-mkinitcpio-install.hook ]; then
    mv /etc/pacman.d/hooks/90-mkinitcpio-install.hook \
       /etc/pacman.d/hooks/90-mkinitcpio-install.hook.frog-disabled
fi
if [ -f /usr/local/bin/mkinitcpio ]; then
    mv /usr/local/bin/mkinitcpio /usr/local/bin/mkinitcpio.frog-disabled
fi

# A volatile journal and inhibited suspend are right for a live image and wrong
# once installed.
rm -f /etc/systemd/journald.conf.d/volatile-storage.conf
rm -f /etc/systemd/logind.conf.d/do-not-suspend.conf
rm -f /etc/systemd/system-generators/systemd-gpt-auto-generator
rm -f /etc/motd
rm -f /etc/skel/Desktop/install-frog-linux.desktop
rm -f /etc/skel/.config/autostart/frog-init.desktop
rm -f /etc/pacman.d/hooks/91-frog-tauri-compat.hook
rm -f /usr/local/bin/choose-mirror /usr/local/bin/Installation_guide \
      /usr/local/bin/livecd-sound /usr/local/bin/frog-init.sh \
      /usr/local/bin/frog-patch-tauri-desktop.sh
rm -f /etc/systemd/scripts/choose-mirror

# Unwind releng's service set.
# It enables systemd-networkd and iwd; Frog ships NetworkManager. Both running
# means two daemons claiming the same interfaces, plus networkd-wait-online
# holding network-online.target open until it times out.
#
# systemd-resolved stays. NetworkManager integrates with it.
WANTS=/etc/systemd/system
rm -f "$WANTS/multi-user.target.wants/systemd-networkd.service"
rm -f "$WANTS/sockets.target.wants/systemd-networkd.socket"
rm -rf "$WANTS/network-online.target.wants"
rm -f "$WANTS/multi-user.target.wants/iwd.service"
rm -f "$WANTS/multi-user.target.wants/sshd.service"
rm -f "$WANTS/multi-user.target.wants/ModemManager.service"
rm -f "$WANTS/multi-user.target.wants/hv_fcopy_daemon.service"
rm -f "$WANTS/multi-user.target.wants/hv_kvp_daemon.service"
rm -f "$WANTS/multi-user.target.wants/hv_vss_daemon.service"
rm -f "$WANTS/multi-user.target.wants/choose-mirror.service"
rm -f "$WANTS/choose-mirror.service"
rm -f "$WANTS/sockets.target.wants/pcscd.socket"
# Both of these lose their binaries above, and espeakup was never installed.
rm -f "$WANTS/multi-user.target.wants/livecd-talk.service"
rm -f "$WANTS/livecd-talk.service"
rm -f "$WANTS/sound.target.wants/livecd-alsa-unmuter.service"
rm -f "$WANTS/livecd-alsa-unmuter.service"
# VM guest agents stay enabled. They carry ConditionVirtualization, so they are
# inert on bare metal.

# Fallback only. Calamares' users module overwrites this with what the installer
# typed.
echo 'frog' > /etc/hostname

rm -f /usr/local/bin/frog-postinstall-cleanup.sh
