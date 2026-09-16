#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from phono_console.probe_analysis import analyze


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    for path in args.files:
        print(json.dumps(analyze(path), indent=2))


if __name__ == "__main__":
    main()
