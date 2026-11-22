#!/usr/bin/env python3
"""Run Scholarime's publication-figure templates and quality audits."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Sequence


VERSION = "1.0.0"
ROOT = Path(__file__).resolve().parents[2]
FIGURE_DIR = ROOT / "scholarime" / "vendor" / "nature_skills" / "figure"
COMMANDS = {
    "template": "plot_templates.py",
    "validate-source": "validate_figure.py",
    "collision": "audit_figure_collisions.py",
    "panel-alignment": "audit_panel_alignment.py",
    "pdf-text": "audit_pdf_text.py",
    "svg": "audit_svg_schematic.py",
}
SELF_TEST_COMMANDS = (
    ("validate_figure.py", "--self-test"),
    ("audit_figure_collisions.py", "--self-test"),
    ("audit_panel_alignment.py", "--self-test"),
    ("audit_svg_schematic.py", "--help"),
    ("audit_pdf_text.py", "--help"),
)


def usage() -> str:
    return (
        "usage: scholarly_figure.py <command> [options]\n\n"
        "commands:\n"
        "  template         render volcano, ROC, dot, marginal, paired, heatmap, or forest template\n"
        "  validate-source  audit Python/R plotting source before rendering\n"
        "  collision        audit text and graphic collisions in an exported PDF\n"
        "  panel-alignment  audit a panel-layout JSON manifest\n"
        "  pdf-text         audit effective PDF text sizes\n"
        "  svg              audit an SVG schematic\n"
        "  self-test        run dependency-free audit self-tests\n"
        "  list-templates   list the bundled manuscript templates\n\n"
        "Examples:\n"
        "  scholarime figure template volcano --input results.csv --output figures/volcano\n"
        "  scholarime figure collision figure.pdf --json-out figure.audit.json\n"
        "  scholarime figure validate-source plotting.py --strict"
    )


def _run_script(script: str, args: Sequence[str]) -> int:
    path = FIGURE_DIR / script
    if not path.is_file():
        print(f"error: bundled figure tool is missing: {path}", file=sys.stderr)
        return 2
    return subprocess.run(
        [sys.executable, str(path), *args],
        check=False,
    ).returncode


def _list_templates() -> None:
    print("volcano        effect size versus adjusted p value")
    print("roc            one FPR column and one or more TPR columns")
    print("dotplot        size/color matrix for markers or categories")
    print("marginal       joint scatter with marginal distributions")
    print("paired         paired box-and-point plot for two conditions")
    print("heatmap        row-by-column value matrix with explicit order")
    print("forest         effect estimates with confidence intervals")


def _self_test() -> int:
    for script, argument in SELF_TEST_COMMANDS:
        print(f"== {script} ==")
        code = _run_script(script, [argument])
        if code:
            return code
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print(usage())
        return 0
    if args[0] == "--version":
        print(f"scholarly_figure.py {VERSION}")
        return 0
    command = args.pop(0)
    if command == "list-templates":
        _list_templates()
        return 0
    if command == "self-test":
        return _self_test()
    script = COMMANDS.get(command)
    if script is None:
        print(f"error: unknown figure command {command!r}\n\n{usage()}", file=sys.stderr)
        return 2
    return _run_script(script, args)


if __name__ == "__main__":
    raise SystemExit(main())
