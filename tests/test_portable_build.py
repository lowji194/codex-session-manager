"""Packaging guard tests: no app imports, account access, build or GUI launch."""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_portable.py"
SPEC = importlib.util.spec_from_file_location("portable_build", MODULE_PATH)
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


class PortableBuildTests(unittest.TestCase):
    def test_dry_run_leaves_output_uncreated(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "not-created"
            result = build.build(Namespace(
                version="test-1", output_dir=output, webview2_runtime=None, dry_run=True,
            ))
            self.assertTrue(result["dry_run"])
            self.assertFalse(result["launch_app"])
            self.assertFalse(result["user_data_included"])
            self.assertEqual(len(result["web_assets"]), 3)
            self.assertFalse(output.exists())

    def test_package_rejects_private_config_and_backups(self):
        for path in ("config.local.json", "tool/backups/record.json", "AUTH.JSON", "backups/account.enc"):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("synthetic test input", encoding="utf-8")
                with self.assertRaises(ValueError):
                    build.package_files(root)

    def test_fixed_runtime_requires_executable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            args = Namespace(version="test", output_dir=root / "output", webview2_runtime=root, dry_run=True)
            with self.assertRaises(ValueError):
                build.build(args)
            (root / "msedgewebview2.exe").write_bytes(b"synthetic validation-only fixture")
            self.assertEqual(build.build(args)["bundled_webview2"], str(root.resolve()))
            self.assertFalse((root / "output").exists())

    def test_existing_archive_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            args = Namespace(version="test", output_dir=root, webview2_runtime=None, dry_run=True)
            archive = Path(build.build(args)["archive"])
            archive.write_bytes(b"original")
            with self.assertRaises(FileExistsError):
                build.build(args)
            self.assertEqual(archive.read_bytes(), b"original")

    def test_version_label_rejects_path_or_shell_syntax(self):
        for value in ("../outside", "test;command", "$(echo)"):
            with self.subTest(value=value), self.assertRaises(SystemExit):
                build.parse_args(["--version", value])


if __name__ == "__main__":
    unittest.main()
