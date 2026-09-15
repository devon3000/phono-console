from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AudioConfig:
    capture_device: str
    playback_device: str
    target_latency_ms: int
    sample_rate: int = 48_000
    channels: int = 2
    detection_window_ms: int = 100


@dataclass(frozen=True)
class DetectionConfig:
    phono_threshold_dbfs: float
    attack_ms: int
    release_ms: int
    hysteresis_db: float


@dataclass(frozen=True)
class BluetoothConfig:
    enabled: bool = False
    capture_device: str = "bluealsa"
    threshold_dbfs: float = -60.0
    attack_ms: int = 200
    release_ms: int = 2000
    adapter: str = "hci0"
    alias: str = "PhonoConsole"
    pairing_window_seconds: int = 120


@dataclass(frozen=True)
class RoutingConfig:
    distribution_target: str = "Downstairs"
    local_fallback_enabled: bool = True
    distribution_recovery_hold_ms: int = 10_000


@dataclass(frozen=True)
class MusicAssistantConfig:
    base_url: str
    console_player: str
    vinyl_source: str
    token_env: str = "PHONO_CONSOLE_MA_TOKEN"
    whole_house_players: tuple[str, ...] = ("Downstairs",)


@dataclass(frozen=True)
class SendspinConfig:
    server_url: str
    player_name: str
    source_name: str
    source_enabled: bool = False
    state_dir: str = "/var/lib/phono-console/source"


@dataclass(frozen=True)
class RuntimeConfig:
    poll_interval_ms: int = 100
    api_host: str = "0.0.0.0"
    api_port: int = 8765
    api_token_env: str = "PHONO_CONSOLE_API_TOKEN"


@dataclass(frozen=True)
class Config:
    audio: AudioConfig
    detection: DetectionConfig
    music_assistant: MusicAssistantConfig
    sendspin: SendspinConfig
    bluetooth: BluetoothConfig = BluetoothConfig()
    routing: RoutingConfig = RoutingConfig()
    runtime: RuntimeConfig = RuntimeConfig()


def _required(table: dict, key: str, section: str):
    try:
        return table[key]
    except KeyError as exc:
        raise ValueError(f"Missing configuration value [{section}].{key}") from exc


def load_config(path: Path) -> Config:
    with path.open("rb") as handle:
        raw = tomllib.load(handle)

    audio = raw.get("audio", {})
    detection = raw.get("detection", {})
    ma = raw.get("music_assistant", {})
    sendspin = raw.get("sendspin", {})
    runtime = raw.get("runtime", {})
    bluetooth = raw.get("bluetooth", {})
    routing = raw.get("routing", {})

    config = Config(
        audio=AudioConfig(
            capture_device=str(_required(audio, "capture_device", "audio")),
            playback_device=str(_required(audio, "playback_device", "audio")),
            target_latency_ms=int(_required(audio, "target_latency_ms", "audio")),
            sample_rate=int(audio.get("sample_rate", 48_000)),
            channels=int(audio.get("channels", 2)),
            detection_window_ms=int(audio.get("detection_window_ms", 100)),
        ),
        detection=DetectionConfig(
            phono_threshold_dbfs=float(
                _required(detection, "phono_threshold_dbfs", "detection")
            ),
            attack_ms=int(_required(detection, "attack_ms", "detection")),
            release_ms=int(_required(detection, "release_ms", "detection")),
            hysteresis_db=float(_required(detection, "hysteresis_db", "detection")),
        ),
        music_assistant=MusicAssistantConfig(
            base_url=str(_required(ma, "base_url", "music_assistant")),
            console_player=str(_required(ma, "console_player", "music_assistant")),
            vinyl_source=str(_required(ma, "vinyl_source", "music_assistant")),
            token_env=str(ma.get("token_env", "PHONO_CONSOLE_MA_TOKEN")),
            whole_house_players=tuple(
                str(player)
                for player in ma.get("whole_house_players", ["Downstairs"])
            ),
        ),
        sendspin=SendspinConfig(
            server_url=str(_required(sendspin, "server_url", "sendspin")),
            player_name=str(_required(sendspin, "player_name", "sendspin")),
            source_name=str(_required(sendspin, "source_name", "sendspin")),
            source_enabled=bool(sendspin.get("source_enabled", False)),
            state_dir=str(
                sendspin.get("state_dir", "/var/lib/phono-console/source")
            ),
        ),
        bluetooth=BluetoothConfig(
            enabled=bool(bluetooth.get("enabled", False)),
            capture_device=str(bluetooth.get("capture_device", "bluealsa")),
            threshold_dbfs=float(bluetooth.get("threshold_dbfs", -60.0)),
            attack_ms=int(bluetooth.get("attack_ms", 200)),
            release_ms=int(bluetooth.get("release_ms", 2000)),
            adapter=str(bluetooth.get("adapter", "hci0")),
            alias=str(bluetooth.get("alias", "PhonoConsole")),
            pairing_window_seconds=int(
                bluetooth.get("pairing_window_seconds", 120)
            ),
        ),
        routing=RoutingConfig(
            distribution_target=str(
                routing.get(
                    "distribution_target",
                    ma.get("whole_house_players", ["Downstairs"])[0],
                )
            ),
            local_fallback_enabled=bool(
                routing.get("local_fallback_enabled", True)
            ),
            distribution_recovery_hold_ms=int(
                routing.get("distribution_recovery_hold_ms", 10_000)
            ),
        ),
        runtime=RuntimeConfig(
            poll_interval_ms=int(runtime.get("poll_interval_ms", 100)),
            api_host=str(runtime.get("api_host", "0.0.0.0")),
            api_port=int(runtime.get("api_port", 8765)),
            api_token_env=str(
                runtime.get("api_token_env", "PHONO_CONSOLE_API_TOKEN")
            ),
        ),
    )
    _validate(config)
    return config


def _validate(config: Config) -> None:
    if not 10 <= config.audio.target_latency_ms <= 500:
        raise ValueError("audio.target_latency_ms must be between 10 and 500")
    if config.audio.sample_rate <= 0 or config.audio.channels not in (1, 2):
        raise ValueError("audio sample rate/channels are invalid")
    if not 10 <= config.audio.detection_window_ms <= 1000:
        raise ValueError("audio.detection_window_ms must be between 10 and 1000")
    if config.detection.attack_ms < 0 or config.detection.release_ms < 0:
        raise ValueError("detection attack/release times cannot be negative")
    if config.detection.hysteresis_db < 0:
        raise ValueError("detection.hysteresis_db cannot be negative")
    if config.bluetooth.attack_ms < 0 or config.bluetooth.release_ms < 0:
        raise ValueError("bluetooth attack/release times cannot be negative")
    if not 10 <= config.bluetooth.pairing_window_seconds <= 600:
        raise ValueError("bluetooth.pairing_window_seconds must be between 10 and 600")
    if not config.routing.distribution_target:
        raise ValueError("routing.distribution_target must be non-empty")
    if config.routing.distribution_recovery_hold_ms < 0:
        raise ValueError("routing.distribution_recovery_hold_ms cannot be negative")
    if not 10 <= config.runtime.poll_interval_ms <= 5000:
        raise ValueError("runtime.poll_interval_ms must be between 10 and 5000")
    if not 1 <= config.runtime.api_port <= 65535:
        raise ValueError("runtime.api_port must be between 1 and 65535")
    if not all(config.music_assistant.whole_house_players):
        raise ValueError("music_assistant.whole_house_players entries must be non-empty")
    if config.sendspin.source_enabled and not config.sendspin.state_dir:
        raise ValueError("sendspin.state_dir is required when source_enabled is true")
