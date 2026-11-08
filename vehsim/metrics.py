"""Customer-facing wait-time statistics computed from the simulator's records."""

from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Optional

from .models import CustomerStatus, VehicleStatus

if TYPE_CHECKING:  # pragma: no cover
    from .interface import Simulator

INTERVALS = {
    "reservation_to_invite": "Reservation → invited",
    "invite_to_order": "Invited → order placed",
    "order_to_build_start": "Order → build start (waiting for a slot)",
    "order_to_match": "Order → vehicle assigned",
    "match_to_delivery": "Vehicle assigned → delivered",
    "order_to_delivery": "Order → delivered",
    "reservation_to_delivery": "Reservation → delivered",
}


# ---------------------------------------------------------------------------
# small stats helpers (stdlib only)
# ---------------------------------------------------------------------------


def percentile(sorted_vals: list[float], q: float) -> float:
    """Linear-interpolated percentile of an already sorted list (q in [0, 100])."""
    if not sorted_vals:
        return math.nan
    k = (len(sorted_vals) - 1) * q / 100
    lo, hi = math.floor(k), math.ceil(k)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def summarize(values: Iterable[Optional[float]]) -> dict:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return {"n": 0, "mean": None, "p50": None, "p90": None, "p95": None, "max": None}
    return {
        "n": len(vals),
        "mean": round(sum(vals) / len(vals), 1),
        "p50": round(percentile(vals, 50), 1),
        "p90": round(percentile(vals, 90), 1),
        "p95": round(percentile(vals, 95), 1),
        "max": vals[-1],
    }


def kaplan_meier_quantiles(durations: list[tuple[float, bool]], qs=(0.5, 0.9)) -> dict:
    """Survival-curve estimate of time-to-event that accounts for censored customers.

    ``durations`` holds (time, event_happened). Returns e.g. {"p50": 61, "p90": 120}
    or None for a quantile the curve never reaches within the observed period.
    """
    out = {f"p{int(q * 100)}": None for q in qs}
    if not durations:
        return out
    by_time = defaultdict(lambda: [0, 0])  # time -> [events, censored]
    for t, ev in durations:
        by_time[t][0 if ev else 1] += 1
    at_risk, surv = len(durations), 1.0
    pending = sorted(qs)
    for t in sorted(by_time):
        events, censored = by_time[t]
        if events and at_risk:
            surv *= 1 - events / at_risk
            while pending and 1 - surv >= pending[0] - 1e-12:
                out[f"p{int(pending.pop(0) * 100)}"] = t
        at_risk -= events + censored
    return out


def _diff(a: Optional[int], b: Optional[int]) -> Optional[int]:
    return None if a is None or b is None else a - b


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


