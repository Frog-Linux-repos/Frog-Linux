import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "archiso/airootfs"
HELPER = ROOT / "usr/local/bin/frog-install-desktop.sh"
MODULES = ROOT / "etc/calamares/modules"


class DesktopTests(unittest.TestCase):
    def run_installer(self, desktop, failure=""):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            bin_dir = target / "bin"
            bin_dir.mkdir()
            log = target / "calls"
            for command in ("pacman", "systemctl", "cosmic-settings"):
                stub = bin_dir / command
                stub.write_text(
                    '#!/bin/bash\n'
                    'printf "%s %s\\n" "${0##*/}" "$*" >> "$CALL_LOG"\n'
                    'case "$FAILURE:${0##*/}:$1" in\n'
                    '  install:pacman:-Syu|remove:pacman:-R|theme:cosmic-settings:appearance) exit 1;;\n'
                    'esac\n'
                )
                stub.chmod(0o755)
            assets = target / "usr/local/share/frog-desktops"
            shutil.copytree(ROOT / "usr/local/share/frog-desktops", assets)
            wallpaper = target / "etc/skel/Wallpapers/frog.png"
            wallpaper.parent.mkdir(parents=True)
            wallpaper.write_text("wallpaper fixture")
            script = target / "installer.sh"
            script.write_text(
                HELPER.read_text().replace("/etc/", f"{target}/etc/")
                .replace("/usr/local/", f"{target}/usr/local/")
            )
            result = subprocess.run(
                ["bash", str(script), desktop], text=True, capture_output=True,
                env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
                     "CALL_LOG": str(log), "FAILURE": failure},
            )
            files = {
                str(path.relative_to(target)): path.read_text()
                for path in (target / "etc").rglob("*") if path.is_file()
            }
            return result, log.read_text().splitlines() if log.exists() else [], files

    def test_kde_preserves_packages_and_uses_plasma_login(self):
        result, calls, files = self.run_installer("kde")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(call.startswith("pacman") for call in calls))
        self.assertIn("systemctl enable --force plasmalogin.service", calls)
        self.assertIn("default=kde", files["etc/xdg/xdg-desktop-portal/kde-portals.conf"])

    def test_cosmic_installs_before_removing_kde_and_uses_cosmic_greeter(self):
        result, calls, files = self.run_installer("cosmic")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(calls[0].startswith("pacman -Syu --noconfirm --needed cosmic "))
        removal = next(call for call in calls if call.startswith("pacman -R "))
        self.assertIn("plasma-meta", removal)
        self.assertNotIn("pipewire", removal)
        self.assertIn("cosmic-greeter", calls[0].split())
        self.assertNotIn("greetd-regreet", calls[0].split())
        self.assertNotIn("cage", calls[0].split())
        self.assertLess(calls.index(removal), calls.index(
            "systemctl enable --force cosmic-greeter.service"))
        self.assertIn("default=cosmic;gtk", files["etc/xdg/xdg-desktop-portal/cosmic-portals.conf"])
        self.assertNotIn("etc/greetd/config.toml", files)
        self.assertNotIn("etc/greetd/regreet.toml", files)

    def test_failures_do_not_enable_a_replacement(self):
        for failure in ("install", "remove", "theme"):
            with self.subTest(failure=failure):
                result, calls, _ = self.run_installer("cosmic", failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(call.startswith("systemctl") for call in calls))
                if failure == "install":
                    self.assertEqual(len(calls), 1)
                if failure == "theme":
                    self.assertFalse(any(call.startswith("pacman -R") for call in calls))

    def test_unknown_profile_is_rejected_without_changes(self):
        result, calls, files = self.run_installer("unknown")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(calls, [])
        self.assertEqual(files, {"etc/skel/Wallpapers/frog.png": "wallpaper fixture"})

    def test_chooser_only_offers_shipped_desktops(self):
        chooser = (MODULES / "packagechooser_desktop.conf").read_text()
        contextual = (MODULES / "contextualprocess_desktop.conf").read_text()
        self.assertIn("default: kde", chooser)
        for desktop in ("kde", "cosmic"):
            self.assertIn(f"id: {desktop}", chooser)
            self.assertIn(f"frog-install-desktop.sh {desktop}", contextual)
        settings = (ROOT / "etc/calamares/settings.conf").read_text()
        self.assertIn("packagechooser@desktop", settings)


if __name__ == "__main__":
    unittest.main()
