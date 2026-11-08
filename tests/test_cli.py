from __future__ import annotations

import json

from vehsim.cli import main


def test_file_driven_weeks(tmp_path, capsys):
    root = tmp_path / "game"
    assert main(["init", str(root), "--weeks", "3", "--seed", "2"]) == 0
    assert (root / "week_000" / "actions" / "release.csv").exists()
    assert main(["step", str(root)]) == 0  # empty actions are fine
    pending = (root / "week_001" / "observation" / "pending_reservations.csv").read_text().splitlines()[1:]
    assert pending
    (root / "week_001" / "actions" / "release.csv").write_text("reservation_id\n" + "\n".join(pending[:5]) + "\n")
    assert main(["step", str(root)]) == 0
    status = json.loads((root / "week_002" / "observation" / "status.json").read_text())
    assert status["week"] == 2
    assert main(["step", str(root)]) == 0
    assert (root / "report" / "summary.json").exists()


def test_bad_actions_leave_state_untouched(tmp_path, capsys):
    root = tmp_path / "game"
    main(["init", str(root), "--weeks", "3"])
    (root / "week_000" / "actions" / "release.csv").write_text("reservation_id\nR999999\n")
    assert main(["step", str(root)]) == 2
    assert "unknown reservation id" in capsys.readouterr().err
    (root / "week_000" / "actions" / "release.csv").write_text("reservation_id\n")
    assert main(["step", str(root)]) == 0


def test_run_command(tmp_path, capsys):
    assert main(["run", "--weeks", "6", "--out", str(tmp_path / "out"), "--save-weekly"]) == 0
    assert "Simulation summary" in capsys.readouterr().out
    assert (tmp_path / "out" / "week_000" / "observation" / "status.json").exists()
    assert (tmp_path / "out" / "report" / "weekly.csv").exists()


def test_csv_center_actions(tmp_path):
    root = tmp_path / "game"
    main(["init", str(root), "--weeks", "3"])
    main(["step", str(root)])
    unassigned = (root / "week_001" / "observation" / "unassigned_reservations.csv").read_text().splitlines()
    header = unassigned[0].split(",")
    first = dict(zip(header, unassigned[1].split(",")))
    acts = root / "week_001" / "actions"
    (acts / "open_centers.csv").write_text("city,state,capacity\nOmaha,NE,75\n")
    (acts / "center_assignments.csv").write_text(f"reservation_id,center\n{first['id']},\"Omaha, NE\"\n")
    assert main(["step", str(root)]) == 0
    centers = (root / "week_002" / "observation" / "centers.csv").read_text()
    assert "Omaha,NE,75" in centers