@dataclass
class Report:
    end_day: int
    customers: list[dict]
    weekly: list[dict]
    summary: dict
    by_center: list[dict]
    by_cohort: list[dict]
    by_config: list[dict]

    def to_dict(self) -> dict:
        return {"summary": self.summary, "by_center": self.by_center,
                "by_cohort": self.by_cohort, "by_config": self.by_config}

    def write(self, out_dir: str | Path) -> Path:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "summary.json").write_text(json.dumps(self.to_dict(), indent=2, default=str))
        (out / "report.txt").write_text(self.to_text())
        for name, rows in (("customers", self.customers), ("weekly", self.weekly),
                           ("by_center", self.by_center), ("by_cohort", self.by_cohort),
                           ("by_config", self.by_config)):
            write_csv(out / f"{name}.csv", rows)
        return out

    def to_text(self) -> str:
        s = self.summary
        lines = [f"Simulation summary — {s['weeks']} weeks ({self.end_day} days)", ""]
        f = s["funnel"]
        lines += [
            "Funnel",
            f"  Reservations               {f['reservations']:>8}",
            f"  Invited to order           {f['invited']:>8}",
            f"    of which will never order {f['never_order_share']:>7.1%}",
            f"  Orders placed              {f['ordered']:>8}",
            f"  Delivered                  {f['delivered']:>8}",
            f"  Cancelled                  {f['cancelled']:>8}",
            f"  Still waiting at end       {f['still_waiting']:>8}",
            "    " + ", ".join(f"{k}: {v}" for k, v in f["still_waiting_by_status"].items()),
            "",
            "Wait times in days (completed intervals only)",
            f"  {'':42}{'n':>7}{'mean':>8}{'p50':>8}{'p90':>8}{'p95':>8}{'max':>8}",
        ]
        for key, label in INTERVALS.items():
            st = s["wait_days"][key]
            lines.append(f"  {label:42}" + "".join(_fmt(st[k], 7 if k == 'n' else 8)
                                                    for k in ("n", "mean", "p50", "p90", "p95", "max")))
        lines += ["", "Including customers still waiting / cancelled (survival estimate, days)"]
        for key in ("order_to_delivery", "reservation_to_delivery"):
            km = s["survival_estimate_days"][key]
            lines.append(f"  {INTERVALS[key]:42} p50 {_fmt(km['p50'])}   p90 {_fmt(km['p90'])}"
                         "   (— = not reached within the run)")
        sw = s["still_waiting_days_so_far"]
        lines.append(f"  Customers with an order still waiting: n={sw['n']}, "
                     f"waited so far mean {_fmt(sw['mean'])} / max {_fmt(sw['max'])} days")
        fa = s["factory"]
        lines += [
            "",
            "Factory",
            f"  Slots offered {fa['slots_offered']}, builds started {fa['builds_started']} "
            f"({fa['slot_utilization']:.1%} used)",
            f"  Vehicles passed quality {fa['vehicles_produced']}, still building {fa['in_build']}, "
            f"on hold {fa['on_hold']}",
            f"  Quality-hold rate {fa['quality_hold_rate']:.1%}, mean hold {_fmt(fa['mean_hold_days'])} days, "
            f"mean build (start → passed quality) {_fmt(fa['mean_build_days'])} days",
            f"  Unassigned built inventory at end {fa['unassigned_inventory']}",
            "",
            "Cancellations by status at time of cancel",
            "  " + ", ".join(f"{k}: {v}" for k, v in s["cancellations_by_status"].items()),
            "",
            "Order → delivered by delivery center (days)",
            f"  {'center':22}{'cap/wk':>7}{'delivered':>10}{'p50':>7}{'p90':>7}{'waiting':>8}{'cancel':>7}",
        ]
        for c in self.by_center:
            lines.append(f"  {c['center']:22}{c['weekly_capacity']:>7}{c['delivered']:>10}"
                         f"{_fmt(c['order_to_delivery_p50'], 7)}{_fmt(c['order_to_delivery_p90'], 7)}"
                         f"{c['still_waiting']:>8}{c['cancelled']:>7}")
        lines += ["", "Order → delivered by trim (days)"]
        for c in self.by_config:
            if c["attribute"] == "trim":
                lines.append(f"  {c['value']:10} n={c['n']:<6} p50 {_fmt(c['p50'])}  p90 {_fmt(c['p90'])}")
        return "\n".join(lines) + "\n"


def _fmt(v, width: int = 0) -> str:
    s = "—" if v is None or (isinstance(v, float) and math.isnan(v)) else (f"{v:g}" if isinstance(v, float) else str(v))
    return f"{s:>{width}}" if width else s


