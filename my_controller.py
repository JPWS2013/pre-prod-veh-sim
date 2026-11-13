"""Your controller. Fill in the five functions below, then run:

    uv run vehsim run --controller my_controller.py

Before the first week, the simulator calls init_memory (optional) once so you
can set up memory. Then every week the simulator calls these functions in this order:

    1. open_centers          (optional) open new delivery centers
    2. assign_centers        pick a delivery center for each new reservation
    3. assign_vehicles       match built vehicles to orders
    4. schedule_production   choose which orders get this week's production slots
    5. release_reservations  invite reservations to place an order

Each function receives:

    obs     what the simulator reports this week (see "What's in obs" below)
    memory  a dict that persists across weeks; store anything you want to remember

If a function returns something invalid (e.g. more production orders than slots,
a vehicle whose config doesn't match the order, changing a reservation's center),
the run stops with an error listing every problem.

What's in obs
-------------
obs.week, obs.day                 week index (0-51) and its first day
obs.production_capacity           production slots available this week
obs.new_reservations              reservations created last week
obs.unassigned_reservations       active reservations without a delivery center
    each has: .id .created_day .street .city .state .center_id .nearest_center_id
obs.pending_reservation_ids       reservation ids not yet invited, oldest first
obs.new_orders                    orders placed last week
obs.open_orders                   orders without a vehicle (status "ORDERED" or "IN_PRODUCTION")
    each has: .id .reservation_id .placed_day .config .center_id .status .production_vin
obs.available_vehicles            built vehicles with no customer
obs.vehicles_produced             vehicles that passed quality last week
obs.vehicles_started              builds started last week
    each has: .vin .config .built_for_order_id .status .location .start_day
              .expected_done_day .available_day
    .location is "FACTORY" or a center id
obs.cancellations                 .reservation_id .order_id .day .status_at_cancel .freed_vin
obs.deliveries                    .order_id .reservation_id .vin .center_id .day
obs.centers                       .id .city .state .weekly_capacity .queue_length .opened_day
obs.factory                       .line_rate .positions .in_build .on_hold .free_positions

A vehicle config (.config) has .paint .interior .trim .wheels; two configs match
when they are equal (vehicle.config == order.config).
"""

from __future__ import annotations

from vehsim import Observation


def init_memory(memory: dict) -> None:
    """Set up memory before the first week (optional; called once per run).

    ``memory`` starts empty. Add any starting values, counters or lookup tables
    your other functions expect, so they don't need memory.get(...) defaults.
    This same dict is then passed to every function, every week.
    You can also return a dict instead; its keys are added to memory.

    Example:
        memory["total_invited"] = 0
        memory["invite_multiplier"] = 2.0
    """
    # TODO: add any starting values your controller needs.


def open_centers(obs: Observation, memory: dict) -> list[tuple]:
    """Open new delivery centers (optional).

    Return a list of (city, state) or (city, state, weekly_handover_capacity),
    e.g. [("Omaha", "NE", 75)]. Without a capacity a center gets 25/week.
    You can assign reservations to a center in the same week you open it by
    using its name ("Omaha, NE") in assign_centers.

    Return [] to keep the current network.
    """
    # TODO: decide whether to open centers (e.g. when obs.centers[i].queue_length grows).
    return []


def assign_centers(obs: Observation, memory: dict) -> dict[str, str]:
    """Choose the delivery center for reservations that don't have one yet.

    Return {reservation_id: center}, where center is a center id ("C04") or a
    name ("Chicago, IL"). Assignments are final: a reservation's center can
    never be changed. A customer's order cannot receive a vehicle until their
    reservation has a center.

    Candidates: obs.unassigned_reservations (each has a .nearest_center_id hint).
    Example: return {r.id: r.nearest_center_id for r in obs.unassigned_reservations}
    """
    # TODO: pick a center for each reservation in obs.unassigned_reservations.
    return {}


def assign_vehicles(obs: Observation, memory: dict) -> dict[str, str]:
    """Match built vehicles to orders.

    Return {vin: order_id}. Rules:
      * the vehicle must be in obs.available_vehicles
      * the order must be in obs.open_orders, at most one vehicle per order
      * vehicle.config must equal order.config
      * the order's reservation must have a delivery center
    The vehicle ships to the order's delivery center straight away.

    Usually a vehicle goes to the order it was built for (.built_for_order_id),
    but vehicles whose customer cancelled need a new home.
    """
    # TODO: match obs.available_vehicles to obs.open_orders.
    return {}


def schedule_production(obs: Observation, memory: dict, assignments: dict[str, str]) -> list[str]:
    """Choose which orders start production this week.

    Return a list of order ids, at most obs.production_capacity of them. Each
    must be an order in obs.open_orders with status "ORDERED" (not already
    being built). A vehicle with that order's config starts building and takes
    8-14 weeks.

    ``assignments`` is what assign_vehicles returned this week, so you can skip
    orders that are already getting a vehicle.
    """
    # TODO: pick up to obs.production_capacity orders with status "ORDERED".
    return []


def release_reservations(obs: Observation, memory: dict) -> list[str]:
    """Invite reservations to place an order.

    Return a list of reservation ids from obs.pending_reservation_ids. About
    90% of invited customers order 1-30 days later; the rest never order.
    Inviting too few starves the factory; inviting too many makes customers
    wait longer after ordering.
    """
    # TODO: pick reservations from obs.pending_reservation_ids.
    return []
