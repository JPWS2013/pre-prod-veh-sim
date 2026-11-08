from __future__ import annotations

import random
from collections import Counter

import pytest

from vehsim import Actions, SimConfig, SimulationError, Simulator, VehicleConfig
from vehsim.controllers import FifoController
from vehsim.metrics import kaplan_meier_quantiles
from vehsim.models import CustomerStatus, VehicleStatus
from vehsim.processes.conversion import TruncatedNormalDelay
from vehsim.processes.factory import FixedBuildTime, UniformQualityHold


def run(sim: Simulator, controller=None, weeks=None):
    controller = controller or FifoController()
    obs = sim.reset()
    for _ in range(weeks or sim.config.n_weeks):
        if sim.done:
            break
        obs = sim.step(controller.decide(obs))
    return obs


def small_config(**kw) -> SimConfig:
    cfg = SimConfig(n_weeks=kw.pop("n_weeks", 20), seed=kw.pop("seed", 3))
    cfg.demand.base_weekly = 60
    cfg.demand.launch_spike = 300
    cfg.factory.line_rate = 50
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


def advance_until(sim, cond, controller=None, max_weeks=60):
    controller = controller or FifoController()
    obs = sim._observe([]) if sim.week else sim.reset()
    for _ in range(max_weeks):
        if cond(obs):
            return obs
        obs = sim.step(controller.decide(obs))
    raise AssertionError("condition never reached")


# --- defaults --------------------------------------------------------------------


def test_default_setup():
    sim = Simulator()
    assert sim.config.n_weeks == 52
    assert len(sim.centers) == 20
    assert all(c.weekly_capacity >= 10 for c in sim.centers.values())
    assert sim.factory.positions == 500 * 11
    obs = sim.reset()
    assert obs.production_capacity == 500


def test_same_seed_same_results():
    a, b = Simulator(small_config()), Simulator(small_config())
    run(a)
    run(b)
    assert a.report().summary == b.report().summary


def test_full_default_run_is_consistent():
    sim = Simulator(SimConfig(seed=11))
    run(sim)
    rep = sim.report()
    f = rep.summary["funnel"]
    assert f["delivered"] + f["cancelled"] + f["still_waiting"] == f["reservations"]
    assert 0.08 < f["never_order_share"] < 0.12
    st = rep.summary["wait_days"]["invite_to_order"]
    assert 1 <= st["p50"] and st["max"] <= 30
    # Every vehicle is in exactly one place; factory bookkeeping matches vehicle states.
    vs = Counter(v.status for v in sim.vehicles.values())
    assert vs[VehicleStatus.IN_BUILD] == sim.factory.in_build
    assert vs[VehicleStatus.QUALITY_HOLD] == sim.factory.on_hold
    # Delivered orders really have a vehicle with the same config.
    for o in sim.orders.values():
        if o.status == CustomerStatus.DELIVERED:
            assert sim.vehicles[o.vin].config == o.config
            assert sim.vehicles[o.vin].status == VehicleStatus.DELIVERED


# --- conversion --------------------------------------------------------------------


def test_truncated_normal_delay_bounds():
    d = TruncatedNormalDelay(10, 6, 1, 30)
    rng = random.Random(0)
    xs = [d.sample_days(rng) for _ in range(20000)]
    assert min(xs) >= 1 and max(xs) <= 30
    assert 9.5 < sum(xs) / len(xs) < 12


def test_never_order_customers_stay_invited():
    cfg = small_config(n_weeks=12)
    cfg.conversion.never_order_share = 1.0
    cfg.cancellation.base_weekly = {}
    cfg.cancellation.slope_per_week = 0
    sim = Simulator(cfg)
    run(sim)
    assert not sim.orders
    assert all(r.status in (CustomerStatus.INVITED, CustomerStatus.RESERVED) for r in sim.reservations.values())


# --- factory -------------------------------------------------------------------------


def test_slots_full_until_positions_fill_then_track_completions():
    sim = Simulator(SimConfig(seed=5, n_weeks=40))
    obs = sim.reset()
    ctl = FifoController()
    caps = []
    while not sim.done:
        caps.append(obs.production_capacity)
        obs = sim.step(ctl.decide(obs))
    assert caps[:8] == [500] * 8
    assert min(caps[14:]) < 500  # build-time spread + holds reduce slots once the factory is full
    assert all(c <= 500 for c in caps)
    assert sim.factory.occupied <= sim.factory.positions


def test_production_over_capacity_is_rejected():
    sim = Simulator(small_config())
    obs = advance_until(sim, lambda o: len([x for x in o.open_orders if x.status == "ORDERED"]) > 3)
    ordered = [o.id for o in obs.open_orders if o.status == "ORDERED"]
    sim.factory.line_rate = 2  # shrink capacity so we can exceed it
    with pytest.raises(SimulationError, match="production slots"):
        sim.step(Actions(production_order_ids=ordered[:3]))
    # nothing changed
    assert all(sim.orders[i].status == CustomerStatus.ORDERED for i in ordered)


