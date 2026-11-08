# vehsim

A week-by-week, event-driven simulator for the start of production of a new vehicle.

Each week the simulator generates customer reservations, turns invited reservations into
orders, runs the factory and moves finished vehicles to delivery centers. **You** play the
controller: each week you decide which delivery center serves each reservation, which
reservations may order, which orders get a production slot, which built vehicle goes to which
order, and whether to open new delivery centers. At the end, it reports customer-facing
wait-time statistics.

## Setup

Dependencies are managed with [uv](https://docs.astral.sh/uv/). The simulator itself uses only
the Python standard library; pytest is a dev dependency.

```bash
uv sync
uv run pytest
```

## Quick start

Run 52 weeks with the built-in first-come-first-served controller:

```bash
uv run vehsim run --seed 1 --out runs/demo
```

The report prints to the terminal and is also saved under `runs/demo/report/`:
`report.txt`, `summary.json`, `customers.csv` (one row per customer with every timestamp),
`weekly.csv`, `by_center.csv`, `by_cohort.csv` and `by_config.csv`.

## Write your own controller

Open [`my_controller.py`](my_controller.py) and fill in these functions. Each one has a `TODO`
and a docstring with its rules. Each week they're called in this order:

| # | Function | Return | Required? |
|---|---|---|---|
| 1 | `open_centers(obs, memory)` | `[(city, state, capacity), ...]`; capacity is optional, default 25/week | Optional; return `[]` to keep the 20 default centers |
| 2 | `assign_centers(obs, memory)` | `{reservation_id: center_id or "City, ST"}`; final once set | Yes |
| 3 | `assign_vehicles(obs, memory)` | `{vin: order_id}`; configs must match | Yes |
| 4 | `schedule_production(obs, memory, assignments)` | `[order_id, ...]`, at most `obs.production_capacity` | Yes |
| 5 | `release_reservations(obs, memory)` | `[reservation_id, ...]` to invite to order | Yes |

- `obs` is what the simulator reports for the week. The top of `my_controller.py` lists every
  field.
- `memory` is a dict that persists across weeks; store anything you want to remember.
- `assignments` passed to `schedule_production` is what `assign_vehicles` returned that week.
  Use it to avoid scheduling production for orders that are already getting a vehicle.

Then run it:

```bash
uv run vehsim run --controller my_controller.py --out runs/mine
```

The unfilled template runs, but nothing happens: no one is invited, so nothing is built or
delivered. If a function returns something invalid, the run stops and lists every problem.
For a working example, see the FIFO controller in
[`vehsim/controllers/fifo.py`](vehsim/controllers/fifo.py). To keep several strategies, copy
`my_controller.py` under a new name and pass that file to `--controller`.

## Python API

```python
from vehsim import Actions, SimConfig, Simulator

sim = Simulator(SimConfig(seed=42))
obs = sim.reset()
while not sim.done:
    actions = Actions(
        center_assignments={"R000001": "C04"},  # reservation -> center id or "City, ST"; final
        release_reservation_ids=[...],          # reservations invited to order
        production_order_ids=[...],             # at most obs.production_capacity
        assignments={"V0000001": "O000001"},    # built vehicle -> order (configs must match)
        open_delivery_centers=[("Kansas City", "MO", 60)],  # optional; capacity/week optional
    )
    obs = sim.step(actions)
print(sim.report().to_text())
```

See `vehsim/controllers/fifo.py` for a complete controller.

### What you see each week (`Observation`)

| Field | Meaning |
|---|---|
| `production_capacity` | Production slots for the coming week |
| `new_reservations` | Reservations created last week, with address and `nearest_center_id` (a hint) |
| `unassigned_reservations` | Active reservations that don't have a delivery center yet |
| `new_orders` | Orders placed last week, with vehicle config |
| `vehicles_started` / `vehicles_produced` | Builds started; vehicles that passed quality |
| `quality_holds_started` / `_released` | VINs entering / leaving quality hold |
| `cancellations`, `deliveries` | Last week's cancellations and handovers |
| `pending_reservation_ids` | Reservations not yet invited |
| `open_orders` | Orders without a vehicle (status `ORDERED` or `IN_PRODUCTION`) |
| `available_vehicles` | Built, unassigned vehicles and where they are |
| `centers`, `factory` | Delivery center capacity/queues; factory occupancy |

Invalid actions raise `SimulationError` and nothing changes. Examples: more production orders
than slots, unknown IDs, a config mismatch, a vehicle that isn't built, changing a reservation's
delivery center, or assigning a vehicle to an order with no delivery center. With
`SimConfig(strict=False)` invalid items are skipped instead and listed in `obs.rejections`.

## Playing week by week with CSV files

```bash
uv run vehsim init runs/game            # creates runs/game/week_000/{observation,actions}
# edit runs/game/week_000/actions/*.csv:
#   center_assignments.csv (reservation_id,center), release.csv, production.csv,
#   assignments.csv, open_centers.csv (city,state,capacity — capacity optional)
uv run vehsim step runs/game            # simulates the week, writes week_001/
uv run vehsim report runs/game          # report at any point
```

## How the model works

Time is tracked in days; decisions are made weekly (week *w* = days 7w…7w+6). Decisions apply
on the first day of the week.

- **Reservations** follow a Poisson process with a launch spike that decays to a steady rate.
  Addresses are drawn from ~75 US cities weighted by market size.
- **Delivery centers** are assigned to reservations by the controller, at any point before a
  vehicle is assigned to the customer's order. Once set, a reservation's center can't be
  changed. Each reservation comes with a `nearest_center_id` hint; the FIFO controller just
  uses that.
- **Ordering:** 10% of invited customers never order and stay `INVITED`. The rest order after
  X days, where X ~ Normal(10, 6) truncated to 1–30 days. The vehicle config (paint, interior,
  trim, wheels) is chosen at order time.
- **Factory:** at most `line_rate` (500) builds start per week, and the factory can hold
  `positions` (500 × 11 = 5,500) vehicles at once. Build time is uniform between 8 and 14
  weeks. 5% of finished builds go on quality hold for 1–3 weeks and keep their position until
  released. Slots for a week = min(line_rate, free positions), so after about 11 weeks the slot
  count follows how many vehicles passed quality the week before.
- **Assignment** needs an exact config match. A vehicle can go to any open order, not just the
  one it was built for. If you take a vehicle from the order it was built for, that order goes
  back to `ORDERED`.
- **Logistics:** transit = ⌈miles / 500⌉ weeks. Each delivery center hands over vehicles
  first-in-first-out, up to its weekly capacity, starting 2 days after arrival. The 20 default
  centers split 700 handovers/week by market size. Centers opened mid-run get the weekly
  capacity the controller specifies (default 25), and only serve reservations the controller
  assigns to them.
- **Cancellations:** weekly probability = base rate for the customer's status + 0.0001 × weeks
  since reservation. A cancelled customer's vehicle becomes unassigned stock where it is
  (factory, in transit or a center).

Every number is a setting in `vehsim/config.py`. To override settings, pass a JSON file
(`--config my.json`):

```json
{"n_weeks": 26, "factory": {"line_rate": 800}, "conversion": {"never_order_share": 0.15}}
```

Build time, quality holds, order delay and address → center routing are pluggable
(`Simulator(build_time=..., quality=..., order_delay=..., resolver=...)`). For example,
`FixedBuildTime(70)` in `vehsim/processes/factory.py` gives every vehicle a fixed build time.

## Statistics

Wait times are reported in days, broken down by delivery center, reservation week and config
attribute:

- reservation → invited
- invited → order
- order → build start
- order → vehicle assigned
- assigned → delivered
- **order → delivered**
- **reservation → delivered**

Customers still waiting, or who cancelled, are included through a survival estimate, so
results aren't biased toward people who happened to be served quickly.
