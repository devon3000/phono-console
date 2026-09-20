from phono_console.amplifier import cec_to_logical, logical_to_cec
from phono_console.config import AmplifierConfig


def test_volume_mapping_uses_configured_safe_amplifier_range() -> None:
    config = AmplifierConfig(volume_min=15, volume_max=45)

    assert logical_to_cec(config, 1) == 15
    assert logical_to_cec(config, 50) == 30
    assert logical_to_cec(config, 100) == 45
    assert cec_to_logical(config, 15) == 1
    assert cec_to_logical(config, 30) == 50
    assert cec_to_logical(config, 45) == 100


def test_volume_mapping_clamps_values_outside_each_range() -> None:
    config = AmplifierConfig(volume_min=15, volume_max=45)

    assert logical_to_cec(config, -10) == 15
    assert logical_to_cec(config, 120) == 45
    assert cec_to_logical(config, 5) == 1
    assert cec_to_logical(config, 80) == 100
