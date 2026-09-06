from phono_console.detector import ActivityDetector


def detector() -> ActivityDetector:
    return ActivityDetector(-48.0, 0.25, 5.0, 6.0)


def test_attack_requires_sustained_signal() -> None:
    subject = detector()
    assert not subject.update(-40.0, 0.0)
    assert not subject.update(-40.0, 0.24)
    assert subject.update(-40.0, 0.25)


def test_release_holds_activity_across_brief_silence() -> None:
    subject = detector()
    subject.update(-40.0, 0.0)
    subject.update(-40.0, 0.25)
    assert subject.update(-80.0, 1.0)
    assert subject.update(-80.0, 5.99)
    assert not subject.update(-80.0, 6.0)


def test_hysteresis_avoids_threshold_chatter() -> None:
    subject = detector()
    subject.update(-40.0, 0.0)
    subject.update(-40.0, 0.25)
    assert subject.update(-50.0, 1.0)

