"""Incoming reservations and the customer addresses attached to them."""

from __future__ import annotations

import math
import random

from ..config import DemandConfig
from ..geo import CITIES
from ..models import Address

STREET_NAMES = ("Main", "Oak", "Maple", "Cedar", "Elm", "Park", "Lake", "Hill", "Pine", "Washington", "2nd", "3rd")
STREET_TYPES = ("St", "Ave", "Blvd", "Dr", "Ln", "Way")


def poisson(rng: random.Random, lam: float) -> int:
    """Poisson sample using only the stdlib (exact for small λ, normal approx for large)."""
    if lam <= 0:
        return 0
    if lam < 30:
        limit, k, p = math.exp(-lam), 0, 1.0
        while True:
            p *= rng.random()
            if p <= limit:
                return k
            k += 1
    return max(0, round(rng.gauss(lam, math.sqrt(lam))))


class DemandModel:
    def __init__(self, cfg: DemandConfig) -> None:
        self.cfg = cfg

    def weekly_rate(self, week: int) -> float:
        c = self.cfg
        return c.base_weekly + c.launch_spike * math.exp(-week / c.spike_decay_weeks)

    def arrivals(self, week: int, rng: random.Random) -> list[int]:
        """Days on which reservations arrive this week (sorted)."""
        n = poisson(rng, self.weekly_rate(week))
        start = week * 7
        return sorted(start + rng.randrange(7) for _ in range(n))


class AddressGenerator:
    """Random addresses in US cities weighted by market size."""

    def __init__(self, cities=CITIES) -> None:
        self.cities = [(c, s) for c, s, *_ in cities]
        self.weights = [w for *_, w in cities]

    def sample(self, rng: random.Random) -> Address:
        city, state = rng.choices(self.cities, weights=self.weights, k=1)[0]
        street = f"{rng.randint(100, 9999)} {rng.choice(STREET_NAMES)} {rng.choice(STREET_TYPES)}"
        return Address(street=street, city=city, state=state)
