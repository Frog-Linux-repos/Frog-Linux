#!/usr/bin/env bash
set -euo pipefail

if [ ! -f /var/local-repo/frog-local.db.tar.zst ]; then
  echo "ERROR: /var/local-repo is empty, so this is not the toolchain image." >&2
  echo "Build it first: docker build -t frog-linux-build docker/" >&2
  echo "Or just run scripts/build-local.sh, which does both steps." >&2
  exit 1
fi

echo "==> [1/2] Set up archiso profile"

rm -rf ./frog-profile ./work
cp -r /usr/share/archiso/configs/releng/ ./frog-profile

sed -i 's/^\s*DownloadUser\s*=/#&/' ./frog-profile/pacman.conf || true

cat >> ./frog-profile/pacman.conf <<'EOF'

[frog-local]
SigLevel = Optional TrustAll
Server = file:///var/local-repo

[cachyos]
SigLevel = Required DatabaseOptional
Server = https://mirror.cachyos.org/repo/$arch/$repo
EOF

# These hooks target installed systems and must not run while pacstrap builds
# the live root, which has no ESP or Snapper configuration yet.
sed -i '/^\[options\]/a NoExtract = usr/share/libalpm/hooks/*limine*.hook\nNoExtract = usr/share/libalpm/hooks/*snap-pac*.hook\nNoExtract = etc/pacman.d/hooks/90-mkinitcpio-install.hook\nNoExtract = usr/local/bin/mkinitcpio' \
  ./frog-profile/pacman.conf

find ./frog-profile/syslinux ./frog-profile/grub ./frog-profile/efiboot \
     -type f \( -name '*.cfg' -o -name '*.conf' \) -print0 |
  xargs -0r sed -i \
    -e 's|vmlinuz-linux |vmlinuz-linux-cachyos |g' \
    -e 's|vmlinuz-linux$|vmlinuz-linux-cachyos|g' \
    -e 's|initramfs-linux\.img|initramfs-linux-cachyos.img|g' \
    -e '/archisobasedir=/ s|archisobasedir=|cow_spacesize=2G archisobasedir=|'

PRESET_DIR=./frog-profile/airootfs/etc/mkinitcpio.d
mkdir -p "$PRESET_DIR"
rm -f "$PRESET_DIR/linux.preset"

cat > "$PRESET_DIR/linux-cachyos.preset" <<'EOF'
PRESETS=('archiso')
ALL_kver="/boot/vmlinuz-linux-cachyos"
archiso_config="/etc/mkinitcpio.conf.d/archiso.conf"
archiso_image="/boot/initramfs-linux-cachyos.img"
EOF

cat >> ./frog-profile/profiledef.sh <<'EOF'

airootfs_image_tool_options=('-comp' 'zstd' '-Xcompression-level' '15' '-b' '1M')

iso_name="frog"
iso_label="FROG_$(date --date="@${SOURCE_DATE_EPOCH:-$(date +%s)}" +%Y%m)"
iso_publisher="Frog Linux <https://github.com/Crazy10061/Frog-Linux>"
iso_application="Frog Linux Live/Install CD"

file_permissions+=(
  ["/usr/local/bin/frog-init.sh"]="0:0:755"
  ["/usr/local/bin/frog-install-desktop.sh"]="0:0:755"
  ["/usr/local/bin/frog-install-browser.sh"]="0:0:755"
  ["/usr/local/bin/frog-configure-snapshots"]="0:0:755"
  ["/usr/local/bin/frog-patch-tauri-desktop.sh"]="0:0:755"
  ["/usr/local/bin/frog-postinstall-cleanup.sh"]="0:0:755"
  ["/etc/skel/Desktop/install-frog-linux.desktop"]="0:0:755"
  ["/etc/sudoers.d/g_wheel"]="0:0:440"
  ["/etc/passwd"]="0:0:644"
)
EOF

cp archiso/packages.x86_64 ./frog-profile/packages.x86_64
sed -i 's/\r$//' ./frog-profile/packages.x86_64

if [ -d "archiso/airootfs" ]; then
  cp -r archiso/airootfs/. ./frog-profile/airootfs/
fi

AIROOTFS=./frog-profile/airootfs

cp ./frog-profile/pacman.conf "$AIROOTFS/etc/pacman.conf"

sed -i '/^NoExtract = .*\(limine\|snap-pac\|90-mkinitcpio-install\|usr\/local\/bin\/mkinitcpio\)/d' \
  "$AIROOTFS/etc/pacman.conf"

sed -i '/^\[frog-local\]/,/^$/d' \
  "$AIROOTFS/etc/pacman.conf"

sed -i '/^\[options\]/a IgnorePkg = webkit2gtk-4.1 webkit2gtk' \
  "$AIROOTFS/etc/pacman.conf"

if [ ! -s "$AIROOTFS/etc/pacman.d/mirrorlist" ]; then
  if [ -s /etc/pacman.d/mirrorlist ]; then
    cp /etc/pacman.d/mirrorlist \
      "$AIROOTFS/etc/pacman.d/mirrorlist"
  else
    echo "ERROR: No Arch mirrorlist available." >&2
    exit 1
  fi
fi

HOOK_STAGE="$AIROOTFS/root/frog-package-hooks"
mkdir -p "$HOOK_STAGE"
pacman -Syw --noconfirm --config ./frog-profile/pacman.conf \
  --cachedir /var/cache/pacman/pkg \
  cachyos-keyring limine-mkinitcpio-hook limine-snapper-sync snap-pac

