"""Transit times between the factory and delivery centers."""

from __future__ import annotations

import math

from ..config import LogisticsConfig
from ..geo import haversine_miles


class TransitModel:
    def __init__(self, cfg: LogisticsConfig) -> None:
        self.cfg = cfg

    def days(self, origin: tuple[float, float], dest: tuple[float, float]) -> int:
        """Whole weeks of transit: ceil(miles / miles_per_week) × 7; 0 for the same location."""
        miles = haversine_miles(origin, dest)
        if miles < 1:
            return 0
        return math.ceil(miles / self.cfg.miles_per_week) * 7
