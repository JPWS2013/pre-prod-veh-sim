"""Factory model: limited build positions, random build time, quality holds.

A vehicle occupies one build position from the day it starts until it passes
quality (including any quality-hold rework). Slots offered for a week are
``min(line_rate, free positions)``, so slow builds and holds both reduce the
number of slots that open up.
"""

from __future__ import annotations

import random
from typing import Optional, Protocol

from ..config import FactoryConfig


class BuildTimeModel(Protocol):
    def sample_days(self, rng: random.Random) -> int: ...


class QualityModel(Protocol):
    def sample_hold_days(self, rng: random.Random) -> int:
        """0 if the vehicle passes quality first time, otherwise rework days."""
        ...


class UniformBuildTime:
    def __init__(self, min_weeks: float, max_weeks: float) -> None:
        if min_weeks > max_weeks:
            raise ValueError("build_weeks_min must be <= build_weeks_max")
        self.lo, self.hi = round(min_weeks * 7), round(max_weeks * 7)

    def sample_days(self, rng: random.Random) -> int:
        return rng.randint(self.lo, self.hi)


class FixedBuildTime:
    def __init__(self, days: int) -> None:
        self.days = days

    def sample_days(self, rng: random.Random) -> int:
        return self.days


class UniformQualityHold:
    def __init__(self, prob: float, min_weeks: float, max_weeks: float) -> None:
        self.prob = prob
        self.lo, self.hi = round(min_weeks * 7), round(max_weeks * 7)

    def sample_hold_days(self, rng: random.Random) -> int:
        if rng.random() < self.prob:
            return rng.randint(self.lo, self.hi)
        return 0


class Factory:
    def __init__(
        self,
        cfg: FactoryConfig,
        build_time: Optional[BuildTimeModel] = None,
        quality: Optional[QualityModel] = None,
    ) -> None:
        self.cfg = cfg
        self.line_rate = cfg.line_rate
        self.positions = cfg.resolved_positions()
        self.build_time = build_time or UniformBuildTime(cfg.build_weeks_min, cfg.build_weeks_max)
        self.quality = quality or UniformQualityHold(cfg.quality_hold_prob, cfg.hold_weeks_min, cfg.hold_weeks_max)
        self.in_build = 0
        self.on_hold = 0

    @property
    def occupied(self) -> int:
        return self.in_build + self.on_hold

    @property
    def free_positions(self) -> int:
        return self.positions - self.occupied

    def slots_available(self) -> int:
        return max(0, min(self.line_rate, self.free_positions))

    # Bookkeeping hooks called by the simulator.
    def start(self) -> None:
        self.in_build += 1

    def finish_build(self, held: bool) -> None:
        self.in_build -= 1
        if held:
            self.on_hold += 1

    def release_hold(self) -> None:
        self.on_hold -= 1
