from conftest import reference_json

from scopeshift.harness import run_eval
from scopeshift.providers.base import Provider, ProviderResponse


class RecordingProvider(Provider):
    """Records every prompt object it receives; answers perfectly."""

    name = "recording"

    def __init__(self, tasks):
        super().__init__("recording")
        self.seen = []
        self._by_user = {}
        from scopeshift.prompt import build_prompt
        for t in tasks:
            self._by_user[build_prompt(t).user] = t

    def complete(self, prompt):
        self.seen.append(prompt)
        return ProviderResponse(text=reference_json(self._by_user[prompt.user]))


def test_trials_get_unique_ids(tasks):
    _, recs = run_eval(tasks, RecordingProvider(tasks), trials=3)
    ids = [r.trial_id for r in recs]
    assert len(ids) == 12 and len(set(ids)) == 12
    assert len({(r.task_id, r.trial_index) for r in recs}) == 12


def test_no_history_shared_between_trials(tasks):
    p = RecordingProvider(tasks)
    run_eval(tasks, p, trials=3)
    assert len(p.seen) == 12
    # same task -> identical prompt every trial; no previous answer appended
    by_user = {}
    for prompt in p.seen:
        by_user.setdefault(prompt.user, []).append(prompt)
    assert len(by_user) == 4
    for prompts in by_user.values():
        assert len(prompts) == 3 and len({(x.system, x.user) for x in prompts}) == 1
        assert len({id(x) for x in prompts}) == 3  # fresh prompt object per trial
    for prompt in p.seen:
        assert '"exception_status":' not in prompt.user
        assert "Mock answer" not in prompt.user and "rationale\": \"x" not in prompt.user


def test_tasks_not_mutated_by_run(tasks):
    before = [t.model_dump_json() for t in tasks]
    run_eval(tasks, RecordingProvider(tasks), trials=2)
    assert [t.model_dump_json() for t in tasks] == before


def test_run_ids_unique_per_run(tasks):
    a, _ = run_eval(tasks, RecordingProvider(tasks), trials=1)
    b, _ = run_eval(tasks, RecordingProvider(tasks), trials=1)
    assert a != b