def test_lenient_mode_truncates_and_reports():
    cfg = small_config(strict=False)
    sim = Simulator(cfg)
    obs = advance_until(sim, lambda o: len([x for x in o.open_orders if x.status == "ORDERED"]) > 3)
    ordered = [o.id for o in obs.open_orders if o.status == "ORDERED"]
    sim.factory.line_rate = 2
    obs = sim.step(Actions(production_order_ids=ordered[:3] + ["O999999"]))
    assert len(obs.vehicles_started) == 2
    assert {r.kind for r in obs.rejections} == {"production"}


def test_quality_hold_occupies_position_then_releases():
    cfg = small_config(n_weeks=10)
    sim = Simulator(cfg, build_time=FixedBuildTime(7), quality=UniformQualityHold(1.0, 1, 1))
    obs = advance_until(sim, lambda o: any(x.status == "ORDERED" for x in o.open_orders))
    oid = next(o.id for o in obs.open_orders if o.status == "ORDERED")
    obs = sim.step(Actions(production_order_ids=[oid]))
    vin = obs.vehicles_started[0].vin
    obs = sim.step()
    assert vin in obs.quality_holds_started
    assert sim.vehicles[vin].status == VehicleStatus.QUALITY_HOLD
    assert sim.factory.on_hold == 1
    obs = sim.step()
    assert vin in obs.quality_holds_released
    assert sim.vehicles[vin].status == VehicleStatus.AVAILABLE
    assert sim.factory.occupied == 0


# --- assignment ------------------------------------------------------------------------


def _built_vehicle(sim):
    """Run a small sim with fast builds until a vehicle is AVAILABLE; return (obs, vehicle view)."""
    obs = advance_until(sim, lambda o: bool(o.vehicles_produced), controller=_NoAssign())
    return obs, obs.vehicles_produced[0]


class _NoAssign(FifoController):
    def decide(self, obs):
        a = super().decide(obs)
        return Actions(release_reservation_ids=a.release_reservation_ids, production_order_ids=a.production_order_ids,
                       center_assignments=a.center_assignments)


def test_assignment_requires_matching_config():
    sim = Simulator(small_config(), build_time=FixedBuildTime(7), quality=UniformQualityHold(0, 1, 1))
    obs, v = _built_vehicle(sim)
    other = next((o for o in obs.open_orders if o.config != v.config), None)
    if other is None:  # force a mismatch
        other = obs.open_orders[0]
        sim.orders[other.id].config = VehicleConfig("red", "light", "max", '21"') \
            if v.config.paint != "red" else VehicleConfig("white", "light", "max", '21"')
    with pytest.raises(SimulationError, match="does not match"):
        sim.step(Actions(assignments={v.vin: other.id}))


def test_assignment_ships_and_delivers():
    sim = Simulator(small_config(), build_time=FixedBuildTime(7), quality=UniformQualityHold(0, 1, 1))
    obs, v = _built_vehicle(sim)
    oid = v.built_for_order_id
    obs = sim.step(Actions(assignments={v.vin: oid}))
    assert sim.orders[oid].status in (CustomerStatus.IN_TRANSIT, CustomerStatus.AT_CENTER, CustomerStatus.DELIVERED)
    for _ in range(12):
        if sim.orders[oid].status == CustomerStatus.DELIVERED:
            break
        obs = sim.step()
    assert sim.orders[oid].status == CustomerStatus.DELIVERED
    assert sim.vehicles[v.vin].location == sim.orders[oid].center_id


def test_reassigning_a_vehicle_returns_original_order_to_ordered():
    sim = Simulator(small_config(), build_time=FixedBuildTime(7), quality=UniformQualityHold(0, 1, 1))
    obs, v = _built_vehicle(sim)
    other = next((o for o in obs.open_orders if o.config == v.config and o.id != v.built_for_order_id), None)
    if other is None:
        other = next(o for o in obs.open_orders if o.id != v.built_for_order_id)
        sim.orders[other.id].config = v.config
    original = v.built_for_order_id
    sim.step(Actions(assignments={v.vin: other.id}))
    assert sim.orders[original].status == CustomerStatus.ORDERED
    assert sim.orders[original].production_vin is None


# --- cancellation -------------------------------------------------------------------------


def test_cancel_frees_vehicle_for_reassignment():
    sim = Simulator(small_config(), build_time=FixedBuildTime(7), quality=UniformQualityHold(0, 1, 1))
    obs, v = _built_vehicle(sim)
    oid = v.built_for_order_id
    rid = sim.orders[oid].reservation_id
    sim.step(Actions(assignments={v.vin: oid}))
    view = sim.cancel(rid)
    assert view.freed_vin == v.vin
    assert sim.orders[oid].status == CustomerStatus.CANCELLED
    assert sim.vehicles[v.vin].order_id is None
    for _ in range(10):  # let it arrive at the center as unassigned stock
        obs = sim.step()
        if sim.vehicles[v.vin].status == VehicleStatus.AVAILABLE:
            break
    assert sim.vehicles[v.vin].status == VehicleStatus.AVAILABLE
    assert any(x.vin == v.vin for x in obs.available_vehicles)


