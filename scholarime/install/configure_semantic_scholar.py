#!/usr/bin/env python3
"""Configure the optional Semantic Scholar API key."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent / "scripts"))

from scholar_config import (  # noqa: E402
    config_path,
    remove_semantic_scholar_key,
    save_semantic_scholar_key,
    semantic_scholar_key,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Configure Semantic Scholar API access.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--key-stdin", action="store_true", help="Read the API key from stdin.")
    group.add_argument("--no-key", action="store_true", help="Do not configure a key.")
    group.add_argument("--remove", action="store_true", help="Remove the saved API key.")
    return parser


def _prompt_key() -> str:
    answer = input("Do you have a Semantic Scholar API key? [y/N]: ").strip().casefold()
    if answer not in {"y", "yes"}:
        return ""
    value = getpass.getpass("Semantic Scholar API key (input hidden): ").strip()
    return value


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.remove:
        removed = remove_semantic_scholar_key()
        print("Semantic Scholar API key removed." if removed else "No saved key was present.")
        return 0

    existing = bool(semantic_scholar_key())
    if args.no_key:
        api_key = ""
    elif args.key_stdin:
        api_key = sys.stdin.readline().strip()
    else:
        env_key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "").strip()
        api_key = env_key or _prompt_key()

    if not api_key:
        if existing:
            print(f"Semantic Scholar remains configured in {config_path()}.")
        else:
            print(
                "Semantic Scholar search is unavailable until "
                "SEMANTIC_SCHOLAR_API_KEY or a saved API key is configured."
            )
        return 0
    if len(api_key) < 8:
        print("error: the Semantic Scholar API key is too short.", file=sys.stderr)
        return 2
    path = save_semantic_scholar_key(api_key)
    print(f"Semantic Scholar API key saved to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
