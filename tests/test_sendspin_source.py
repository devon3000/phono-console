import asyncio
from dataclasses import dataclass, field

from phono_console.config import AudioConfig, SendspinConfig
from phono_console.controller import Status
from phono_console.policy import Route
from phono_console.sendspin_source import SendspinSourcePublisher
from phono_console.simulation import SimulatedEventSink
from phono_console.state import StateStore

AUDIO = AudioConfig(
    capture_device="phono_capture",
    playback_device="phono_playback",
    target_latency_ms=40,
)
SENDSPIN = SendspinConfig(
    server_url="ws://ma:8927/sendspin",
    player_name="Phono Console",
    source_name="Console Vinyl",
    source_enabled=True,
    state_dir="/tmp/unused",
)


@dataclass
class FakeSourceCommand:
    command: str


@dataclass
class FakeServerCommand:
    source: FakeSourceCommand | None


class FakeCapture:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.fed: list[bytes] = []

    async def start(self) -> None:
        self.started = True

    async def feed(self, pcm: bytes, capture_timestamp_us: int | None = None) -> None:
        self.fed.append(pcm)

    async def stop(self) -> None:
        self.stopped = True


@dataclass
class FakeClient:
    connect_error: Exception | None = None
    command_listeners: list = field(default_factory=list)
    disconnect_listeners: list = field(default_factory=list)
    connected_urls: list[str] = field(default_factory=list)
    disconnects: int = 0
    captures: list[FakeCapture] = field(default_factory=list)
    signals: list = field(default_factory=list)

    def add_server_command_listener(self, callback):
        self.command_listeners.append(callback)
        return lambda: self.command_listeners.remove(callback)

    def add_disconnect_listener(self, callback):
        self.disconnect_listeners.append(callback)
        return lambda: self.disconnect_listeners.remove(callback)

    async def connect(self, url: str) -> None:
        if self.connect_error is not None:
            raise self.connect_error
        self.connected_urls.append(url)

    async def disconnect(self) -> None:
        self.disconnects += 1

    def is_time_synchronized(self) -> bool:
        return True

    def create_source_capture(self, audio_format) -> FakeCapture:
        capture = FakeCapture()
        self.captures.append(capture)
        return capture

    async def send_source_signal(self, signal) -> None:
        self.signals.append(signal)

    def command(self, name: str) -> None:
        for listener in list(self.command_listeners):
            listener(FakeServerCommand(FakeSourceCommand(name)))


async def endless_pcm():
    while True:
        yield b"\x00\x00" * 2 * 960
        await asyncio.sleep(0)


async def finite_pcm():
    yield b"\x00\x00" * 2 * 960


def make_publisher(client: FakeClient, state: StateStore | None = None):
    events = SimulatedEventSink()
    state = state or StateStore()

    async def factory():
        return client

    publisher = SendspinSourcePublisher(
        SENDSPIN,
        AUDIO,
        events,
        state,
        client_factory=factory,
        pcm_stream_factory=endless_pcm,
        reconnect_seconds=0.01,
        signal_poll_seconds=0.01,
    )
    publisher.client_id = "source-client-id"
    return publisher, events, state


def test_server_commands_start_and_stop_the_capture_stream() -> None:
    async def scenario() -> None:
        client = FakeClient()
        publisher, events, state = make_publisher(client)
        stop = asyncio.Event()
        run = asyncio.create_task(publisher.run(stop))
        await asyncio.sleep(0.05)
        assert client.connected_urls == [SENDSPIN.server_url]

        client.command("start")
        await asyncio.sleep(0.05)
        assert client.captures and client.captures[0].started
        assert client.captures[0].fed  # PCM is flowing
        assert state.sendspin_source["streaming"] is True

        client.command("stop")
        await asyncio.sleep(0.05)
        assert client.captures[0].stopped
        assert state.sendspin_source["streaming"] is False

        stop.set()
        await asyncio.wait_for(run, timeout=2)
        assert client.disconnects == 1
        names = [event for event, _ in events.events]
        assert "sendspin_source_connected" in names
        assert "sendspin_source_stream_started" in names
        assert "sendspin_source_stream_stopped" in names

    asyncio.run(scenario())


def test_publisher_reconnects_after_connect_failure() -> None:
    async def scenario() -> None:
        client = FakeClient(connect_error=OSError("refused"))
        publisher, events, _ = make_publisher(client)
        stop = asyncio.Event()
        run = asyncio.create_task(publisher.run(stop))
        await asyncio.sleep(0.06)
        stop.set()
        await asyncio.wait_for(run, timeout=2)
        failures = [
            event for event, _ in events.events
            if event == "sendspin_source_connect_failed"
        ]
        assert len(failures) >= 2  # kept retrying

    asyncio.run(scenario())


def test_phono_activity_is_reported_as_line_sense_signal() -> None:
    async def scenario() -> None:
        client = FakeClient()
        state = StateStore()
        publisher, _, _ = make_publisher(client, state)
        stop = asyncio.Event()
        run = asyncio.create_task(publisher.run(stop))
        await asyncio.sleep(0.03)

        await state.set_status(Status(Route.LOCAL_PHONO, True, False, False, -20.0))
        await asyncio.sleep(0.05)
        await state.set_status(Status(Route.IDLE, False, False, False, -80.0))
        await asyncio.sleep(0.05)

        stop.set()
        await asyncio.wait_for(run, timeout=2)
        values = [signal.value for signal in client.signals]
        assert values[:3] == ["absent", "present", "absent"]

    asyncio.run(scenario())


def test_capture_eof_clears_state_and_allows_restart() -> None:
    async def scenario() -> None:
        client = FakeClient()
        publisher, events, state = make_publisher(client)
        publisher._pcm_stream_factory = finite_pcm
        stop = asyncio.Event()
        run = asyncio.create_task(publisher.run(stop))
        await asyncio.sleep(0.03)

        client.command("start")
        await asyncio.sleep(0.05)
        assert publisher._stream_task is None
        assert state.sendspin_source["streaming"] is False

        client.command("start")
        await asyncio.sleep(0.05)
        assert len(client.captures) == 2
        assert [name for name, _ in events.events].count(
            "sendspin_source_stream_stopped"
        ) == 2

        stop.set()
        await asyncio.wait_for(run, timeout=2)

    asyncio.run(scenario())
