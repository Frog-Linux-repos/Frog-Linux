import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "archiso/airootfs"
MODULE = ROOT / "usr/lib/calamares/modules/froglimine/main.py"


class Storage:
    def __init__(self, values):
        self.values = values

    def value(self, key):
        return self.values.get(key)

    def insert(self, key, value):
        self.values[key] = value


class BootloaderTests(unittest.TestCase):
    def load_module(self, values, calls):
        storage = Storage(values)
        calamares = types.SimpleNamespace(
            globalstorage=storage,
            utils=types.SimpleNamespace(target_env_call=lambda command: calls.append(command) or 0),
        )
        spec = importlib.util.spec_from_file_location("froglimine", MODULE)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"libcalamares": calamares}):
            spec.loader.exec_module(module)
        return module, storage

    def test_uefi_installs_limine_with_frog_name_and_root_parameters(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "etc").mkdir()
            calls = []
            esp_path = target / "boot/efi"
            esp_path.mkdir(parents=True)

            def target_call(command):
                calls.append(command)
                if command == ["limine-update"]:
                    (esp_path / "limine.conf").write_text(
                        "timeout: 5\ndefault_entry: Frog Linux/linux-cachyos\n"
                        "/Frog Linux\n  //linux-cachyos\n"
                    )
                return 0

            module, storage = self.load_module({
                "firmwareType": "efi",
                "rootMountPoint": directory,
                "efiSystemPartition": "/boot/efi",
                "partitions": [
                    {"mountPoint": "/", "fs": "btrfs", "uuid": "root-uuid",
                     "luksUuid": "luks-uuid", "luksMapperName": "cryptroot"},
                    {"mountPoint": "/boot/efi", "fs": "fat32", "uuid": "esp-uuid"},
                    {"mountPoint": "swap", "fs": "linuxswap", "uuid": "swap-uuid",
                     "claimed": True},
                ],
                "btrfsRootSubvolume": "@",
            }, calls)
            module.libcalamares.utils.target_env_call = target_call
            module.LIMINE_SHARE = ROOT / "usr/share/frog/limine"

            def copy_limine(args, check):
                destination = Path(args[-1])
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("limine")

            with patch.object(module.subprocess, "check_output", side_effect=(
                "/dev/nvme0n1p1\n", "nvme0n1\n", "1\n"
            )), patch.object(module.subprocess, "run", side_effect=copy_limine):
                self.assertIsNone(module.run())

            self.assertEqual("none", storage.value("frogBootloader"))
            self.assertEqual("/boot/efi\n", (target / "etc/frog-limine-esp").read_text())
            self.assertEqual(
                "quiet rw rd.luks.name=luks-uuid=cryptroot "
                "root=/dev/mapper/cryptroot rootflags=subvol=@\n",
                (target / "etc/kernel/cmdline").read_text(),
            )
            self.assertNotIn("resume=", (target / "etc/kernel/cmdline").read_text())
            self.assertEqual(
                'ESP_PATH="/boot/efi"\nTARGET_OS_NAME="Frog Linux"\n'
                'ENABLE_VERIFICATION=no\nBOOT_ORDER="*, *fallback, Snapshots"\n'
                'FIND_BOOTLOADERS=no\n',
                (target / "etc/default/limine").read_text(),
            )
            self.assertIn(["limine-update"], calls)
            self.assertIn(["limine-snapper-sync"], calls)
            self.assertIn(["systemctl", "enable", "limine-snapper-sync.service"], calls)
            limine_config = (esp_path / "limine.conf").read_text()
            self.assertIn("default_entry: Frog Linux/linux-cachyos", limine_config)
            self.assertIn("//Snapshots", limine_config)
            self.assertIn(
                "term_palette: 24273a;ed8796;a6da95;eed49f;8aadf4;f5bde6;8bd5ca;cad3f5",
                limine_config,
            )
            self.assertIn("interface_branding_color: a6da95", limine_config)
            self.assertIn("interface_branding: Frog Linux", limine_config)
            self.assertNotIn("Frog Linux Linux", limine_config)
            self.assertLess(limine_config.index("term_palette:"),
                            limine_config.index("/Frog Linux"))
            self.assertEqual(1, limine_config.count("term_palette:"))
            self.assertTrue((target / "usr/share/licenses/catppuccin-limine/LICENSE").is_file())
            self.assertIn(
                "catppuccin/limine",
                (target / "usr/share/licenses/catppuccin-limine/SOURCE").read_text(),
            )
            self.assertIn(["efibootmgr", "--create", "--disk", "/dev/nvme0n1",
                           "--part", "1", "--label", "Frog Linux", "--loader",
                           "\\EFI\\limine\\limine_x64.efi"], calls)
            self.assertEqual(["pacman", "-R", "--noconfirm", "grub"], calls[-1])

    def test_bios_keeps_grub(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            module, storage = self.load_module({
                "firmwareType": "bios", "rootMountPoint": directory,
            }, calls)
            self.assertIsNone(module.run())
            self.assertEqual("grub", storage.value("frogBootloader"))
            self.assertEqual([[
                "pacman", "-R", "--noconfirm", "limine-mkinitcpio-hook", "limine-snapper-sync",
            ]], calls)

    def test_theme_replaces_old_global_colors_and_preserves_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "limine.conf"
            config.write_text(
                "timeout: 5\n"
                "term_palette: 000000;111111;222222;333333;444444;555555;666666;777777\n"
                "interface_branding: Frog Linux Linux\n"
                "/Frog Linux\n  protocol: limine\n  path: boot():/vmlinuz\n"
            )
            module, _ = self.load_module({}, [])
            module.LIMINE_SHARE = ROOT / "usr/share/frog/limine"

            module.apply_theme(config)
            module.apply_theme(config)

            result = config.read_text()
            self.assertEqual(1, result.count("term_palette:"))
            self.assertEqual(1, result.count("interface_branding: Frog Linux\n"))
            self.assertNotIn("Frog Linux Linux", result)
            self.assertIn("/Frog Linux\n  protocol: limine\n  path: boot():/vmlinuz", result)

    def test_uefi_does_not_remove_grub_package_if_limine_files_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "etc").mkdir()
            (target / "boot/efi").mkdir(parents=True)
            calls = []
            module, _ = self.load_module({
                "firmwareType": "efi",
                "rootMountPoint": directory,
                "efiSystemPartition": "/boot/efi",
                "partitions": [
                    {"mountPoint": "/", "fs": "ext4", "uuid": "root-uuid"},
                    {"mountPoint": "/boot/efi", "fs": "fat32", "uuid": "esp-uuid"},
                ],
            }, calls)
            module.libcalamares.utils.target_env_call = (
                lambda command: calls.append(command) or (1 if command == ["limine-update"] else 0)
            )
            with patch.object(module.subprocess, "run"):
                self.assertEqual("Limine installation failed", module.run()[0])
            self.assertNotIn(["pacman", "-R", "--noconfirm", "grub"], calls)

    def test_bootloader_sequence_and_branding(self):
        settings = (ROOT / "etc/calamares/settings.conf").read_text()
        branding = (ROOT / "etc/calamares/branding/frog/branding.desc").read_text()
        bootloader = (ROOT / "etc/calamares/modules/bootloader.conf").read_text()
        packages = (REPO / "archiso/packages.x86_64").read_text().splitlines()

        self.assertLess(settings.index("  - initcpio\n"), settings.index("  - froglimine\n"))
        self.assertLess(settings.index("  - froglimine\n"), settings.index("  - bootloader\n"))
        self.assertIn("efiBootLoaderVar: frogBootloader", bootloader)
        self.assertIn("bootloaderEntryName: Frog\n", branding)
        self.assertIn("limine", packages)
        self.assertIn("limine-mkinitcpio-hook", packages)
        self.assertIn("limine-snapper-sync", packages)
        self.assertIn("snapper", packages)
        self.assertIn("snap-pac", packages)

    def test_calamares_discovers_froglimine_and_uses_btrfs_subvolumes(self):
        settings = (ROOT / "etc/calamares/settings.conf").read_text()
        module_desc = (ROOT / "usr/lib/calamares/modules/froglimine/module.desc").read_text()
        partition = (ROOT / "etc/calamares/modules/partition.conf").read_text()
        mount = (ROOT / "etc/calamares/modules/mount.conf").read_text()

        self.assertIn("- froglimine\n", settings)
        self.assertIn("shellprocess@btrfs-snapshots", settings)
        self.assertLess(settings.index("  - shellprocess@btrfs-snapshots\n"),
                        settings.index("  - initcpio\n"))
        self.assertIn("name: froglimine", module_desc)
        self.assertIn('defaultFileSystemType: "btrfs"', partition)
        self.assertIn("recommendedSize: 4096MiB", partition)
        self.assertIn("mountPoint: /sys", mount)
        for mountpoint, subvolume in (
            ("/", "/@"), ("/home", "/@home"),
            ("/var/cache", "/@cache"), ("/var/log", "/@log"),
        ):
            self.assertIn(f'mountPoint: {mountpoint}', mount)
            self.assertIn(f'subvolume: {subvolume}', mount)

    def test_snapshot_helper_avoids_dbus_for_target_chroot_commands(self):
        helper = (ROOT / "usr/local/bin/frog-configure-snapshots").read_text()

        self.assertIn("snapper --no-dbus list-configs", helper)
        self.assertIn("snapper --no-dbus -c root create-config /", helper)
        self.assertIn("snapper --no-dbus -c root set-config", helper)


if __name__ == "__main__":
    unittest.main()