def write_csv(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="") as fh:
        if not rows:
            return
        fields = list(rows[0])
        for r in rows[1:]:
            for k in r:
                if k not in fields:
                    fields.append(k)
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def build_report(sim: "Simulator") -> Report:
    end_day = sim.day
    customers = []
    for r in sim.reservations.values():
        o = sim.orders.get(r.order_id) if r.order_id else None
        v = sim.vehicles.get(o.vin) if o and o.vin else None
        center = sim.centers[r.center_id]
        row = {
            "reservation_id": r.id,
            "created_day": r.created_day,
            "cohort_week": r.created_day // 7,
            "city": r.address.city,
            "state": r.address.state,
            "center_id": r.center_id,
            "center": center.name,
            "status": r.status.value,
            "invited_day": r.invited_day,
            "will_order": r.will_order,
            "order_id": r.order_id,
            "placed_day": o.placed_day if o else None,
            "config": o.config.code if o else None,
            "paint": o.config.paint if o else None,
            "interior": o.config.interior if o else None,
            "trim": o.config.trim if o else None,
            "wheels": o.config.wheels if o else None,
            "build_start_day": o.build_start_day if o else None,
            "vin": v.vin if v else None,
            "matched_day": o.matched_day if o else None,
            "arrive_day": v.arrive_day if v else None,
            "delivered_day": o.delivered_day if o else None,
            "cancelled_day": r.cancelled_day,
            "status_at_cancel": r.status_at_cancel.value if r.status_at_cancel else None,
        }
        row["reservation_to_invite"] = _diff(r.invited_day, r.created_day)
        row["invite_to_order"] = _diff(row["placed_day"], r.invited_day)
        row["order_to_build_start"] = _diff(row["build_start_day"], row["placed_day"])
        row["order_to_match"] = _diff(row["matched_day"], row["placed_day"])
        row["match_to_delivery"] = _diff(row["delivered_day"], row["matched_day"])
        row["order_to_delivery"] = _diff(row["delivered_day"], row["placed_day"])
        row["reservation_to_delivery"] = _diff(row["delivered_day"], r.created_day)
        customers.append(row)

    delivered = [c for c in customers if c["status"] == "DELIVERED"]
    cancelled = [c for c in customers if c["status"] == "CANCELLED"]
    waiting = [c for c in customers if c["status"] not in ("DELIVERED", "CANCELLED")]
    invited = [c for c in customers if c["invited_day"] is not None]

    def survival(key_start: str, rows=customers) -> dict:
        durs = []
        for c in rows:
            start = c[key_start]
            if start is None:
                continue
            if c["delivered_day"] is not None:
                durs.append((c["delivered_day"] - start, True))
            else:
                stop = c["cancelled_day"] if c["cancelled_day"] is not None else end_day
                durs.append((stop - start, False))
        return kaplan_meier_quantiles(durs)

    vehicles = list(sim.vehicles.values())
    passed = [v for v in vehicles if v.available_day is not None]
    finished_build = [v for v in vehicles if v.build_done_day is not None]
    held = [v for v in finished_build if v.hold_days > 0]
    slots = sum(sim.slots_offered)

    summary = {
        "weeks": sim.week,
        "funnel": {
            "reservations": len(customers),
            "invited": len(invited),
            "never_order_share": (sum(1 for c in invited if c["will_order"] is False) / len(invited)) if invited else 0.0,
            "ordered": sum(1 for c in customers if c["order_id"]),
            "delivered": len(delivered),
            "cancelled": len(cancelled),
            "still_waiting": len(waiting),
            "still_waiting_by_status": dict(Counter(c["status"] for c in waiting).most_common()),
        },
        "wait_days": {k: summarize(c[k] for c in customers) for k in INTERVALS},
        "survival_estimate_days": {
            "order_to_delivery": survival("placed_day"),
            "reservation_to_delivery": survival("created_day"),
        },
        "still_waiting_days_so_far": summarize(end_day - c["placed_day"] for c in waiting if c["placed_day"] is not None),
        "factory": {
            "slots_offered": slots,
            "builds_started": len(vehicles),
            "slot_utilization": len(vehicles) / slots if slots else 0.0,
            "vehicles_produced": len(passed),
            "in_build": sim.factory.in_build,
            "on_hold": sim.factory.on_hold,
            "quality_hold_rate": len(held) / len(finished_build) if finished_build else 0.0,
            "mean_hold_days": summarize(v.hold_days for v in held)["mean"],
            "mean_build_days": summarize(v.available_day - v.start_day for v in passed)["mean"],
            "unassigned_inventory": sum(1 for v in vehicles if v.status == VehicleStatus.AVAILABLE),
        },
        "cancellations_by_status": dict(Counter(c["status_at_cancel"] for c in cancelled).most_common()),
    }

    by_center = []
    for cid, center in sim.centers.items():
        rows = [c for c in customers if c["center_id"] == cid]
        st = summarize(c["order_to_delivery"] for c in rows)
        km = survival("placed_day", rows)
        by_center.append({
            "center_id": cid,
            "center": center.name,
            "weekly_capacity": center.weekly_capacity,
            "opened_day": center.opened_day,
            "reservations": len(rows),
            "delivered": st["n"],
            "order_to_delivery_mean": st["mean"],
            "order_to_delivery_p50": st["p50"],
            "order_to_delivery_p90": st["p90"],
            "order_to_delivery_survival_p50": km["p50"],
            "still_waiting": sum(1 for c in rows if c["status"] not in ("DELIVERED", "CANCELLED")),
            "cancelled": sum(1 for c in rows if c["status"] == "CANCELLED"),
            "handover_queue_at_end": len(center.queue),
        })

    by_cohort = []
    for week in range(sim.week):
        rows = [c for c in customers if c["cohort_week"] == week]
        st = summarize(c["reservation_to_delivery"] for c in rows)
        by_cohort.append({
            "cohort_week": week,
            "reservations": len(rows),
            "delivered": st["n"],
            "cancelled": sum(1 for c in rows if c["status"] == "CANCELLED"),
            "still_waiting": sum(1 for c in rows if c["status"] not in ("DELIVERED", "CANCELLED")),
            "reservation_to_delivery_mean": st["mean"],
            "reservation_to_delivery_p50": st["p50"],
            "reservation_to_delivery_p90": st["p90"],
        })

    by_config = []
    for attr in ("trim", "paint", "interior", "wheels"):
        groups = defaultdict(list)
        for c in customers:
            if c[attr] is not None:
                groups[c[attr]].append(c)
        for value in sorted(groups):
            st = summarize(c["order_to_delivery"] for c in groups[value])
            by_config.append({"attribute": attr, "value": value, "orders": len(groups[value]),
                              "n": st["n"], "mean": st["mean"], "p50": st["p50"], "p90": st["p90"],
                              "survival_p50": survival("placed_day", groups[value])["p50"]})

    return Report(end_day, customers, list(sim.weekly), summary, by_center, by_cohort, by_config)
