import asyncio

from phono_console.amplifier import (
    CecAmplifier,
    cec_to_logical,
    logical_to_cec,
    physical_address_bytes,
)
from phono_console.config import AmplifierConfig
from phono_console.simulation import SimulatedEventSink
from phono_console.state import StateStore


def test_volume_mapping_uses_configured_safe_amplifier_range() -> None:
    config = AmplifierConfig(volume_min=15, volume_max=45, volume_mid=None)

    assert logical_to_cec(config, 1) == 15
    assert logical_to_cec(config, 50) == 30
    assert logical_to_cec(config, 100) == 45
    assert cec_to_logical(config, 15) == 1
    assert cec_to_logical(config, 30) == 50
    assert cec_to_logical(config, 45) == 100


def test_volume_mapping_clamps_values_outside_each_range() -> None:
    config = AmplifierConfig(volume_min=15, volume_max=45, volume_mid=None)

    assert logical_to_cec(config, -10) == 15
    assert logical_to_cec(config, 120) == 45
    assert cec_to_logical(config, 5) == 1
    assert cec_to_logical(config, 80) == 100


def test_physical_address_encodes_hdmi_input_path() -> None:
    assert physical_address_bytes("1.0.0.0") == (0x10, 0x00)
    assert physical_address_bytes("3.2.1.0") == (0x32, 0x10)


def test_calibrated_curve_is_monotonic_and_round_trips_hardware_steps() -> None:
    config = AmplifierConfig()
    assert [logical_to_cec(config, v) for v in (1, 60, 100)] == [12, 20, 40]
    assert [cec_to_logical(config, v) for v in (12, 20, 40)] == [1, 60, 100]
    mapped = [logical_to_cec(config, v) for v in range(1, 101)]
    assert mapped == sorted(mapped)
    assert set(mapped) == set(range(12, 41))
    for hardware in range(12, 41):
        assert logical_to_cec(config, cec_to_logical(config, hardware)) == hardware
    assert cec_to_logical(config, 0) == 1
    assert cec_to_logical(config, 80) == 100


class CecProcess:
    """An interactive CEC process; replies arrive through its one stdout reader."""

    def __init__(self, *, volume=20, powered=True, stop=None):
        self.stdout = asyncio.StreamReader()
        self.stdin = self
        self.returncode = None
        self.volume = volume
        self.powered = powered
        self.commands = []
        self.stop = stop

    def write(self, data):
        command = data.decode().strip()
        self.commands.append(command)
        if command == "q":
            self.returncode = 0
            self.stdout.feed_eof()
        elif command.endswith(":8f"):
            self.stdout.feed_data(f">> 51:90:{0 if self.powered else 1:02x}\n".encode())
            if not self.powered and self.stop:
                self.stop.set()
        elif command.endswith(":71"):
            self.stdout.feed_data(f">> 51:7a:{self.volume:02x}\n".encode())
            if self.stop:
                self.stop.set()
        elif command.endswith(":44:43"):
            self.volume ^= 0x80
        elif command.endswith(":44:41"):
            self.volume += 1
        elif command.endswith(":44:42"):
            self.volume -= 1

    async def drain(self):
        pass

    async def wait(self):
        return self.returncode


def make_amplifier(monkeypatch, process):
    async def spawn(*args, **kwargs):
        return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    return CecAmplifier(AmplifierConfig(), SimulatedEventSink(), StateStore())


def test_remote_reports_refresh_state_without_writing_volume(monkeypatch) -> None:
    async def scenario():
        process = CecProcess()
        amp = make_amplifier(monkeypatch, process)
        await amp.start()
        for raw in (12, 20, 40, 0x94):  # 20 with mute bit set
            process.stdout.feed_data(f">> 51:7a:{raw:02x}\n".encode())
            await asyncio.sleep(0.01)
            status = amp.state.components["amplifier"]
            assert status["cec_volume"] == (raw & 0x7F)
            assert status["volume"] == (0 if raw & 0x80 else cec_to_logical(amp.config, raw))
        assert process.commands == []
        await amp.close()
    asyncio.run(scenario())


def test_poll_reads_current_volume_and_power_without_waking_or_restoring(monkeypatch) -> None:
    async def scenario():
        for powered in (True, False):
            stop = asyncio.Event()
            process = CecProcess(volume=40, powered=powered, stop=stop)
            amp = make_amplifier(monkeypatch, process)
            await amp.run(stop)
            status = amp.state.components["amplifier"]
            assert status["powered"] is powered
            assert process.commands == (["tx 15:8f", "tx 15:71"] if powered else ["tx 15:8f"])
            if powered:
                assert status["cec_volume"] == 40
            await amp.close()
    asyncio.run(scenario())


def test_set_volume_discards_cached_reply_and_uses_new_curve(monkeypatch) -> None:
    async def scenario():
        process = CecProcess(volume=20)
        amp = make_amplifier(monkeypatch, process)
        await amp.start()
        process.stdout.feed_data(b">> 51:7a:0c\n")
        await asyncio.sleep(0.01)
        # Current amp is already at CEC 20: no volume keys should be sent.
        assert await amp.set_volume(60) == (60, False)
        assert process.commands == ["tx 15:71", "tx 15:71"]
        await amp.close()
    asyncio.run(scenario())


def test_zero_mutes_and_new_positive_setting_unmutes(monkeypatch) -> None:
    async def scenario():
        process = CecProcess(volume=20)
        amp = make_amplifier(monkeypatch, process)
        assert await amp.set_volume(0) == (0, True)
        assert process.volume == 0x94
        assert await amp.set_volume(60) == (60, False)
        assert process.volume == 20
        assert process.commands.count("tx 15:44:43") == 2
        await amp.close()
    asyncio.run(scenario())
