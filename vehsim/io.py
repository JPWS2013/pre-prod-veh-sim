"""CSV/JSON files for driving the simulator without writing Python."""

from __future__ import annotations

import csv
import dataclasses
import json
from pathlib import Path

from .interface import Actions, Observation
from .metrics import write_csv

ACTION_FILES = {
    "release.csv": ["reservation_id"],
    "production.csv": ["order_id"],
    "assignments.csv": ["vin", "order_id"],
    "center_assignments.csv": ["reservation_id", "center"],  # center id (C03) or "City, ST"
    "open_centers.csv": ["city", "state", "capacity"],  # capacity optional
}


def _flatten(view) -> dict:
    row = {}
    for k, v in dataclasses.asdict(view).items():
        if k == "config":
            cfg = getattr(view, "config")
            row["config"] = cfg.code
            row.update(cfg.as_dict())
        else:
            row[k] = v
    return row


def write_observation(obs: Observation, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    tables = {
        "new_reservations": obs.new_reservations,
        "new_orders": obs.new_orders,
        "unassigned_reservations": obs.unassigned_reservations,
        "vehicles_started": obs.vehicles_started,
        "vehicles_produced": obs.vehicles_produced,
        "cancellations": obs.cancellations,
        "deliveries": obs.deliveries,
        "open_orders": obs.open_orders,
        "available_vehicles": obs.available_vehicles,
        "centers": obs.centers,
    }
    for name, views in tables.items():
        rows = [_flatten(v) for v in views]
        path = out_dir / f"{name}.csv"
        if rows:
            write_csv(path, rows)
        else:
            path.write_text("")
    with open(out_dir / "pending_reservations.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["reservation_id"])
        w.writerows([r] for r in obs.pending_reservation_ids)
    status = {
        "week": obs.week,
        "day": obs.day,
        "done": obs.done,
        "production_capacity": obs.production_capacity,
        "factory": dataclasses.asdict(obs.factory) if obs.factory else None,
        "quality_holds_started": obs.quality_holds_started,
        "quality_holds_released": obs.quality_holds_released,
        "rejections": [dataclasses.asdict(r) for r in obs.rejections],
    }
    (out_dir / "status.json").write_text(json.dumps(status, indent=2))


def write_action_templates(actions_dir: Path) -> None:
    actions_dir.mkdir(parents=True, exist_ok=True)
    for name, header in ACTION_FILES.items():
        path = actions_dir / name
        if not path.exists():
            path.write_text(",".join(header) + "\n")


def write_actions(actions: Actions, actions_dir: Path) -> None:
    actions_dir.mkdir(parents=True, exist_ok=True)
    rows = {
        "release.csv": [[r] for r in actions.release_reservation_ids],
        "production.csv": [[o] for o in actions.production_order_ids],
        "assignments.csv": [[v, o] for v, o in actions.assignments.items()],
        "center_assignments.csv": [[r, c] for r, c in actions.center_assignments.items()],
        "open_centers.csv": [list(c) + [""] * (3 - len(c)) for c in actions.open_delivery_centers],
    }
    for name, header in ACTION_FILES.items():
        with open(actions_dir / name, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(rows[name])


def read_actions(actions_dir: Path) -> Actions:
    def rows(name: str) -> list[dict]:
        path = actions_dir / name
        if not path.exists() or not path.read_text().strip():
            return []
        with open(path, newline="") as fh:
            return [r for r in csv.DictReader(fh) if any((v or "").strip() for v in r.values())]

    return Actions(
        release_reservation_ids=[r["reservation_id"].strip() for r in rows("release.csv")],
        production_order_ids=[r["order_id"].strip() for r in rows("production.csv")],
        assignments={r["vin"].strip(): r["order_id"].strip() for r in rows("assignments.csv")},
        center_assignments={r["reservation_id"].strip(): r["center"].strip() for r in rows("center_assignments.csv")},
        open_delivery_centers=[_center_spec(r) for r in rows("open_centers.csv")],
    )


def _center_spec(row: dict) -> tuple:
    cap = (row.get("capacity") or "").strip()
    return (row["city"].strip(), row["state"].strip(), cap) if cap else (row["city"].strip(), row["state"].strip())


__all__ = ["read_actions", "write_action_templates", "write_actions", "write_observation"]
