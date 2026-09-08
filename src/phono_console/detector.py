from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ActivityDetector:
    threshold_dbfs: float
    attack_seconds: float
    release_seconds: float
    hysteresis_db: float
    active: bool = False
    _candidate_since: float | None = None

    def update(self, level_dbfs: float, now: float) -> bool:
        """Update activity from a monotonic timestamp and an RMS level."""
        boundary = (
            self.threshold_dbfs - self.hysteresis_db
            if self.active
            else self.threshold_dbfs
        )
        candidate = level_dbfs >= boundary

        if candidate == self.active:
            self._candidate_since = None
            return self.active

        if self._candidate_since is None:
            self._candidate_since = now

        delay = self.attack_seconds if candidate else self.release_seconds
        if now - self._candidate_since >= delay:
            self.active = candidate
            self._candidate_since = None
        return self.active

    def reset_inactive(self) -> None:
        """Immediately clear activity when the input measurement is invalid."""
        self.active = False
        self._candidate_since = None
