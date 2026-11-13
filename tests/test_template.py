from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from vehsim import SimConfig, Simulator
from vehsim.cli import main
from vehsim.controllers.template import TemplateController

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "my_controller.py"

FILLED = textwrap.dedent('''
    def init_memory(obs, memory):
        memory["init_calls"] = memory.get("init_calls", 0) + 1
        memory["init_week"] = obs.week
        memory["centers"] = {c.id: c.weekly_capacity for c in obs.centers}
        memory["weeks_seen"] = 0

    def open_centers(obs, memory):
        return [("Omaha", "NE", 60)] if obs.week == 2 else []

    def assign_centers(obs, memory):
        return {r.id: r.nearest_center_id for r in obs.unassigned_reservations}

    def assign_vehicles(obs, memory):
        open_ids = {o.id for o in obs.open_orders}
        return {v.vin: v.built_for_order_id for v in obs.available_vehicles if v.built_for_order_id in open_ids}

    def schedule_production(obs, memory, assignments):
        taken = set(assignments.values())
        waiting = [o.id for o in obs.open_orders if o.status == "ORDERED" and o.id not in taken]
        return waiting[: obs.production_capacity]

    def release_reservations(obs, memory):
        memory["weeks_seen"] += 1
        return obs.pending_reservation_ids[:600]
''')


def test_unfilled_template_runs(capsys):
    assert main(["run", "--controller", str(TEMPLATE), "--weeks", "3"]) == 0
    assert "Simulation summary" in capsys.readouterr().out


def test_filled_controller_delivers(tmp_path):
    path = tmp_path / "ctl.py"
    path.write_text(FILLED)
    ctl = TemplateController.from_file(path)
    sim = Simulator(SimConfig(seed=4, n_weeks=20))
    obs = sim.reset()
    while not sim.done:
        obs = sim.step(ctl.decide(obs))
    assert ctl.memory["weeks_seen"] == 20
    assert ctl.memory["init_calls"] == 1
    assert ctl.memory["init_week"] == 0
    assert len(ctl.memory["centers"]) == 20  # default network, before Omaha opened in week 2
    assert any(c.city == "Omaha" and c.weekly_capacity == 60 for c in sim.centers.values())
    assert sim.report().summary["funnel"]["delivered"] > 0


def test_missing_function_is_reported(tmp_path, capsys):
    path = tmp_path / "ctl.py"
    path.write_text("def assign_centers(obs, memory):\n    return {}\n")
    with pytest.raises(SystemExit, match="missing: assign_vehicles"):
        main(["run", "--controller", str(path), "--weeks", "1"])


def test_invalid_decisions_stop_the_run(tmp_path, capsys):
    path = tmp_path / "ctl.py"
    path.write_text(FILLED.replace('return obs.pending_reservation_ids[:600]', 'return ["R999999"]'))
    assert main(["run", "--controller", str(path), "--weeks", "2"]) == 2
    assert "unknown reservation id" in capsys.readouterr().err


def _first_week(path):
    ctl = TemplateController.from_file(path)
    ctl.decide(Simulator(SimConfig(seed=1, n_weeks=1)).reset())
    return ctl


def test_init_memory_can_return_a_dict(tmp_path):
    path = tmp_path / "ctl.py"
    path.write_text(FILLED.replace('memory["weeks_seen"] = 0', 'return {"weeks_seen": 5}'))
    ctl = _first_week(path)
    assert ctl.memory["weeks_seen"] == 6  # 5 from init, +1 from week 0
    assert ctl.memory["init_calls"] == 1


def test_init_memory_is_optional(tmp_path):
    path = tmp_path / "ctl.py"
    body = "def open_centers" + FILLED.split("def open_centers")[1]
    path.write_text(body.replace('memory["weeks_seen"] += 1', "pass"))
    assert _first_week(path).memory == {}


def test_init_memory_bad_return_is_reported(tmp_path):
    path = tmp_path / "ctl.py"
    path.write_text(FILLED.replace('memory["weeks_seen"] = 0', "return 42"))
    with pytest.raises(TypeError, match="init_memory must return None or a dict"):
        _first_week(path)
