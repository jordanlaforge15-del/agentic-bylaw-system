"""Consecutive-failure tracking with alert-threshold logic."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from advisor.monitoring.probe import ProbeResult


@dataclass
class FailureTracker:
    """Counts consecutive probe failures and fires *on_alert* at threshold.

    The counter resets to 0 on the first healthy result.  *on_alert* is
    called exactly once per "outage window" — it will not fire again
    until at least one healthy probe intervenes.
    """

    failure_threshold: int = 2
    on_alert: Callable[[ProbeResult, int], None] | None = None

    _consecutive_failures: int = field(default=0, init=False, repr=False)
    _alert_active: bool = field(default=False, init=False, repr=False)
    _last_result: ProbeResult | None = field(default=None, init=False, repr=False)

    def record(self, result: ProbeResult) -> None:
        self._last_result = result
        if result.healthy:
            self._consecutive_failures = 0
            self._alert_active = False
        else:
            self._consecutive_failures += 1
            if (
                self._consecutive_failures >= self.failure_threshold
                and not self._alert_active
                and self.on_alert is not None
            ):
                self._alert_active = True
                self.on_alert(result, self._consecutive_failures)

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def alert_active(self) -> bool:
        return self._alert_active

    @property
    def last_result(self) -> ProbeResult | None:
        return self._last_result