for package in cachyos-keyring limine-mkinitcpio-hook limine-snapper-sync snap-pac; do
  version="$(pacman -Si --config ./frog-profile/pacman.conf "$package" |
    sed -n 's/^Version[[:space:]]*:[[:space:]]*//p' | head -n1)"
  archive="$(find /var/cache/pacman/pkg -maxdepth 1 -type f \
    -name "${package}-${version}-*.pkg.tar.*" ! -name '*.sig' -print -quit)"
  if [ -z "$archive" ]; then
    echo "ERROR: downloaded package archive missing for $package $version." >&2
    exit 1
  fi

  if [ "$package" = cachyos-keyring ]; then
    install -m 644 "$archive" "$AIROOTFS/root/cachyos-keyring.pkg.tar.zst"
    continue
  fi

  mapfile -t hooks < <(tar --zstd -tf "$archive" |
    grep -E '^(etc/pacman.d/hooks|usr/share/libalpm/hooks)/[^/]+\.hook$|^usr/local/bin/mkinitcpio$' || true)
  if [ "${#hooks[@]}" -eq 0 ]; then
    echo "ERROR: no pacman hooks found in $package $version." >&2
    exit 1
  fi
  tar --zstd -xf "$archive" -C "$HOOK_STAGE" "${hooks[@]}"
done

echo "==> Build-time pacman repositories:"
grep -E '^\[[^]]+\]' ./frog-profile/pacman.conf

for repo in core extra frog-local cachyos; do
  if ! grep -qx "\[$repo\]" ./frog-profile/pacman.conf; then
    echo "ERROR: Required build repository [$repo] missing from pacman.conf." >&2
    exit 1
  fi
done

echo "==> Live-system pacman repositories:"
grep -E '^\[[^]]+\]' "$AIROOTFS/etc/pacman.conf"

for repo in core extra cachyos; do
  if ! grep -qx "\[$repo\]" "$AIROOTFS/etc/pacman.conf"; then
    echo "ERROR: Required repository [$repo] missing from live pacman.conf." >&2
    exit 1
  fi
done

if grep -qx '\[frog-local\]' "$AIROOTFS/etc/pacman.conf"; then
  echo "ERROR: Build-only [frog-local] repository leaked into live pacman.conf." >&2
  exit 1
fi

if [ ! -s "$AIROOTFS/etc/pacman.d/mirrorlist" ]; then
  echo "ERROR: Arch mirrorlist is missing or empty." >&2
  exit 1
fi

mkdir -p "$AIROOTFS/etc/flatpak/remotes.d"

curl -fsSL https://flathub.org/repo/flathub.flatpakrepo \
  -o "$AIROOTFS/etc/flatpak/remotes.d/flathub.flatpakrepo"

ln -sf /usr/lib/systemd/system/graphical.target \
  "$AIROOTFS/etc/systemd/system/default.target"

WANTS_MU="$AIROOTFS/etc/systemd/system/multi-user.target.wants"

mkdir -p "$WANTS_MU"

rm -f "$WANTS_MU"/{sshd,iwd,ModemManager,livecd-talk}.service
rm -f "$WANTS_MU"/hv_{fcopy,kvp,vss}_daemon.service
rm -f "$AIROOTFS/etc/systemd/system/sockets.target.wants/pcscd.socket"
rm -f "$WANTS_MU/systemd-networkd.service"
rm -f "$AIROOTFS/etc/systemd/system/sockets.target.wants/systemd-networkd.socket"
rm -rf "$AIROOTFS/etc/systemd/system/network-online.target.wants"

ln -sf /usr/lib/systemd/system/NetworkManager.service \
  "$WANTS_MU/NetworkManager.service"

rm -f "$AIROOTFS/etc/systemd/system/display-manager.service"
ln -sf /usr/lib/systemd/system/plasmalogin.service \
  "$AIROOTFS/etc/systemd/system/display-manager.service"

ln -sf /usr/lib/systemd/system/ananicy-cpp.service \
  "$WANTS_MU/ananicy-cpp.service" || true

ln -sf /usr/lib/systemd/system/bluetooth.service \
  "$WANTS_MU/bluetooth.service" || true

ln -sf /usr/lib/systemd/system/cups.service \
  "$WANTS_MU/cups.service" || true

ln -sf /usr/lib/systemd/system/vboxservice.service \
  "$WANTS_MU/vboxservice.service" || true

ln -sf /usr/lib/systemd/system/qemu-guest-agent.service \
  "$WANTS_MU/qemu-guest-agent.service" || true

ln -sf /usr/lib/systemd/system/spice-vdagentd.service \
  "$WANTS_MU/spice-vdagentd.service" || true

ln -sf /usr/lib/systemd/system/vmtoolsd.service \
  "$WANTS_MU/vmtoolsd.service" || true

echo "==> [2/2] Build ISO with mkarchiso"

WORK=/var/tmp/frog-work

rm -rf "$WORK"
mkdir -p "$WORK" ./output

mkarchiso \
  -v \
  -w "$WORK" \
  -o "$(pwd)/output" \
  ./frog-profile

echo
echo "==> Done. ISO(s) in ./output/:"
ls -lh ./output/
