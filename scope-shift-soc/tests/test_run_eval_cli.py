import json

from conftest import ROOT

import run_eval


def test_cli_writes_results_and_transcripts(tmp_path):
    rc = run_eval.main(["--provider", "mock", "--model", "mock-perfect", "--tasks", str(ROOT / "tasks/development"),
                        "--trials", "2", "--results-dir", str(tmp_path / "r"), "--transcripts-dir", str(tmp_path / "t")])
    assert rc == 0
    summary = json.loads(next((tmp_path / "r").glob("*.json")).read_text())
    assert summary["metrics"]["strict_pass_pow_2"] == 1.0
    lines = next((tmp_path / "t").glob("*.jsonl")).read_text().splitlines()
    assert len(lines) == 8 and len({json.loads(x)["trial_id"] for x in lines}) == 8


def test_cli_bad_task_path(tmp_path):
    assert run_eval.main(["--provider", "mock", "--model", "mock-perfect", "--tasks", str(tmp_path / "missing")]) == 2


def test_cli_refuses_when_oracle_disagrees(tmp_path):
    src = json.loads((ROOT / "tasks/development/SS-DEV-001A.json").read_text())
    src["hidden_reference"]["primary_action"] = "MONITOR"
    (tmp_path / "SS-DEV-001A.json").write_text(json.dumps(src))
    assert run_eval.main(["--provider", "mock", "--model", "mock-perfect", "--tasks", str(tmp_path),
                          "--results-dir", str(tmp_path / "r"), "--transcripts-dir", str(tmp_path / "t")]) == 2
