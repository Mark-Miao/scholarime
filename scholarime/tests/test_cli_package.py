from __future__ import annotations

import subprocess
import sys
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class CliPackageTests(unittest.TestCase):
    def test_module_help_and_version(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "scholarime", "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in ("workspace", "stats", "manuscript", "symbolic", "chart"):
            self.assertIn(command, result.stdout)
        version = subprocess.run(
            [sys.executable, "-m", "scholarime", "--version"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(version.returncode, 0, version.stderr)
        self.assertIn("Scholarime", version.stdout)
        search_help = subprocess.run(
            [sys.executable, "-m", "scholarime", "search", "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(search_help.returncode, 0, search_help.stderr)
        self.assertIn("academic_search.py", search_help.stdout)

    def test_pyproject_defines_entrypoint_and_extras(self) -> None:
        payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(payload["project"]["scripts"]["scholarime"], "scholarime.cli:main")
        self.assertEqual(payload["project"]["license"]["file"], "LICENSE")
        self.assertEqual(payload["project"]["readme"], "README.md")
        extras = payload["project"]["optional-dependencies"]
        for name in ("workspace", "stats", "writing", "all"):
            self.assertIn(name, extras)

    def test_noncommercial_license_notice_is_present(self) -> None:
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("SPDX-License-Identifier: CC-BY-NC-SA-4.0", license_text)
        self.assertIn("Commercial use is prohibited", readme)


if __name__ == "__main__":
    unittest.main()
