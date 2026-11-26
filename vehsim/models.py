"""Entities tracked by the simulator.

All times are integer *days* since start of production (day 0). Week ``w`` covers
days ``[7w, 7w + 7)``.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .catalog import VehicleConfig


class CustomerStatus(str, Enum):
    RESERVED = "RESERVED"
    INVITED = "INVITED"
    ORDERED = "ORDERED"
    IN_PRODUCTION = "IN_PRODUCTION"
    IN_TRANSIT = "IN_TRANSIT"
    AT_CENTER = "AT_CENTER"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"


ORDER_STATUSES = {
    CustomerStatus.ORDERED,
    CustomerStatus.IN_PRODUCTION,
    CustomerStatus.IN_TRANSIT,
    CustomerStatus.AT_CENTER,
    CustomerStatus.DELIVERED,
    CustomerStatus.CANCELLED,
}

# Orders that do not yet have a vehicle assigned and can therefore be matched.
MATCHABLE_ORDER_STATUSES = {CustomerStatus.ORDERED, CustomerStatus.IN_PRODUCTION}


class VehicleStatus(str, Enum):
    IN_BUILD = "IN_BUILD"
    QUALITY_HOLD = "QUALITY_HOLD"
    AVAILABLE = "AVAILABLE"  # built, passed quality, not assigned; at the factory or a center
    IN_TRANSIT = "IN_TRANSIT"
    AT_CENTER = "AT_CENTER"  # assigned and waiting in a center's handover queue
    DELIVERED = "DELIVERED"


FACTORY = "FACTORY"


@dataclass(frozen=True)
class Address:
    street: str
    city: str
    state: str
    zip: Optional[str] = None

    def __str__(self) -> str:
        z = f" {self.zip}" if self.zip else ""
        return f"{self.street}, {self.city}, {self.state}{z}"

    @classmethod
    def parse(cls, text: str) -> "Address":
        """'233 S Wacker Dr, Chicago, IL 60606' -> Address. The ZIP is optional.

        Raises ValueError unless the text is "street, city, ST[ ZIP]".
        """
        parts = [" ".join(p.split()) for p in str(text).split(",")]
        if len(parts) < 3 or not all(parts):
            raise ValueError(f"expected 'street, city, ST ZIP', got {text!r}")
        street, city, tail = ", ".join(parts[:-2]), parts[-2], parts[-1].split()
        state, zip_code = tail[0].upper(), (tail[1] if len(tail) > 1 else None)
        if len(state) != 2 or len(tail) > 2 or (zip_code and not (zip_code.isdigit() and len(zip_code) == 5)):
            raise ValueError(f"expected 'street, city, ST ZIP' with a 2-letter state and 5-digit ZIP, got {text!r}")
        return cls(street=street, city=city, state=state, zip=zip_code)


@dataclass
class DeliveryCenter:
    id: str
    address: str  # full street address, e.g. "233 S Wacker Dr, Chicago, IL 60606"
    lat: float
    lon: float
    weekly_capacity: int
    opened_day: int = 0
    # VINs waiting for handover, in arrival order: (ready_day, vin)
    queue: deque = field(default_factory=deque)
    _tokens: float = 0.0

    @property
    def name(self) -> str:
        return self.address


@dataclass
class Reservation:
    id: str
    created_day: int
    address: Address
    center_id: Optional[str] = None  # set once by the controller; never changes afterwards
    center_assigned_day: Optional[int] = None
    status: CustomerStatus = CustomerStatus.RESERVED
    invited_day: Optional[int] = None
    will_order: Optional[bool] = None  # hidden from controllers; drawn at invite
    order_id: Optional[str] = None
    cancelled_day: Optional[int] = None
    status_at_cancel: Optional[CustomerStatus] = None


@dataclass
class Order:
    id: str
    reservation_id: str
    placed_day: int
    config: VehicleConfig
    center_id: Optional[str]
    status: CustomerStatus = CustomerStatus.ORDERED
    production_vin: Optional[str] = None  # vehicle started for this order
    build_start_day: Optional[int] = None
    vin: Optional[str] = None  # vehicle assigned (may differ from production_vin)
    matched_day: Optional[int] = None
    delivered_day: Optional[int] = None


@dataclass
class Vehicle:
    vin: str
    config: VehicleConfig
    built_for_order_id: Optional[str]
    start_day: int
    build_days: int
    status: VehicleStatus = VehicleStatus.IN_BUILD
    location: str = FACTORY  # FACTORY, a center id, or the destination center while in transit
    build_done_day: Optional[int] = None
    hold_days: int = 0
    available_day: Optional[int] = None  # passed quality
    order_id: Optional[str] = None  # current assignment
    ship_day: Optional[int] = None
    arrive_day: Optional[int] = None
    delivered_day: Optional[int] = None

    @property
    def expected_done_day(self) -> int:
        return self.start_day + self.build_days