def test_cancelled_reservation_cannot_be_released():
    sim = Simulator(small_config())
    obs = sim.step()
    rid = obs.pending_reservation_ids[0]
    sim.cancel(rid)
    with pytest.raises(SimulationError, match="CANCELLED"):
        sim.step(Actions(release_reservation_ids=[rid]))


# --- delivery centers ---------------------------------------------------------------------


def test_new_reservations_have_no_center_until_controller_assigns():
    sim = Simulator(small_config())
    obs = sim.step()
    assert obs.new_reservations and all(r.center_id is None for r in obs.new_reservations)
    assert {r.id for r in obs.unassigned_reservations} == {r.id for r in obs.new_reservations}
    r = obs.new_reservations[0]
    obs = sim.step(Actions(center_assignments={r.id: r.nearest_center_id}))
    assert sim.reservations[r.id].center_id == r.nearest_center_id
    assert r.id not in {x.id for x in obs.unassigned_reservations}


def test_center_assignment_is_final():
    sim = Simulator(small_config())
    obs = sim.step()
    rid = obs.new_reservations[0].id
    sim.step(Actions(center_assignments={rid: "Chicago, IL"}))
    chicago = sim.reservations[rid].center_id
    assert sim.centers[chicago].city == "Chicago"
    with pytest.raises(SimulationError, match="final"):
        sim.step(Actions(center_assignments={rid: "Boston, MA"}))
    assert sim.reservations[rid].center_id == chicago


def test_unknown_center_is_rejected():
    sim = Simulator(small_config())
    obs = sim.step()
    with pytest.raises(SimulationError, match="unknown delivery center"):
        sim.step(Actions(center_assignments={obs.new_reservations[0].id: "Nowhere, ZZ"}))


def test_vehicle_assignment_needs_a_center():
    sim = Simulator(small_config(), build_time=FixedBuildTime(7), quality=UniformQualityHold(0, 1, 1))

    class NoCenters(_NoAssign):
        def decide(self, obs):
            a = super().decide(obs)
            return Actions(release_reservation_ids=a.release_reservation_ids,
                           production_order_ids=a.production_order_ids)

    obs = advance_until(sim, lambda o: bool(o.vehicles_produced), controller=NoCenters())
    v = obs.vehicles_produced[0]
    oid = v.built_for_order_id
    with pytest.raises(SimulationError, match="no delivery center"):
        sim.step(Actions(assignments={v.vin: oid}))
    # Assigning the center in the same week makes it valid.
    rid = sim.orders[oid].reservation_id
    sim.step(Actions(assignments={v.vin: oid}, center_assignments={rid: "Denver, CO"}))
    assert sim.orders[oid].center_id == sim.reservations[rid].center_id
    assert sim.vehicles[v.vin].location == sim.orders[oid].center_id


def test_open_center_with_capacity_and_assign_same_week():
    cfg = small_config()
    sim = Simulator(cfg)
    obs = sim.step()
    rid = obs.new_reservations[0].id
    obs = sim.step(Actions(open_delivery_centers=[("Kansas City", "MO", 120), ("Omaha", "NE")],
                           center_assignments={rid: "Kansas City, MO"}))
    kc = next(c for c in obs.centers if c.city == "Kansas City")
    omaha = next(c for c in obs.centers if c.city == "Omaha")
    assert kc.weekly_capacity == 120
    assert omaha.weekly_capacity == cfg.logistics.new_center_capacity
    assert sim.reservations[rid].center_id == kc.id
    # Opening a center never moves customers who already have one.
    assigned_before = {r.id: r.center_id for r in sim.reservations.values() if r.center_id}
    sim.step(Actions(open_delivery_centers=[("Wichita", "KS", 40)]))
    assert all(sim.reservations[i].center_id == c for i, c in assigned_before.items())


@pytest.mark.parametrize("cap", [0, -5, "lots"])
def test_bad_center_capacity_is_rejected(cap):
    sim = Simulator(small_config())
    with pytest.raises(SimulationError, match="capacity"):
        sim.step(Actions(open_delivery_centers=[("Wichita", "KS", cap)]))


def test_duplicate_center_is_rejected():
    sim = Simulator(small_config())
    with pytest.raises(SimulationError, match="already exists"):
        sim.step(Actions(open_delivery_centers=[("Chicago", "IL")]))


# --- metrics -------------------------------------------------------------------------------


def test_kaplan_meier_handles_censoring():
    # 4 delivered at 10, 20, 30, 40; 4 censored at 5 (cancelled early) -> median among survivors is 20
    durs = [(10, True), (20, True), (30, True), (40, True)] + [(5, False)] * 4
    km = kaplan_meier_quantiles(durs)
    assert km["p50"] == 20
    assert kaplan_meier_quantiles([(10, True), (50, False), (50, False)])["p50"] is None


def test_report_writes_files(tmp_path):
    sim = Simulator(small_config(n_weeks=8))
    run(sim)
    out = sim.report().write(tmp_path)
    for name in ("summary.json", "report.txt", "customers.csv", "weekly.csv", "by_center.csv",
                 "by_cohort.csv", "by_config.csv"):
        assert (out / name).exists()
