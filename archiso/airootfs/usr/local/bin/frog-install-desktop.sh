#!/usr/bin/env bash
set -euo pipefail

desktop="${1:-}"
case "$desktop" in
    kde)
        manager=plasmalogin
        ;;
    cosmic)
        pacman -Syu --noconfirm --needed cosmic cosmic-greeter \
            xdg-desktop-portal-gtk gnome-keyring
        manager=cosmic-greeter
        ;;
    *)
        echo "ERROR: unsupported desktop: $desktop" >&2
        exit 2
        ;;
esac

if [ "$desktop" != kde ]; then
    rm -f /etc/skel/.config/kdeglobals
    install -Dm644 /etc/skel/Wallpapers/frog.png /usr/local/share/backgrounds/frog.png
    install -d /etc/skel/.config
    cp -a "/usr/local/share/frog-desktops/$desktop/." /etc/skel/.config/
fi

if [ "$desktop" = cosmic ]; then
    install -Dm644 /usr/local/share/frog-desktops/frog-cosmic.ron /usr/local/share/themes/frog-cosmic.ron
    XDG_CONFIG_HOME=/etc/skel/.config cosmic-settings appearance import /usr/local/share/themes/frog-cosmic.ron
fi

install -d /etc/xdg/xdg-desktop-portal
case "$desktop" in
    kde) portals='default=kde' ;;
    cosmic) portals='default=cosmic;gtk' ;;
esac
printf '[preferred]\n%s\n' "$portals" > "/etc/xdg/xdg-desktop-portal/${desktop}-portals.conf"

if [ "$desktop" != kde ]; then
    # Remove Frog's KDE session packages while leaving unrelated shared dependencies installed.
    kde_packages=(plasma-meta konsole dolphin kate ark gwenview spectacle
        xdg-desktop-portal-kde phonon-qt6-vlc plasma-login-manager
        plasma-desktop plasma-workspace kwin kdeplasma-addons
        plasma-browser-integration plasma-nm plasma-pa plasma-vault powerdevil)
    installed=()
    for package in "${kde_packages[@]}"; do
        if pacman -Qq "$package" >/dev/null 2>&1; then
            installed+=("$package")
        fi
    done
    if [ "${#installed[@]}" -gt 0 ]; then
        pacman -R --noconfirm "${installed[@]}"
    fi
fi

# Enable in the target without starting services inside Calamares' chroot.
systemctl enable --force "$manager.service"
systemctl set-default graphical.target
rm -rf /usr/local/share/frog-desktops
rm -f /usr/local/bin/frog-install-desktop.sh
