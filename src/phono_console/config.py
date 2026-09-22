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
    needle_drop_peak_dbfs: float = -45.0


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
    volume_min: int = 20
    volume_max: int = 80


@dataclass(frozen=True)
class RoutingConfig:
    distribution_target: str = "Downstairs"
    local_fallback_enabled: bool = True
    distribution_recovery_hold_ms: int = 10_000
    distribution_start_timeout_ms: int = 5_000
    phono_mode_sticky_minutes: int = 60


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
    phono_gain_db: float = 12.0
    bluetooth_gain_db: float = 6.0
    limiter_ceiling_dbfs: float = -1.0
    limiter_release_ms: int = 200


@dataclass(frozen=True)
class RuntimeConfig:
    poll_interval_ms: int = 100
    api_host: str = "0.0.0.0"
    api_port: int = 8765


@dataclass(frozen=True)
class AudioEngineConfig:
    backend: str = "legacy"
    socket_path: str = "/run/phono-console/audio-engine.sock"
    frame_ms: int = 20
    output_prebuffer_ms: int = 80
    route_fade_ms: int = 8
    max_soft_correction_ppm: int = 250
    queue_frames: int = 50


@dataclass(frozen=True)
class AmplifierConfig:
    enabled: bool = True
    cec_device: str = "/dev/cec0"
    logical_address: int = 5
    wake_on_audio: bool = True
    physical_address: str = "1.0.0.0"
    wake_settle_seconds: float = 0.0
    volume_min: int = 15
    volume_max: int = 45
    local_phono_volume: int = 50


