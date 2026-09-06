from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .config import load_config
from .controller import Controller
from .policy import Inputs, choose_route
from .simulation import (
    SimulatedAudioRouter,
    SimulatedEventSink,
    SimulatedLevelMonitor,
    SimulatedMusicAssistant,
)


def main() -> int:
    parser = argparse.ArgumentParser(prog="phono-console")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--phono-active", action="store_true")
    parser.add_argument("--ma-playing", action="store_true")
    parser.add_argument("--whole-house", action="store_true")
    args = parser.parse_args()

    if args.config is not None:
        asyncio.run(_validate_controller(args.config))
        return 0

    inputs = Inputs(
        phono_active=args.phono_active,
        ma_playing=args.ma_playing,
        whole_house_requested=args.whole_house,
    )
    print(json.dumps({"route": choose_route(inputs).value}))
    return 0


async def _validate_controller(config_path: Path) -> None:
    config = load_config(config_path)
    router = SimulatedAudioRouter()
    controller = Controller(
        config,
        SimulatedLevelMonitor(),
        SimulatedMusicAssistant(),
        router,
        SimulatedEventSink(),
    )
    status = await controller.tick(now=0)
    print(json.dumps({"route": status.route.value, "configuration": "valid"}))


if __name__ == "__main__":
    raise SystemExit(main())
