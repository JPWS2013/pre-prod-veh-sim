"""Simulation settings. Every number in the model lives here with a default."""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .catalog import ConfigMix

# 20 default delivery centers: real Rivian service centers, spread across the US.
# Addresses are from rivian.com's US service center list (Nov 2026), with suite/building
# numbers dropped. Minneapolis is written as St Louis Park, its actual city, and all 20
# were checked to geocode with Nominatim (OpenStreetMap). Ids are C01, C02, ... in this order.
DEFAULT_CENTERS: list[str] = [
    "3201 South Norfolk St, Seattle, WA 98118",
    "2620 SW 1st Ave, Portland, OR 97201",
    "340 Fell St, San Francisco, CA 94102",
    "7701 Haskell Ave, Los Angeles, CA 91406",          # Van Nuys
    "601 North Lindenwood Dr, Olathe, KS 66062",        # Kansas City area
    "1075 E Salter Dr, Phoenix, AZ 85024",
    "1830 Redwood Depot Ln, Salt Lake City, UT 84104",
    "3893 Steele St, Denver, CO 80205",
    "2512 Hawes Ave, Dallas, TX 75235",
    "14820 North Freeway, Houston, TX 77090",
    "6980 Oxford St, St Louis Park, MN 55426",          # Minneapolis area
    "2033 W Walnut St, Chicago, IL 60612",
    "32601 Industrial Dr, Madison Heights, MI 48071",   # Detroit area
    "305 5th Ave N, Franklin, TN 37064",                # Nashville area
    "2333 Defoor Hills Rd NW, Atlanta, GA 30318",
    "9309 Monroe Rd, Charlotte, NC 28270",
    "6955 NW 52nd St, Miami, FL 33166",
    "8787 Snouffer School Rd, Gaithersburg, MD 20879",  # Washington, DC area
    "512 Johnson Ave, Brooklyn, NY 11237",              # New York area
    "25 Griffin Way, Chelsea, MA 02150",                # Boston area
]


@dataclass
class DemandConfig:
    """Weekly reservations ~ Poisson(base + spike * exp(-week / decay))."""

    base_weekly: float = 300.0
    launch_spike: float = 3000.0
    spike_decay_weeks: float = 4.0


@dataclass
class ConversionConfig:
    """What happens after a reservation is invited to order."""

    never_order_share: float = 0.10
    delay_mean_days: float = 10.0
    delay_sd_days: float = 6.0
    delay_min_days: int = 1
    delay_max_days: int = 30


@dataclass
class FactoryConfig:
    line_rate: int = 500  # max vehicles that can start per week
    positions: Optional[int] = None  # vehicles in the factory at once; None → line_rate × mean build weeks
    build_weeks_min: float = 8.0
    build_weeks_max: float = 14.0
    quality_hold_prob: float = 0.05
    hold_weeks_min: float = 1.0
    hold_weeks_max: float = 3.0
    city: str = "Kansas City"
    state: str = "MO"

    def resolved_positions(self) -> int:
        if self.positions is not None:
            return self.positions
        return round(self.line_rate * (self.build_weeks_min + self.build_weeks_max) / 2)


@dataclass
class LogisticsConfig:
    miles_per_week: float = 500.0  # transit = ceil(miles / miles_per_week) weeks
    handover_prep_days: int = 2  # days between arrival at a center and earliest handover
    total_handover_capacity: int = 700  # per week across default centers, split by market size
    min_center_capacity: int = 10
    new_center_capacity: int = 25


def _default_cancel_base() -> dict[str, float]:
    return {
        "RESERVED": 0.002,
        "INVITED": 0.003,
        "ORDERED": 0.002,
        "IN_PRODUCTION": 0.002,
        "IN_TRANSIT": 0.0005,
        "AT_CENTER": 0.0005,
    }


@dataclass
class CancellationConfig:
    """Weekly cancel probability = min(max_weekly, base[status] + slope × weeks since reservation)."""

    base_weekly: dict[str, float] = field(default_factory=_default_cancel_base)
    slope_per_week: float = 0.0001
    max_weekly: float = 0.05


@dataclass
class SimConfig:
    n_weeks: int = 52
    seed: int = 0
    strict: bool = True  # raise on invalid actions; False → skip and report them
    demand: DemandConfig = field(default_factory=DemandConfig)
    conversion: ConversionConfig = field(default_factory=ConversionConfig)
    config_mix: ConfigMix = field(default_factory=ConfigMix)
    factory: FactoryConfig = field(default_factory=FactoryConfig)
    logistics: LogisticsConfig = field(default_factory=LogisticsConfig)
    cancellation: CancellationConfig = field(default_factory=CancellationConfig)
    centers: list[str] = field(default_factory=lambda: list(DEFAULT_CENTERS))  # full addresses

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SimConfig":
        return _build(cls, data)

    @classmethod
    def load(cls, path: str | Path) -> "SimConfig":
        """Load overrides from a .json (or .yaml, if PyYAML is installed) file."""
        path = Path(path)
        text = path.read_text()
        if path.suffix in (".yaml", ".yml"):
            import yaml  # optional dependency

            data = yaml.safe_load(text) or {}
        else:
            data = json.loads(text)
        return cls.from_dict(data)


def _build(cls, data: dict[str, Any]):
    """Build a (possibly nested) config dataclass, keeping defaults for missing keys."""
    if not isinstance(data, dict):
        raise TypeError(f"expected a mapping for {cls.__name__}, got {type(data).__name__}")
    known = {f.name: f for f in dataclasses.fields(cls)}
    unknown = set(data) - set(known)
    if unknown:
        raise ValueError(f"unknown {cls.__name__} settings: {sorted(unknown)}")
    kwargs = {}
    defaults = cls()
    for name, value in data.items():
        current = getattr(defaults, name)
        if dataclasses.is_dataclass(current):
            kwargs[name] = _build(type(current), value)
        elif name == "centers":
            kwargs[name] = [str(c) for c in value]
        else:
            kwargs[name] = value
    return cls(**kwargs)
