from __future__ import annotations

import argparse
import json

from .policy import Inputs, choose_route


def main() -> int:
    parser = argparse.ArgumentParser(prog="phono-console")
    parser.add_argument("--phono-active", action="store_true")
    parser.add_argument("--ma-playing", action="store_true")
    parser.add_argument("--whole-house", action="store_true")
    args = parser.parse_args()

    inputs = Inputs(
        phono_active=args.phono_active,
        ma_playing=args.ma_playing,
        whole_house_requested=args.whole_house,
    )
    print(json.dumps({"route": choose_route(inputs).value}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