@dataclass(frozen=True)
class Config:
    audio: AudioConfig
    detection: DetectionConfig
    music_assistant: MusicAssistantConfig
    sendspin: SendspinConfig
    bluetooth: BluetoothConfig = BluetoothConfig()
    routing: RoutingConfig = RoutingConfig()
    runtime: RuntimeConfig = RuntimeConfig()
    audio_engine: AudioEngineConfig = AudioEngineConfig()
    amplifier: AmplifierConfig = AmplifierConfig()


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
    audio_engine = raw.get("audio_engine", {})
    amplifier = raw.get("amplifier", {})

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
            needle_drop_peak_dbfs=float(
                detection.get("needle_drop_peak_dbfs", -45.0)
            ),
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
            phono_gain_db=float(sendspin.get("phono_gain_db", 12.0)),
            bluetooth_gain_db=float(sendspin.get("bluetooth_gain_db", 6.0)),
            limiter_ceiling_dbfs=float(
                sendspin.get("limiter_ceiling_dbfs", -1.0)
            ),
            limiter_release_ms=int(sendspin.get("limiter_release_ms", 200)),
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
            volume_min=int(bluetooth.get("volume_min", 20)),
            volume_max=int(bluetooth.get("volume_max", 80)),
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
            distribution_start_timeout_ms=int(
                routing.get("distribution_start_timeout_ms", 5_000)
            ),
            phono_mode_sticky_minutes=int(
                routing.get("phono_mode_sticky_minutes", 60)
            ),
        ),
        runtime=RuntimeConfig(
            poll_interval_ms=int(runtime.get("poll_interval_ms", 100)),
            api_host=str(runtime.get("api_host", "0.0.0.0")),
            api_port=int(runtime.get("api_port", 8765)),
        ),
        audio_engine=AudioEngineConfig(
            backend=str(audio_engine.get("backend", "legacy")),
            socket_path=str(
                audio_engine.get(
                    "socket_path", "/run/phono-console/audio-engine.sock"
                )
            ),
            frame_ms=int(audio_engine.get("frame_ms", 20)),
            output_prebuffer_ms=int(
                audio_engine.get("output_prebuffer_ms", 80)
            ),
            route_fade_ms=int(audio_engine.get("route_fade_ms", 8)),
            max_soft_correction_ppm=int(
                audio_engine.get("max_soft_correction_ppm", 250)
            ),
            queue_frames=int(audio_engine.get("queue_frames", 50)),
        ),
        amplifier=AmplifierConfig(
            enabled=bool(amplifier.get("enabled", True)),
            cec_device=str(amplifier.get("cec_device", "/dev/cec0")),
            logical_address=int(amplifier.get("logical_address", 5)),
            wake_on_audio=bool(amplifier.get("wake_on_audio", True)),
            physical_address=str(amplifier.get("physical_address", "1.0.0.0")),
            wake_settle_seconds=float(amplifier.get("wake_settle_seconds", 0.0)),
            volume_min=int(amplifier.get("volume_min", 15)),
            volume_max=int(amplifier.get("volume_max", 45)),
            local_phono_volume=int(amplifier.get("local_phono_volume", 50)),
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
    if not -120 <= config.detection.needle_drop_peak_dbfs <= 0:
        raise ValueError(
            "detection.needle_drop_peak_dbfs must be between -120 and 0"
        )
    if config.bluetooth.attack_ms < 0 or config.bluetooth.release_ms < 0:
        raise ValueError("bluetooth attack/release times cannot be negative")
    if not 10 <= config.bluetooth.pairing_window_seconds <= 600:
        raise ValueError("bluetooth.pairing_window_seconds must be between 10 and 600")
    if not 0 <= config.bluetooth.volume_min < config.bluetooth.volume_max <= 100:
        raise ValueError(
            "bluetooth volume range must satisfy 0 <= volume_min < volume_max <= 100"
        )
    if not -12.0 <= config.sendspin.limiter_ceiling_dbfs <= 0.0:
        raise ValueError("sendspin.limiter_ceiling_dbfs must be between -12 and 0")
    if not 20 <= config.sendspin.limiter_release_ms <= 5000:
        raise ValueError("sendspin.limiter_release_ms must be between 20 and 5000")
    if not config.routing.distribution_target:
        raise ValueError("routing.distribution_target must be non-empty")
    if config.routing.distribution_recovery_hold_ms < 0:
        raise ValueError("routing.distribution_recovery_hold_ms cannot be negative")
    if not 500 <= config.routing.distribution_start_timeout_ms <= 30_000:
        raise ValueError("routing.distribution_start_timeout_ms is invalid")
    if not 5 <= config.routing.phono_mode_sticky_minutes <= 24 * 60:
        raise ValueError("routing.phono_mode_sticky_minutes must be 5..1440")
    if not 10 <= config.runtime.poll_interval_ms <= 5000:
        raise ValueError("runtime.poll_interval_ms must be between 10 and 5000")
    if not 1 <= config.runtime.api_port <= 65535:
        raise ValueError("runtime.api_port must be between 1 and 65535")
    if not all(config.music_assistant.whole_house_players):
        raise ValueError("music_assistant.whole_house_players entries must be non-empty")
    if config.sendspin.source_enabled and not config.sendspin.state_dir:
        raise ValueError("sendspin.state_dir is required when source_enabled is true")
    if not -24.0 <= config.sendspin.phono_gain_db <= 24.0:
        raise ValueError("sendspin.phono_gain_db must be between -24 and 24")
    if not -24.0 <= config.sendspin.bluetooth_gain_db <= 24.0:
        raise ValueError("sendspin.bluetooth_gain_db must be between -24 and 24")
    if config.audio_engine.backend not in {"legacy", "timestamped"}:
        raise ValueError("audio_engine.backend must be legacy or timestamped")
    if not config.audio_engine.socket_path.startswith("/"):
        raise ValueError("audio_engine.socket_path must be absolute")
    if config.audio_engine.frame_ms not in {10, 20, 40}:
        raise ValueError("audio_engine.frame_ms must be 10, 20, or 40")
    if config.audio_engine.output_prebuffer_ms < config.audio_engine.frame_ms:
        raise ValueError("audio_engine.output_prebuffer_ms is too small")
    if not 0 <= config.audio_engine.route_fade_ms <= 100:
        raise ValueError("audio_engine.route_fade_ms must be between 0 and 100")
    if not 1 <= config.audio_engine.max_soft_correction_ppm <= 1000:
        raise ValueError("audio_engine.max_soft_correction_ppm is invalid")
    if not 2 <= config.audio_engine.queue_frames <= 500:
        raise ValueError("audio_engine.queue_frames must be between 2 and 500")
    if not config.amplifier.cec_device.startswith("/"):
        raise ValueError("amplifier.cec_device must be absolute")
    if not 0 <= config.amplifier.logical_address <= 15:
        raise ValueError("amplifier.logical_address must be between 0 and 15")
    physical_parts = config.amplifier.physical_address.split(".")
    if len(physical_parts) != 4 or any(
        len(part) != 1 or part.lower() not in "0123456789abcdef"
        for part in physical_parts
    ):
        raise ValueError(
            "amplifier.physical_address must contain four hexadecimal nibbles"
        )
    if not 0 <= config.amplifier.wake_settle_seconds <= 30:
        raise ValueError("amplifier.wake_settle_seconds must be between 0 and 30")
    if not 0 <= config.amplifier.volume_min < config.amplifier.volume_max <= 100:
        raise ValueError(
            "amplifier volume range must satisfy 0 <= volume_min < volume_max <= 100"
        )
    if not 0 <= config.amplifier.local_phono_volume <= 100:
        raise ValueError("amplifier.local_phono_volume must be between 0 and 100")
