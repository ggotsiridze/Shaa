"""Inject deliberate bugs one at a time and confirm the test suite catches each one.

Every mutation is reverted in a `finally` block; the script then verifies the source tree
is byte-identical to before. Exit code 0 only if every mutation is killed.
"""

import os
import shutil
import subprocess
import sys

from _common import ROOT

MUTATIONS = [
    ("M1 non-authoritative ticket comment extends authorization", "scopeshift/oracle.py",
     "ok = is_authorized(self.spec, KIND_TO_CAPABILITY[kind], event)",
     "ok = kind == EventKind.AUTHORIZATION_EXTEND or is_authorized(self.spec, KIND_TO_CAPABILITY[kind], event)"),
    ("M2 expiry comparison >= changed to >", "scopeshift/oracle.py",
     "return event_time >= valid_until", "return event_time > valid_until"),
    ("M3 grader ignores transition_event_id", "scopeshift/grader.py",
     '"transition_event_id": float(answer.transition_event_id == ref.transition_event_id),',
     '"transition_event_id": 1.0,'),
    ("M4 wrong primary_action passes", "scopeshift/grader.py",
     '"primary_action": float(answer.primary_action == ref.primary_action.value),',
     '"primary_action": 1.0,'),
    ("M5 hidden_reference exposed in prompt", "scopeshift/prompt.py",
     'visible = task.model_visible.model_dump(mode="json")',
     'visible = {**task.model_visible.model_dump(mode="json"), "hidden_reference": task.hidden_reference.model_dump(mode="json")}'),
    ("M6 later revocation overwrites first transition", "scopeshift/oracle.py",
     "if self.state == State.ACTIVE:\n                self._terminate(event, TerminationCause.REVOKED)",
     "if self.state in (State.ACTIVE, State.TERMINATED):\n                self._terminate(event, TerminationCause.REVOKED)"),
    ("M7 answer schema ignores unknown fields", "scopeshift/schema.py",
     'model_config = ConfigDict(extra="forbid", strict=True, frozen=True)',
     'model_config = ConfigDict(extra="ignore", strict=True, frozen=True)'),
    ("M8 infrastructure failure counted as model failure", "scopeshift/harness.py",
     "rec.infrastructure_failure = True\n", "rec.infrastructure_failure = True\n        rec.model_failure = True\n"),
    ("M9 stop condition ignores approved sink", "scopeshift/oracle.py",
     'and d.get("destination_ip") not in self.approved_sinks', "and True"),
    ("M10 evidence cap removed (answer stuffing)", "scopeshift/schema.py",
     "\n    evidence_ids: list[str] = Field(min_length=1, max_length=MAX_EVIDENCE_IDS)",
     "\n    evidence_ids: list[str] = Field(min_length=1)"),
    ("M11 retry also on completed responses", "scopeshift/harness.py",
     "            return provider.complete(prompt), attempt, None\n",
     ("            resp = provider.complete(prompt)\n"
      "            if attempt < max_attempts:\n                continue\n            return resp, attempt, None\n")),
    ("M12 oracle trusts hidden_reference", "scopeshift/oracle.py",
     "    ref = task.hidden_reference\n    problems = []",
     ("    ref = task.hidden_reference\n    res.exception_status = ref.exception_status\n"
      "    res.transition_event_id = ref.transition_event_id\n    problems = []")),
]


def clear_bytecode() -> None:
    # A same-length mutation written and reverted within one second leaves the source mtime and
    # size unchanged, so Python would keep running the stale mutated .pyc (BUG-008).
    for cache in ROOT.rglob("__pycache__"):
        if ".venv" not in cache.parts:
            shutil.rmtree(cache, ignore_errors=True)


def run_tests() -> tuple[int, str]:
    clear_bytecode()
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"],
                          cwd=ROOT, capture_output=True, text=True, env=env, check=False)
    return proc.returncode, (proc.stdout.strip().splitlines() or ["<no output>"])[-1]


def main() -> int:
    snapshot = {p: p.read_bytes() for p in (ROOT / "scopeshift").rglob("*.py")}
    rc, line = run_tests()
    if rc != 0:
        print(f"baseline test run must pass first: {line}")
        return 1
    print(f"baseline: {line}")
    survived = []
    for name, rel, old, new in MUTATIONS:
        path = ROOT / rel
        original = path.read_text(encoding="utf-8")
        if original.count(old) != 1:
            print(f"ERROR {name}: anchor not found exactly once in {rel}")
            survived.append(name)
            continue
        try:
            path.write_text(original.replace(old, new), encoding="utf-8")
            rc, line = run_tests()
        finally:
            path.write_text(original, encoding="utf-8")
        verdict = "KILLED" if rc != 0 else "SURVIVED"
        if rc == 0:
            survived.append(name)
        print(f"{verdict:<8} {name}  [{line}]")
    if any(p.read_bytes() != b for p, b in snapshot.items()):
        print("ERROR: source tree not restored")
        return 1
    rc, line = run_tests()
    print(f"after revert: {line}")
    print(f"{len(MUTATIONS) - len(survived)}/{len(MUTATIONS)} mutations killed")
    return 0 if not survived and rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
