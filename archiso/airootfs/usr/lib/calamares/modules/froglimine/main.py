import re
import subprocess
from pathlib import Path

import libcalamares

LIMINE_SHARE = Path("/usr/share/frog/limine")


def apply_theme(config):
    theme_lines = (LIMINE_SHARE / "catppuccin-macchiato-green.conf").read_text().splitlines()
    theme_keys = {line.split(":", 1)[0] for line in theme_lines}
    lines = config.read_text().splitlines()
    first_entry = next((i for i, line in enumerate(lines)
                        if line.startswith("/") and not line.startswith("//")), len(lines))
    header = [line for line in lines[:first_entry]
              if line.split(":", 1)[0] not in theme_keys
              and line.split(":", 1)[0] not in ("interface_branding", "interface_branding_colour")]
    config.write_text(
        "\n".join(theme_lines + ["interface_branding: Frog Linux"]
                  + header + lines[first_entry:]) + "\n"
    )


def pretty_name():
    return "Install Limine"


def run():
    storage = libcalamares.globalstorage
    if storage.value("firmwareType") != "efi":
        storage.insert("frogBootloader", "grub")
        if libcalamares.utils.target_env_call([
            "pacman", "-R", "--noconfirm", "limine-mkinitcpio-hook", "limine-snapper-sync",
        ]) != 0:
            return "Limine setup failed", "Could not remove the unused UEFI Limine integration packages."
        for path in (
            "/etc/pacman.d/hooks/90-mkinitcpio-install.hook.frog-disabled",
            "/usr/local/bin/mkinitcpio.frog-disabled",
        ):
            (Path(storage.value("rootMountPoint")) / path.lstrip("/")).unlink(missing_ok=True)
        return None

    storage.insert("frogBootloader", "none")
    partitions = storage.value("partitions") or []
    esp = storage.value("efiSystemPartition")
    root = next((p for p in partitions if p["mountPoint"] == "/"), None)
    if not root or not root.get("uuid"):
        return "Limine installation failed", "The root partition has no UUID."
    if not esp or not esp.startswith("/"):
        return "Limine installation failed", "The EFI system partition has no mount point."
    efi_partition = next((p for p in partitions if p["mountPoint"] == esp), None)
    if not efi_partition:
        return "Limine installation failed", "The EFI system partition is not mounted."
    if efi_partition["fs"] not in ("fat16", "fat32", "vfat"):
        return "Limine installation failed", "Limine needs a FAT EFI system partition."
    if root["fs"] == "zfs":
        return "Limine installation failed", "ZFS root is not supported by this boot configuration."

    target = Path(storage.value("rootMountPoint"))
    params = ["quiet", "rw"]
    systemd_hooks = libcalamares.utils.target_env_call(
        ["grep", "-q", "^HOOKS.*systemd", "/etc/mkinitcpio.conf"]
    ) == 0

    if "luksMapperName" in root:
        if systemd_hooks:
            params.append(f"rd.luks.name={root['luksUuid']}={root['luksMapperName']}")
        else:
            params.append(f"cryptdevice=UUID={root['luksUuid']}:{root['luksMapperName']}")
        params.append(f"root=/dev/mapper/{root['luksMapperName']}")
    else:
        params.append(f"root=UUID={root['uuid']}")

    if root["fs"] == "btrfs":
        subvolume = storage.value("btrfsRootSubvolume")
        if subvolume:
            params.append(f"rootflags=subvol={subvolume}")

    (target / "etc/kernel").mkdir(parents=True, exist_ok=True)
    (target / "etc/kernel/cmdline").write_text(" ".join(params) + "\n")
    (target / "etc/frog-limine-esp").write_text(esp + "\n")
    (target / "etc/default").mkdir(parents=True, exist_ok=True)
    (target / "etc/default/limine").write_text(
        f'ESP_PATH="{esp}"\n'
        'TARGET_OS_NAME="Frog Linux"\n'
        'ENABLE_VERIFICATION=no\n'
        'BOOT_ORDER="*, *fallback, Snapshots"\n'
        'FIND_BOOTLOADERS=no\n'
    )

    esp_path = target / esp.lstrip("/")
    try:
        source = subprocess.check_output(
            ["findmnt", "-n", "-o", "SOURCE", "--mountpoint", str(esp_path)],
            text=True,
        ).strip()
    except subprocess.CalledProcessError:
        return "Limine installation failed", "Could not find the mounted EFI partition."
    if not source.startswith("/dev/"):
        return "Limine installation failed", "Could not identify the EFI partition device."
    try:
        disk = subprocess.check_output(["lsblk", "-dn", "-o", "PKNAME", source], text=True).strip()
        number = subprocess.check_output(["lsblk", "-dn", "-o", "PARTN", source], text=True).strip()
    except subprocess.CalledProcessError:
        return "Limine installation failed", "Could not identify the EFI partition."
    if not disk or not number.isdigit():
        return "Limine installation failed", "Could not identify the EFI partition number."

    for path in ("EFI/limine/limine_x64.efi", "EFI/BOOT/BOOTX64.EFI"):
        try:
            subprocess.run(
                ["install", "-Dm644", "/usr/share/limine/BOOTX64.EFI", str(esp_path / path)],
                check=True,
            )
        except subprocess.CalledProcessError:
            return "Limine installation failed", "Could not copy Limine to the EFI partition."

    config = esp_path / "limine.conf"
    if not config.exists():
        config.write_text(
            "timeout: 5\n"
            "default_entry: Frog Linux/linux-cachyos\n"
            "interface_branding: Frog Linux\n"
            "interface_branding_colour: 6fa66b\n"
        )
    if libcalamares.utils.target_env_call(["limine-update"]) != 0:
        return "Limine installation failed", "Could not create Limine kernel entries."
    for original, disabled in (
        ("/etc/pacman.d/hooks/90-mkinitcpio-install.hook",
         "/etc/pacman.d/hooks/90-mkinitcpio-install.hook.frog-disabled"),
        ("/usr/local/bin/mkinitcpio", "/usr/local/bin/mkinitcpio.frog-disabled"),
    ):
        original_path = target / original.lstrip("/")
        disabled_path = target / disabled.lstrip("/")
        if disabled_path.exists():
            disabled_path.rename(original_path)
    if root["fs"] == "btrfs":
        lines = config.read_text().splitlines()
        start = next((i for i, line in enumerate(lines)
                      if re.fullmatch(r"/\+?Frog Linux", line.strip())), None)
        if start is None:
            return "Limine installation failed", "Could not find the Frog Linux Limine entry."
        end = next((i for i in range(start + 1, len(lines))
                    if lines[i].lstrip().startswith("/")
                    and not lines[i].lstrip().startswith("//")), len(lines))
        if not any(line.strip() == "//Snapshots" for line in lines[start + 1:end]):
            lines.insert(end, "  //Snapshots")
            config.write_text("\n".join(lines) + "\n")
        if libcalamares.utils.target_env_call(["limine-snapper-sync"]) != 0:
            return "Limine installation failed", "Could not initialize Limine snapshot entries."
        if libcalamares.utils.target_env_call(
            ["systemctl", "enable", "limine-snapper-sync.service"]
        ) != 0:
            return "Limine installation failed", "Could not enable Limine snapshot entries."
    apply_theme(config)
    license_dir = target / "usr/share/licenses/catppuccin-limine"
    license_dir.mkdir(parents=True, exist_ok=True)
    (license_dir / "LICENSE").write_text((LIMINE_SHARE / "LICENSE").read_text())
    (license_dir / "SOURCE").write_text(
        "Catppuccin Limine theme, Macchiato Green variant\n"
        "https://github.com/catppuccin/limine\n"
    )
    if libcalamares.utils.target_env_call([
        "efibootmgr", "--create", "--disk", f"/dev/{disk}", "--part", number,
        "--label", "Frog Linux", "--loader", "\\EFI\\limine\\limine_x64.efi",
    ]) != 0:
        return "Limine installation failed", "Could not add the firmware boot entry."
    if libcalamares.utils.target_env_call(["pacman", "-R", "--noconfirm", "grub"]) != 0:
        return "Limine installation failed", "Could not remove the unused GRUB package."
    return None
