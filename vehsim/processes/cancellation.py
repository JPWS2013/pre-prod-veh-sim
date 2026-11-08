"""Customer cancellations: the longer someone waits, the likelier they cancel."""

from __future__ import annotations

from ..config import CancellationConfig
from ..models import CustomerStatus


class CancellationModel:
    def __init__(self, cfg: CancellationConfig) -> None:
        self.cfg = cfg

    def weekly_prob(self, status: CustomerStatus, weeks_waiting: float) -> float:
        base = self.cfg.base_weekly.get(status.value, 0.0)
        if base <= 0 and self.cfg.slope_per_week <= 0:
            return 0.0
        return min(self.cfg.max_weekly, base + self.cfg.slope_per_week * max(0.0, weeks_waiting))
