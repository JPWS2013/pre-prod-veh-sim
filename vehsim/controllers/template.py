"""Adapter that turns a user's controller file (see my_controller.py) into a controller."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from ..interface import Actions, Observation

REQUIRED = ("assign_centers", "assign_vehicles", "schedule_production", "release_reservations")
OPTIONAL = ("open_centers",)


def load_module(path: str | Path) -> ModuleType:
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"controller file not found: {path}")
    name = f"vehsim_user_controller_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path} as a Python module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class TemplateController:
    """Calls the user's five functions each week and bundles the results into Actions."""

    def __init__(self, module: ModuleType) -> None:
        missing = [f for f in REQUIRED if not callable(getattr(module, f, None))]
        if missing:
            raise AttributeError(f"{getattr(module, '__file__', module)} is missing: {', '.join(missing)}")
        self.module = module
        self.memory: dict = {}

    @classmethod
    def from_file(cls, path: str | Path) -> "TemplateController":
        return cls(load_module(path))

    def decide(self, obs: Observation) -> Actions:
        m, mem = self.module, self.memory
        opened = m.open_centers(obs, mem) if callable(getattr(m, "open_centers", None)) else []
        centers = m.assign_centers(obs, mem)
        assignments = m.assign_vehicles(obs, mem)
        production = m.schedule_production(obs, mem, dict(assignments or {}))
        release = m.release_reservations(obs, mem)
        return Actions(
            release_reservation_ids=list(release or []),
            production_order_ids=list(production or []),
            assignments=dict(assignments or {}),
            center_assignments=dict(centers or {}),
            open_delivery_centers=[tuple(c) for c in (opened or [])],
        )
