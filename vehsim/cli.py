"""Command line entry point.

    vehsim run   [--config FILE] [--weeks N] [--seed S] [--out DIR] [--save-weekly]
    vehsim init  DIR [--config FILE] [--weeks N] [--seed S]
    vehsim step  DIR
    vehsim report DIR

``run`` drives the whole simulation with a built-in controller. ``init``/``step``
let you play the controller yourself through CSV files, one week at a time:

    DIR/week_003/observation/*.csv   what the simulator reports at the start of week 3
    DIR/week_003/actions/*.csv       your decisions for week 3 (fill these in, then `vehsim step DIR`)
"""

from __future__ import annotations

import argparse
import pickle
import sys
from pathlib import Path

from .config import SimConfig
from .controllers import CONTROLLERS
from .interface import SimulationError, Simulator
from .io import read_actions, write_action_templates, write_actions, write_observation

STATE_FILE = "state.pkl"


def _config(args) -> SimConfig:
    cfg = SimConfig.load(args.config) if args.config else SimConfig()
    if args.weeks is not None:
        cfg.n_weeks = args.weeks
    if args.seed is not None:
        cfg.seed = args.seed
    return cfg


def _week_dir(root: Path, week: int) -> Path:
    return root / f"week_{week:03d}"


def cmd_run(args) -> int:
    sim = Simulator(_config(args))
    controller = CONTROLLERS[args.controller]()
    obs = sim.reset()
    out = Path(args.out) if args.out else None
    while not sim.done:
        actions = controller.decide(obs)
        if out and args.save_weekly:
            write_observation(obs, _week_dir(out, obs.week) / "observation")
            write_actions(actions, _week_dir(out, obs.week) / "actions")
        obs = sim.step(actions)
    report = sim.report()
    print(report.to_text())
    if out:
        report.write(out / "report")
        print(f"Wrote report files to {out / 'report'}")
    return 0


def _save(sim: Simulator, root: Path) -> None:
    with open(root / STATE_FILE, "wb") as fh:
        pickle.dump(sim, fh)


def _load(root: Path) -> Simulator:
    path = root / STATE_FILE
    if not path.exists():
        raise SystemExit(f"{path} not found — run `vehsim init {root}` first")
    with open(path, "rb") as fh:
        return pickle.load(fh)


def _publish(sim: Simulator, obs, root: Path) -> None:
    wd = _week_dir(root, obs.week)
    write_observation(obs, wd / "observation")
    if not obs.done:
        write_action_templates(wd / "actions")
        print(f"Week {obs.week}: {obs.production_capacity} production slots. "
              f"Observation in {wd / 'observation'}; put decisions in {wd / 'actions'} and run `vehsim step {root}`.")


def cmd_init(args) -> int:
    root = Path(args.dir)
    if (root / STATE_FILE).exists() and not args.force:
        raise SystemExit(f"{root} already holds a simulation; use --force to start over")
    root.mkdir(parents=True, exist_ok=True)
    sim = Simulator(_config(args))
    obs = sim.reset()
    _save(sim, root)
    _publish(sim, obs, root)
    return 0


def cmd_step(args) -> int:
    root = Path(args.dir)
    sim = _load(root)
    if sim.done:
        raise SystemExit("simulation already finished; run `vehsim report` to see results")
    actions = read_actions(_week_dir(root, sim.week) / "actions")
    try:
        obs = sim.step(actions)
    except SimulationError as e:
        print(str(e), file=sys.stderr)
        print("Nothing was changed. Fix the action files and run step again.", file=sys.stderr)
        return 2
    _save(sim, root)
    _publish(sim, obs, root)
    if obs.rejections:
        print(f"{len(obs.rejections)} action(s) were skipped; see status.json.")
    if obs.done:
        report = sim.report()
        report.write(root / "report")
        print(report.to_text())
    return 0


def cmd_report(args) -> int:
    root = Path(args.dir)
    sim = _load(root)
    report = sim.report()
    report.write(root / "report")
    print(report.to_text())
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="vehsim", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def sim_opts(sp):
        sp.add_argument("--config", help="JSON (or YAML) file with settings to override")
        sp.add_argument("--weeks", type=int, help="number of weeks to simulate (default 52)")
        sp.add_argument("--seed", type=int, help="random seed")

    r = sub.add_parser("run", help="run a full simulation with a built-in controller")
    sim_opts(r)
    r.add_argument("--controller", choices=sorted(CONTROLLERS), default="fifo")
    r.add_argument("--out", help="directory for report files")
    r.add_argument("--save-weekly", action="store_true", help="also save each week's observation and actions")
    r.set_defaults(func=cmd_run)

    i = sub.add_parser("init", help="start a file-driven simulation in DIR")
    i.add_argument("dir")
    sim_opts(i)
    i.add_argument("--force", action="store_true")
    i.set_defaults(func=cmd_init)

    s = sub.add_parser("step", help="apply DIR/week_NNN/actions and simulate one week")
    s.add_argument("dir")
    s.set_defaults(func=cmd_step)

    rp = sub.add_parser("report", help="write wait-time statistics for a file-driven simulation")
    rp.add_argument("dir")
    rp.set_defaults(func=cmd_report)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
