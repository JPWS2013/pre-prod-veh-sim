"""Baseline controller: first come, first served at every decision point."""

from __future__ import annotations

import math
from collections import defaultdict

from ..interface import Actions, Observation


class FifoController:
    """
    * Release: invite the oldest reservations, keeping roughly ``backlog_weeks`` of
      factory output in orders that are waiting for a slot (counting invites that
      are expected to turn into orders).
    * Production: start the oldest orders that are waiting for a slot.
    * Assignment: send each built vehicle to the order it was built for; vehicles
      whose customer cancelled go to the oldest open order with the same config,
      preferring one at the delivery center the vehicle is already at.
    """

    def __init__(self, backlog_weeks: float = 2.0, expected_conversion: float = 0.9,
                 invite_window_days: int = 30) -> None:
        self.backlog_weeks = backlog_weeks
        self.expected_conversion = expected_conversion
        self.invite_window_days = invite_window_days
        self._outstanding: dict[str, int] = {}  # reservation id -> invite day

    def decide(self, obs: Observation) -> Actions:
        # Forget invites that converted, cancelled, or are past the ordering window.
        for o in obs.new_orders:
            self._outstanding.pop(o.reservation_id, None)
        for c in obs.cancellations:
            self._outstanding.pop(c.reservation_id, None)
        self._outstanding = {r: d for r, d in self._outstanding.items()
                             if obs.day - d <= self.invite_window_days}

        open_orders = sorted(obs.open_orders, key=lambda o: (o.placed_day, o.id))
        by_id = {o.id: o for o in open_orders}
        used: set[str] = set()
        assignments: dict[str, str] = {}

        orphans = []
        for v in sorted(obs.available_vehicles, key=lambda v: (v.available_day or 0, v.vin)):
            if v.built_for_order_id in by_id and v.built_for_order_id not in used:
                assignments[v.vin] = v.built_for_order_id
                used.add(v.built_for_order_id)
            else:
                orphans.append(v)

        by_config = defaultdict(list)
        for o in open_orders:
            by_config[o.config].append(o)
        for v in orphans:
            candidates = [o for o in by_config[v.config] if o.id not in used]
            if not candidates:
                continue
            pick = next((o for o in candidates if o.center_id == v.location), candidates[0])
            assignments[v.vin] = pick.id
            used.add(pick.id)

        waiting = [o for o in open_orders if o.status == "ORDERED" and o.id not in used]
        production = [o.id for o in waiting[: obs.production_capacity]]

        # Keep (orders waiting for a slot + orders expected from outstanding invites) at
        # backlog_weeks of factory output, plus enough to cover next week's starts.
        line_rate = obs.factory.line_rate if obs.factory else obs.production_capacity
        target = (self.backlog_weeks + 1) * line_rate
        unscheduled = len(waiting) - len(production)
        expected = self.expected_conversion * len(self._outstanding)
        shortfall = target - unscheduled - expected
        n_release = max(0, math.ceil(shortfall / self.expected_conversion))
        release = obs.pending_reservation_ids[:n_release]
        for rid in release:
            self._outstanding[rid] = obs.day

        return Actions(release_reservation_ids=release, production_order_ids=production,
                       assignments=assignments)
