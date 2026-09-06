from __future__ import annotations

import argparse
import json
from pathlib import Path

from .modes import Mode, desired_state


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="phono-console")
    parser.add_argument("--config", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")
    set_parser = subparsers.add_parser("set")
    set_parser.add_argument("mode", choices=[mode.value for mode in Mode])
    return parser


def main() -> int:
    args = build_parser().parse_args()
    # Parsing the full configuration begins when hardware adapters are added.
    # Requiring the file now prevents a future CLI compatibility break.
    if not args.config.is_file():
        raise SystemExit(f"Configuration file not found: {args.config}")

    if args.command == "status":
        print(json.dumps({"implementation": "skeleton", "hardware": "not connected"}))
        return 0

    mode = Mode(args.mode)
    print(json.dumps({"mode": mode.value, "desired_state": desired_state(mode).__dict__}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

