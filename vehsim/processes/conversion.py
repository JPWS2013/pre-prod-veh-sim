"""Invite → order conversion."""

from __future__ import annotations

import random
from typing import Optional, Protocol

from ..config import ConversionConfig


class OrderDelayModel(Protocol):
    def sample_days(self, rng: random.Random) -> int: ...


class TruncatedNormalDelay:
    """Normal(mean, sd) truncated to [lo, hi] by redrawing, rounded to whole days."""

    def __init__(self, mean: float, sd: float, lo: int, hi: int) -> None:
        if lo > hi:
            raise ValueError("delay_min_days must be <= delay_max_days")
        self.mean, self.sd, self.lo, self.hi = mean, sd, lo, hi

    def sample_days(self, rng: random.Random) -> int:
        for _ in range(1000):
            x = round(rng.gauss(self.mean, self.sd))
            if self.lo <= x <= self.hi:
                return x
        return min(self.hi, max(self.lo, round(self.mean)))  # pathological parameters


class ConversionModel:
    def __init__(self, cfg: ConversionConfig, delay: Optional[OrderDelayModel] = None) -> None:
        self.cfg = cfg
        self.delay = delay or TruncatedNormalDelay(
            cfg.delay_mean_days, cfg.delay_sd_days, cfg.delay_min_days, cfg.delay_max_days
        )

    def on_invite(self, rng: random.Random) -> Optional[int]:
        """Days until the customer orders, or None if they never will."""
        if rng.random() < self.cfg.never_order_share:
            return None
        return self.delay.sample_days(rng)
