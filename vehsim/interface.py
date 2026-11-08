"""The Simulator: the public step-by-step interface between the simulation and a controller.

Each call to :meth:`Simulator.step` takes the controller's decisions for the coming
week, applies them on the first day of that week, simulates the seven days, and
returns an :class:`Observation` describing what happened.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from .catalog import VehicleConfig
from .config import SimConfig
from .engine import EventQueue, Phase
from .geo import CITIES, CenterResolver, Geo, NearestCenterResolver
from .models import (
    FACTORY,
    Address,
    MATCHABLE_ORDER_STATUSES,
    CustomerStatus,
    DeliveryCenter,
    Order,
    Reservation,
    Vehicle,
    VehicleStatus,
)
from .processes import (
    AddressGenerator,
    BuildTimeModel,
    CancellationModel,
    ConversionModel,
    DemandModel,
    Factory,
    QualityModel,
    TransitModel,
)
from .processes.conversion import OrderDelayModel

ACTIVE_STATUSES = {
    CustomerStatus.RESERVED,
    CustomerStatus.INVITED,
    CustomerStatus.ORDERED,
    CustomerStatus.IN_PRODUCTION,
    CustomerStatus.IN_TRANSIT,
    CustomerStatus.AT_CENTER,
}


def _split_ref(ref: str) -> tuple[str, str]:
    """'Chicago, IL' -> ('Chicago', 'IL'); anything without a comma -> (ref, '')."""
    city, _, state = str(ref).rpartition(",")
    return (city, state) if city else (str(ref), "")


def _center_key(city: str, state: str) -> tuple[str, str]:
    return (" ".join(city.split()).lower(), state.strip().upper())


class SimulationError(Exception):
    """Raised when the controller submits invalid actions (in strict mode)."""

    def __init__(self, rejections: list["Rejection"]) -> None:
        self.rejections = rejections
        lines = [f"  {r.kind} {r.item}: {r.reason}" for r in rejections[:20]]
        more = f"\n  ... and {len(rejections) - 20} more" if len(rejections) > 20 else ""
        super().__init__(f"{len(rejections)} invalid action(s):\n" + "\n".join(lines) + more)


# ---------------------------------------------------------------------------
# Controller-facing records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Actions:
    """Decisions for the coming week."""

    release_reservation_ids: list[str] = field(default_factory=list)
    production_order_ids: list[str] = field(default_factory=list)
    assignments: dict[str, str] = field(default_factory=dict)  # vin -> order_id
    # reservation_id -> center id ("C03") or name ("Chicago, IL"). Final once applied.
    center_assignments: dict[str, str] = field(default_factory=dict)
    # (city, state) or (city, state, weekly_handover_capacity)
    open_delivery_centers: list[tuple] = field(default_factory=list)


@dataclass(frozen=True)
class Rejection:
    kind: str  # release | production | assignment | center_assignment | open_center
    item: str
    reason: str


@dataclass(frozen=True)
class ReservationView:
    id: str
    created_day: int
    street: str
    city: str
    state: str
    center_id: Optional[str]  # None until the controller assigns one
    nearest_center_id: str  # hint: closest open center to the address


@dataclass(frozen=True)
class OrderView:
    id: str
    reservation_id: str
    placed_day: int
    config: VehicleConfig
    center_id: Optional[str]
    status: str
    production_vin: Optional[str]


@dataclass(frozen=True)
class VehicleView:
    vin: str
    config: VehicleConfig
    built_for_order_id: Optional[str]
    status: str
    location: str
    start_day: int
    expected_done_day: int
    available_day: Optional[int]


@dataclass(frozen=True)
class CancellationView:
    reservation_id: str
    order_id: Optional[str]
    day: int
    status_at_cancel: str
    freed_vin: Optional[str]  # vehicle that lost its customer (built or still building)


@dataclass(frozen=True)
class DeliveryView:
    order_id: str
    reservation_id: str
    vin: str
    center_id: str
    day: int


@dataclass(frozen=True)
class CenterView:
    id: str
    city: str
    state: str
    weekly_capacity: int
    queue_length: int
    opened_day: int


@dataclass(frozen=True)
class FactoryView:
    line_rate: int
    positions: int
    in_build: int
    on_hold: int
    free_positions: int


@dataclass
class Observation:
    week: int  # number of weeks simulated so far; decisions you return apply to this week index
    day: int  # first day of the coming week
    done: bool
    production_capacity: int  # max production_order_ids you may submit for the coming week
    new_reservations: list[ReservationView] = field(default_factory=list)
    new_orders: list[OrderView] = field(default_factory=list)
    vehicles_started: list[VehicleView] = field(default_factory=list)
    vehicles_produced: list[VehicleView] = field(default_factory=list)  # passed quality this week
    quality_holds_started: list[str] = field(default_factory=list)
    quality_holds_released: list[str] = field(default_factory=list)
    cancellations: list[CancellationView] = field(default_factory=list)
    deliveries: list[DeliveryView] = field(default_factory=list)
    pending_reservation_ids: list[str] = field(default_factory=list)  # RESERVED, not yet invited
    unassigned_reservations: list[ReservationView] = field(default_factory=list)  # active, no center yet
    open_orders: list[OrderView] = field(default_factory=list)  # no vehicle assigned yet
    available_vehicles: list[VehicleView] = field(default_factory=list)  # built, unassigned
    centers: list[CenterView] = field(default_factory=list)
    factory: Optional[FactoryView] = None
    rejections: list[Rejection] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------


class Simulator:
    def __init__(
        self,
        config: Optional[SimConfig] = None,
        *,
        seed: Optional[int] = None,
        geo: Optional[Geo] = None,
        resolver: Optional[CenterResolver] = None,
        build_time: Optional[BuildTimeModel] = None,
        quality: Optional[QualityModel] = None,
        order_delay: Optional[OrderDelayModel] = None,
    ) -> None:
        self.config = config or SimConfig()
        if seed is not None:
            self.config.seed = seed
        self.geo = geo or Geo()
        self.resolver = resolver or NearestCenterResolver(self.geo)
        self._overrides = {"build_time": build_time, "quality": quality, "order_delay": order_delay}
        self.reset()

    # -- lifecycle -----------------------------------------------------------

    def reset(self) -> Observation:
        cfg = self.config
        # Independent random streams per process so that different controllers see the
        # same customers, build times etc. for the same seed (common random numbers).
        master = random.Random(cfg.seed)
        self._rng = {name: random.Random(master.getrandbits(64)) for name in
                     ("demand", "address", "conversion", "config", "build", "quality", "cancel")}

        self.demand = DemandModel(cfg.demand)
        self.addresses = AddressGenerator()
        self.conversion = ConversionModel(cfg.conversion, self._overrides["order_delay"])
        self.factory = Factory(cfg.factory, self._overrides["build_time"], self._overrides["quality"])
        self.transit = TransitModel(cfg.logistics)
        self.cancellation = CancellationModel(cfg.cancellation)
        self.factory_coords = self.geo.coords(cfg.factory.city, cfg.factory.state)

        self.events = EventQueue()
        self.week = 0
        self.reservations: dict[str, Reservation] = {}
        self.orders: dict[str, Order] = {}
        self.vehicles: dict[str, Vehicle] = {}
        self.centers: dict[str, DeliveryCenter] = {}
        self._active: dict[str, None] = {}  # insertion-ordered set of active reservation ids
        self._counters = Counter()
        self.weekly: list[dict] = []  # per-week time series for reporting
        self.slots_offered: list[int] = []

        self._init_default_centers()
        self._buf = self._new_buffers()
        return self._observe([])

    @property
    def done(self) -> bool:
        return self.week >= self.config.n_weeks

    @property
    def day(self) -> int:
        return self.week * 7

    def _next_id(self, prefix: str, width: int = 6) -> str:
        self._counters[prefix] += 1
        return f"{prefix}{self._counters[prefix]:0{width}d}"

    # -- delivery centers ------------------------------------------------------

    def _init_default_centers(self) -> None:
        lg = self.config.logistics
        for city, state in self.config.centers:
            self._add_center(city, state, capacity=0, day=0)
        # Split total handover capacity by the market share each center serves.
        share = Counter()
        for city, state, lat, lon, w in CITIES:
            cid = self.resolver.resolve(Address("", city, state), self.centers.values())
            share[cid] += w
        total = sum(share.values()) or 1.0
        for c in self.centers.values():
            c.weekly_capacity = max(lg.min_center_capacity, round(lg.total_handover_capacity * share[c.id] / total))

    def _add_center(self, city: str, state: str, capacity: int, day: int) -> DeliveryCenter:
        lat, lon = self.geo.coords(city, state)
        cid = self._next_id("C", 2)
        center = DeliveryCenter(id=cid, city=city, state=state.upper(), lat=lat, lon=lon,
                                weekly_capacity=capacity, opened_day=day)
        self.centers[cid] = center
        return center

    def _center_coords(self, location: str) -> tuple[float, float]:
        if location == FACTORY:
            return self.factory_coords
        c = self.centers[location]
        return (c.lat, c.lon)

    # -- stepping --------------------------------------------------------------

    def step(self, actions: Optional[Actions] = None) -> Observation:
        if self.done:
            raise RuntimeError("simulation is finished; call reset() to start again")
        actions = actions or Actions()
        self._buf = self._new_buffers()
        w, d0 = self.week, self.day
        capacity = self.factory.slots_available()
        self.slots_offered.append(capacity)

        plan, rejections = self._validate(actions, capacity)
        if rejections and self.config.strict:
            raise SimulationError(rejections)

        for city, state, cap in plan["open"]:
            self._open_center(city, state, cap, d0)
        for rid, ref in plan["centers"]:
            self._assign_center(self.reservations[rid], self._center_by_ref(ref), d0)
        for rid in plan["release"]:
            self._invite(self.reservations[rid], d0)
        for oid in plan["production"]:
            self._start_build(self.orders[oid], d0)
        for vin, oid in plan["assign"]:
            self._assign(self.vehicles[vin], self.orders[oid], d0)

        for day in self.demand.arrivals(w, self._rng["demand"]):
            self.events.schedule(day, Phase.RESERVATION_ARRIVES)

        for day in range(d0, d0 + 7):
            for ev in self.events.pop_day(day):
                self._handle(ev.phase, ev.day, ev.data)
            self._handovers(day)
        self._cancellations(d0 + 6)

        self._record_week(w, capacity, plan)
        self.week += 1
        return self._observe(rejections)

    # -- action validation -------------------------------------------------------

    def _validate(self, a: Actions, capacity: int):
        rej: list[Rejection] = []
        plan = {"open": [], "centers": [], "release": [], "production": [], "assign": []}

        existing = {_center_key(c.city, c.state) for c in self.centers.values()}
        new_names = set()
        for spec in a.open_delivery_centers:
            label = ", ".join(str(x) for x in spec)
            if len(spec) not in (2, 3):
                rej.append(Rejection("open_center", label, "expected (city, state) or (city, state, capacity)"))
                continue
            city, state = str(spec[0]).strip(), str(spec[1]).strip().upper()
            cap = spec[2] if len(spec) == 3 and spec[2] not in (None, "") else self.config.logistics.new_center_capacity
            try:
                cap = int(cap)
            except (TypeError, ValueError):
                cap = -1
            key = _center_key(city, state)
            if key in existing:
                rej.append(Rejection("open_center", label, "a delivery center already exists there"))
                continue
            if cap <= 0:
                rej.append(Rejection("open_center", label, "capacity must be a positive whole number per week"))
                continue
            try:
                self.geo.coords(city, state)
            except ValueError as e:
                rej.append(Rejection("open_center", label, str(e)))
                continue
            existing.add(key)
            new_names.add(key)
            plan["open"].append((city, state, cap))

        centered = set()
        for rid, ref in a.center_assignments.items():
            r = self.reservations.get(rid)
            target = self._center_by_ref(ref)
            if r is None:
                rej.append(Rejection("center_assignment", rid, "unknown reservation id"))
            elif r.status not in ACTIVE_STATUSES:
                rej.append(Rejection("center_assignment", rid, f"reservation is {r.status.value}"))
            elif r.center_id is not None:
                rej.append(Rejection("center_assignment", rid,
                                     f"already assigned to {r.center_id}; center assignments are final"))
            elif target is None and _center_key(*_split_ref(ref)) not in new_names:
                rej.append(Rejection("center_assignment", rid, f"unknown delivery center {ref!r}"))
            else:
                plan["centers"].append((rid, ref))
                centered.add(rid)

        seen = set()
        for rid in a.release_reservation_ids:
            r = self.reservations.get(rid)
            if rid in seen:
                rej.append(Rejection("release", rid, "listed more than once"))
            elif r is None:
                rej.append(Rejection("release", rid, "unknown reservation id"))
            elif r.status != CustomerStatus.RESERVED:
                rej.append(Rejection("release", rid, f"reservation is {r.status.value}, not RESERVED"))
            else:
                plan["release"].append(rid)
            seen.add(rid)

        if len(a.production_order_ids) > capacity:
            rej.append(Rejection("production", f"{len(a.production_order_ids)} orders",
                                 f"only {capacity} production slots are available this week"))
        seen = set()
        for oid in a.production_order_ids:
            o = self.orders.get(oid)
            if oid in seen:
                rej.append(Rejection("production", oid, "listed more than once"))
            elif o is None:
                rej.append(Rejection("production", oid, "unknown order id"))
            elif o.status != CustomerStatus.ORDERED:
                rej.append(Rejection("production", oid, f"order is {o.status.value}, not ORDERED"))
            elif len(plan["production"]) >= capacity:
                rej.append(Rejection("production", oid, "over this week's production capacity"))
            else:
                plan["production"].append(oid)
            seen.add(oid)

        used_orders = set()
        for vin, oid in a.assignments.items():
            v, o = self.vehicles.get(vin), self.orders.get(oid)
            if v is None:
                rej.append(Rejection("assignment", vin, "unknown VIN"))
            elif v.status != VehicleStatus.AVAILABLE:
                rej.append(Rejection("assignment", vin, f"vehicle is {v.status.value}, not AVAILABLE"))
            elif o is None:
                rej.append(Rejection("assignment", vin, f"unknown order id {oid}"))
            elif o.status not in MATCHABLE_ORDER_STATUSES:
                rej.append(Rejection("assignment", vin, f"order {oid} is {o.status.value} and cannot take a vehicle"))
            elif oid in used_orders:
                rej.append(Rejection("assignment", vin, f"order {oid} is assigned more than one vehicle"))
            elif v.config != o.config:
                rej.append(Rejection("assignment", vin,
                                     f"config {v.config.code} does not match order {oid} config {o.config.code}"))
            elif o.center_id is None and o.reservation_id not in centered:
                rej.append(Rejection("assignment", vin, f"order {oid} has no delivery center; "
                                                        f"assign reservation {o.reservation_id} to a center first"))
            else:
                plan["assign"].append((vin, oid))
            if o is not None:
                used_orders.add(oid)
        return plan, rej

    # -- state transitions ----------------------------------------------------

    def _set_status(self, r: Reservation, status: CustomerStatus) -> None:
        r.status = status
        if r.order_id:
            self.orders[r.order_id].status = status
        if status in (CustomerStatus.DELIVERED, CustomerStatus.CANCELLED):
            self._active.pop(r.id, None)

    def _open_center(self, city: str, state: str, capacity: int, day: int) -> None:
        center = self._add_center(city, state, capacity, day)
        self._buf["opened"].append(center.id)

    def _center_by_ref(self, ref: str) -> Optional[str]:
        """Center id from an id ("C03") or a name ("Chicago, IL"); None if unknown."""
        if ref in self.centers:
            return ref
        key = _center_key(*_split_ref(ref))
        for c in self.centers.values():
            if _center_key(c.city, c.state) == key:
                return c.id
        return None

    def _assign_center(self, r: Reservation, center_id: str, day: int) -> None:
        r.center_id, r.center_assigned_day = center_id, day
        if r.order_id:
            self.orders[r.order_id].center_id = center_id

    def _nearest_center(self, r: Reservation) -> str:
        """Closest open center to the customer (cached until a new center opens)."""
        if r.nearest_hint is None or r.nearest_hint[0] != len(self.centers):
            r.nearest_hint = (len(self.centers), self.resolver.resolve(r.address, self.centers.values()))
        return r.nearest_hint[1]

    def _invite(self, r: Reservation, day: int) -> None:
        r.invited_day = day
        self._set_status(r, CustomerStatus.INVITED)
        delay = self.conversion.on_invite(self._rng["conversion"])
        r.will_order = delay is not None
        if delay is not None:
            self.events.schedule(day + delay, Phase.ORDER_PLACED, rid=r.id)

    def _start_build(self, o: Order, day: int) -> None:
        vin = self._next_id("V", 7)
        v = Vehicle(vin=vin, config=o.config, built_for_order_id=o.id, start_day=day,
                    build_days=self.factory.build_time.sample_days(self._rng["build"]))
        self.vehicles[vin] = v
        self.factory.start()
        o.production_vin, o.build_start_day = vin, day
        self._set_status(self.reservations[o.reservation_id], CustomerStatus.IN_PRODUCTION)
        self.events.schedule(v.expected_done_day, Phase.BUILD_COMPLETE, vin=vin)
        self._buf["started"].append(vin)

    def _assign(self, v: Vehicle, o: Order, day: int) -> None:
        # If this vehicle was being counted on by another open order, that order goes
        # back to ORDERED so it can be scheduled again or matched to something else.
        prev = self.orders.get(v.built_for_order_id) if v.built_for_order_id else None
        if prev is not None and prev is not o and prev.production_vin == v.vin \
                and prev.status == CustomerStatus.IN_PRODUCTION:
            prev.production_vin, prev.build_start_day = None, None
            self._set_status(self.reservations[prev.reservation_id], CustomerStatus.ORDERED)

        v.order_id, o.vin, o.matched_day = o.id, v.vin, day
        r = self.reservations[o.reservation_id]
        if v.location == o.center_id:
            self._enqueue_handover(v, o, day)
        else:
            transit = self.transit.days(self._center_coords(v.location), self._center_coords(o.center_id))
            v.status, v.location, v.ship_day = VehicleStatus.IN_TRANSIT, o.center_id, day
            self._set_status(r, CustomerStatus.IN_TRANSIT)
            self.events.schedule(day + transit, Phase.VEHICLE_ARRIVES, vin=v.vin)

    def _enqueue_handover(self, v: Vehicle, o: Order, day: int) -> None:
        v.status = VehicleStatus.AT_CENTER
        if v.arrive_day is None or v.location != o.center_id:
            v.arrive_day = day
        v.location = o.center_id
        self.centers[o.center_id].queue.append((day + self.config.logistics.handover_prep_days, v.vin))
        self._set_status(self.reservations[o.reservation_id], CustomerStatus.AT_CENTER)

    def _make_available(self, v: Vehicle, day: int) -> None:
        v.status, v.available_day = VehicleStatus.AVAILABLE, day
        self._buf["produced"].append(v.vin)

    # -- event handlers -------------------------------------------------------------

    def _handle(self, phase: Phase, day: int, data: dict) -> None:
        if phase == Phase.RESERVATION_ARRIVES:
            addr = self.addresses.sample(self._rng["address"])
            rid = self._next_id("R", 6)
            r = Reservation(id=rid, created_day=day, address=addr)
            self.reservations[rid] = r
            self._active[rid] = None
            self._buf["reservations"].append(rid)

        elif phase == Phase.ORDER_PLACED:
            r = self.reservations[data["rid"]]
            if r.status != CustomerStatus.INVITED:
                return  # cancelled while invited
            oid = self._next_id("O", 6)
            self.orders[oid] = Order(id=oid, reservation_id=r.id, placed_day=day,
                                     config=self.config.config_mix.sample(self._rng["config"]),
                                     center_id=r.center_id)
            r.order_id = oid
            self._set_status(r, CustomerStatus.ORDERED)
            self._buf["orders"].append(oid)

        elif phase == Phase.BUILD_COMPLETE:
            v = self.vehicles[data["vin"]]
            v.build_done_day = day
            hold = self.factory.quality.sample_hold_days(self._rng["quality"])
            self.factory.finish_build(held=hold > 0)
            if hold > 0:
                v.status, v.hold_days = VehicleStatus.QUALITY_HOLD, hold
                self.events.schedule(day + hold, Phase.HOLD_RELEASED, vin=v.vin)
                self._buf["holds_started"].append(v.vin)
            else:
                self._make_available(v, day)

        elif phase == Phase.HOLD_RELEASED:
            v = self.vehicles[data["vin"]]
            self.factory.release_hold()
            self._buf["holds_released"].append(v.vin)
            self._make_available(v, day)

        elif phase == Phase.VEHICLE_ARRIVES:
            v = self.vehicles[data["vin"]]
            v.arrive_day = day
            if v.order_id is None:  # customer cancelled while it was on the road
                v.status = VehicleStatus.AVAILABLE
            else:
                self._enqueue_handover(v, self.orders[v.order_id], day)

    def _handovers(self, day: int) -> None:
        for c in self.centers.values():
            rate = c.weekly_capacity / 7
            c._tokens = min(c._tokens + rate, rate + 1)
            while c.queue and c._tokens >= 1 and c.queue[0][0] <= day:
                _, vin = c.queue.popleft()
                v = self.vehicles[vin]
                o = self.orders[v.order_id]
                v.status, v.delivered_day = VehicleStatus.DELIVERED, day
                o.delivered_day = day
                self._set_status(self.reservations[o.reservation_id], CustomerStatus.DELIVERED)
                c._tokens -= 1
                self._buf["deliveries"].append(o.id)

    def _cancellations(self, day: int) -> None:
        rng = self._rng["cancel"]
        for rid in list(self._active):
            r = self.reservations[rid]
            p = self.cancellation.weekly_prob(r.status, (day - r.created_day) / 7)
            if p > 0 and rng.random() < p:
                self.cancel(rid, day)

    def cancel(self, rid: str, day: Optional[int] = None) -> CancellationView:
        """Cancel a customer. Called by the cancellation model; also usable directly in tests."""
        day = self.day if day is None else day
        r = self.reservations[rid]
        if r.status not in ACTIVE_STATUSES:
            raise ValueError(f"reservation {rid} is {r.status.value} and cannot be cancelled")
        status_before = r.status
        freed = None
        o = self.orders.get(r.order_id) if r.order_id else None
        if o is not None:
            if o.vin:
                v = self.vehicles[o.vin]
                v.order_id = None
                freed = v.vin
                if v.status == VehicleStatus.AT_CENTER:
                    c = self.centers[v.location]
                    c.queue = type(c.queue)(e for e in c.queue if e[1] != v.vin)
                    v.status = VehicleStatus.AVAILABLE
            elif o.production_vin:
                freed = o.production_vin
        r.status_at_cancel, r.cancelled_day = status_before, day
        self._set_status(r, CustomerStatus.CANCELLED)
        view = CancellationView(rid, r.order_id, day, status_before.value, freed)
        self._buf["cancellations"].append(view)
        return view

    # -- observation ------------------------------------------------------------

    @staticmethod
    def _new_buffers() -> dict[str, list]:
        return {k: [] for k in ("reservations", "orders", "started", "produced", "holds_started",
                                "holds_released", "cancellations", "deliveries", "opened")}

    def _order_view(self, o: Order) -> OrderView:
        return OrderView(o.id, o.reservation_id, o.placed_day, o.config, o.center_id, o.status.value, o.production_vin)

    def _vehicle_view(self, v: Vehicle) -> VehicleView:
        return VehicleView(v.vin, v.config, v.built_for_order_id, v.status.value, v.location,
                           v.start_day, v.expected_done_day, v.available_day)

    def _observe(self, rejections: list[Rejection]) -> Observation:
        b = self._buf
        f = self.factory
        return Observation(
            week=self.week,
            day=self.day,
            done=self.done,
            production_capacity=f.slots_available(),
            new_reservations=[self._reservation_view(self.reservations[i]) for i in b["reservations"]],
            new_orders=[self._order_view(self.orders[i]) for i in b["orders"]],
            vehicles_started=[self._vehicle_view(self.vehicles[i]) for i in b["started"]],
            vehicles_produced=[self._vehicle_view(self.vehicles[i]) for i in b["produced"]],
            quality_holds_started=list(b["holds_started"]),
            quality_holds_released=list(b["holds_released"]),
            cancellations=list(b["cancellations"]),
            deliveries=[self._delivery_view(self.orders[i]) for i in b["deliveries"]],
            pending_reservation_ids=[i for i in self._active
                                     if self.reservations[i].status == CustomerStatus.RESERVED],
            unassigned_reservations=[self._reservation_view(self.reservations[i]) for i in self._active
                                     if self.reservations[i].center_id is None],
            open_orders=[self._order_view(o) for o in self.orders.values() if o.status in MATCHABLE_ORDER_STATUSES],
            available_vehicles=[self._vehicle_view(v) for v in self.vehicles.values()
                                if v.status == VehicleStatus.AVAILABLE],
            centers=[CenterView(c.id, c.city, c.state, c.weekly_capacity, len(c.queue), c.opened_day)
                     for c in self.centers.values()],
            factory=FactoryView(f.line_rate, f.positions, f.in_build, f.on_hold, f.free_positions),
            rejections=list(rejections),
        )

    def _reservation_view(self, r: Reservation) -> ReservationView:
        a = r.address
        return ReservationView(r.id, r.created_day, a.street, a.city, a.state, r.center_id, self._nearest_center(r))

    def _delivery_view(self, o: Order) -> DeliveryView:
        return DeliveryView(o.id, o.reservation_id, o.vin, o.center_id, o.delivered_day)

    def _record_week(self, w: int, capacity: int, plan: dict) -> None:
        b = self._buf
        statuses = Counter(self.reservations[i].status for i in self._active)
        self.weekly.append({
            "week": w,
            "reservations": len(b["reservations"]),
            "invites": len(plan["release"]),
            "center_assignments": len(plan["centers"]),
            "centers_opened": len(plan["open"]),
            "orders": len(b["orders"]),
            "slots_offered": capacity,
            "builds_started": len(b["started"]),
            "vehicles_produced": len(b["produced"]),
            "quality_holds": len(b["holds_started"]),
            "assignments": len(plan["assign"]),
            "deliveries": len(b["deliveries"]),
            "cancellations": len(b["cancellations"]),
            "factory_in_build": self.factory.in_build,
            "factory_on_hold": self.factory.on_hold,
            "unassigned_inventory": sum(1 for v in self.vehicles.values() if v.status == VehicleStatus.AVAILABLE),
            "handover_queue": sum(len(c.queue) for c in self.centers.values()),
            **{f"active_{s.value.lower()}": statuses.get(s, 0) for s in CustomerStatus
               if s in ACTIVE_STATUSES},
        })

    # -- reporting --------------------------------------------------------------

    def report(self):
        from .metrics import build_report

        return build_report(self)
