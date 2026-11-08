"""Vehicle configuration options and how customers choose between them."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Mapping

PAINTS = ("white", "black", "green", "blue", "red")
INTERIORS = ("light", "dark")
TRIMS = ("base", "premium", "max")
WHEELS = ('20"', '21"')

ATTRIBUTES = {
    "paint": PAINTS,
    "interior": INTERIORS,
    "trim": TRIMS,
    "wheels": WHEELS,
}


@dataclass(frozen=True, order=True)
class VehicleConfig:
    paint: str
    interior: str
    trim: str
    wheels: str

    def __post_init__(self) -> None:
        for name, options in ATTRIBUTES.items():
            value = getattr(self, name)
            if value not in options:
                raise ValueError(f"invalid {name} {value!r}; expected one of {options}")

    @property
    def code(self) -> str:
        """Short, stable string form, e.g. 'white/dark/premium/21"'."""
        return f"{self.paint}/{self.interior}/{self.trim}/{self.wheels}"

    @classmethod
    def from_code(cls, code: str) -> "VehicleConfig":
        paint, interior, trim, wheels = code.split("/")
        return cls(paint, interior, trim, wheels)

    def as_dict(self) -> dict[str, str]:
        return {"paint": self.paint, "interior": self.interior, "trim": self.trim, "wheels": self.wheels}


def _default_mix() -> dict[str, dict[str, float]]:
    return {
        "paint": {"white": 0.30, "black": 0.25, "green": 0.10, "blue": 0.20, "red": 0.15},
        "interior": {"light": 0.40, "dark": 0.60},
        "trim": {"base": 0.45, "premium": 0.35, "max": 0.20},
        "wheels": {'20"': 0.65, '21"': 0.35},
    }


@dataclass
class ConfigMix:
    """Independent per-attribute choice weights used when a customer places an order."""

    weights: dict[str, dict[str, float]] = field(default_factory=_default_mix)

    def __post_init__(self) -> None:
        for name, options in ATTRIBUTES.items():
            w = self.weights.get(name)
            if not w:
                raise ValueError(f"config mix is missing weights for {name!r}")
            unknown = set(w) - set(options)
            if unknown:
                raise ValueError(f"unknown {name} options in config mix: {sorted(unknown)}")
            if sum(w.values()) <= 0:
                raise ValueError(f"weights for {name!r} must sum to a positive number")

    def sample(self, rng: random.Random) -> VehicleConfig:
        picks = {}
        for name in ATTRIBUTES:
            w: Mapping[str, float] = self.weights[name]
            options = list(w)
            picks[name] = rng.choices(options, weights=[w[o] for o in options], k=1)[0]
        return VehicleConfig(**picks)
