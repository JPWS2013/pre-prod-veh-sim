"""Simulation settings. Every number in the model lives here with a default."""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .catalog import ConfigMix

# 20 default delivery centers in key vehicle markets, spread across the US.
# Each is a full "street, city, ST ZIP" address; centers get ids C01, C02, ... in this order.
DEFAULT_CENTERS: list[str] = [
    "2201 Elliott Ave, Seattle, WA 98121",
    "1500 NW Naito Pkwy, Portland, OR 97209",
    "1 Market St, San Francisco, CA 94105",
    "800 W Olympic Blvd, Los Angeles, CA 90015",
    "3150 Paradise Rd, Las Vegas, NV 89109",
    "400 E Van Buren St, Phoenix, AZ 85004",
    "175 W 200 S, Salt Lake City, UT 84101",
    "1701 Wynkoop St, Denver, CO 80202",
    "2500 Victory Ave, Dallas, TX 75219",
    "1001 Avenida De Las Americas, Houston, TX 77010",
    "600 1st Ave N, Minneapolis, MN 55403",
    "233 S Wacker Dr, Chicago, IL 60606",
    "2211 Woodward Ave, Detroit, MI 48201",
    "501 Broadway, Nashville, TN 37203",
    "265 Park Ave W NW, Atlanta, GA 30313",
    "555 S Mint St, Charlotte, NC 28202",
    "601 Biscayne Blvd, Miami, FL 33132",
    "1000 F St NW, Washington, DC 20004",
    "620 8th Ave, New York, NY 10018",
    "100 Legends Way, Boston, MA 02114",
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
