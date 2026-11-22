"""Unified command entry point for Scholarime."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import sys
from pathlib import Path
from typing import Sequence

from scholarime import __version__


SCRIPTS = Path(__file__).resolve().parent / "scripts"
COMMANDS = {
    "search": "academic_search.py",
    "review": "reference_review.py",
    "data": "scholarly_data.py",
    "stats": "scholarly_stats.py",
    "symbolic": "symbolic_derive.py",
    "chart": "scholarly_chart.py",
    "figure": "scholarly_figure.py",
    "screening": "scholarly_screening.py",
    "workspace": "research_workspace.py",
    "manuscript": "scholarly_manuscript.py",
    "verify": "scholarly_verify.py",
}


def _package_status(distribution: str, import_name: str) -> str:
    available = importlib.util.find_spec(import_name) is not None
    if not available:
        return "unavailable"
    try:
        version = importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        version = "installed"
    return version


def _usage() -> str:
    commands = ", ".join(sorted(COMMANDS))
    return (
        "usage: scholarime <command> [options]\n\n"
        f"commands: {commands}\n"
        "Run `scholarime <command> --help` for command-specific options."
    )


def _load_script(filename: str):
    path = SCRIPTS / filename
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    module_name = f"scholarime_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print(_usage())
        return 0
    if args[0] == "--version":
        print(f"Scholarime {__version__}")
        return 0
    command = args.pop(0)
    if command == "doctor":
        print(f"Scholarime {__version__}")
        print(f"Package: {Path(__file__).resolve().parent}")
        print(f"Python: {sys.version.split()[0]}")
        print(f"Entry point available: scholarime")
        for label, distribution, import_name in (
            ("Pillow", "Pillow", "PIL"),
            ("NumPy", "numpy", "numpy"),
            ("Matplotlib", "matplotlib", "matplotlib"),
            ("PyMuPDF", "PyMuPDF", "pymupdf"),
            ("SciPy", "scipy", "scipy"),
            ("Statsmodels", "statsmodels", "statsmodels"),
            ("pywebview", "pywebview", "webview"),
        ):
            print(f"{label}: {_package_status(distribution, import_name)}")
        return 0
    filename = COMMANDS.get(command)
    if filename is None:
        print(f"error: unknown command {command!r}\n\n{_usage()}", file=sys.stderr)
        return 2
    if command == "search" and args and args[0] not in {"search", "cleanup", "-h", "--help"}:
        args.insert(0, "search")
    module = _load_script(filename)
    return int(module.main(args))
