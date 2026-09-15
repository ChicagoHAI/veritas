"""Unit tests for the replicate heartbeat loop (`runner._replicate_with_heartbeat`).

The loop turns one uninterrupted agent call into a series of resumed ones so a
time-budget cutoff ends in a hand-off rather than a silent kill. Its decisions
are pure control flow over `_invoke_agent`'s outcome, so they are driven
here with a scripted fake provider and a fake clock — no subprocess, no agent.

What these pin down:

  - which session operation each call requests (start vs. resume)
  - which check-in message each call carries (continue / stuck / wrap-up)
  - that a failed resume is recovered from rather than ending the phase
  - that the transcript is never truncated after the first call
  - that the loop always terminates, and reports WHY it stopped

The last two matter most. A resume failure that silently truncates the phase
still looks like a clean run in every output veritas produces, and a loop that
fails to terminate hangs a benchmark sweep with no diagnostic at all.
"""

import pytest

from veritas.core import runner as runner_mod
from veritas.core.config import Config
from veritas.core.models.replication import ReplicationPlan, ReplicationStep
from veritas.core.runner import (
    MAX_RESUME_FAILURES,
    MIN_HEARTBEAT_SECONDS,
    STALL_THRESHOLD,
    WRAP_UP_MAX_SECONDS,
    ReplicationRunner,
)
from veritas.llm import AgentResult
from veritas.templates.prompt_generator import PromptGenerator

SESSION_INSTRUCTIONS = "<<full session instructions>>"

# Outcome kinds the fake provider can be scripted with.
TIMEOUT = "timeout"          # watchdog ended it — the normal end of a tick
SUCCESS = "success"          # agent finished on its own
FAILED = "failed"            # non-zero exit that was NOT the watchdog
FAILED_SLOW = "failed_slow"  # ditto, but after burning the whole tick


class FakeClock:
    """Monotonic clock the fake provider advances by hand.

    The loop measures elapsed wall time, so a provider that returns instantly
    would never exhaust the budget — this is what keeps the tests bounded.
    """

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now


class FakeProvider:
    """Scripted stand-in for `_invoke_agent`, recording every call."""

    def __init__(self, clock, script):
        self.clock = clock
        self.script = list(script)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        # Unscripted calls default to "the tick timed out", so a test only has
        # to script the outcomes it actually cares about.
        kind = self.script.pop(0) if self.script else TIMEOUT
        # A timed-out tick burns its whole timeout; a plain failure returns at
        # once, which is what makes a lost session cheap to detect. FAILED_SLOW
        # is the awkward middle: a resume that hangs before erroring, and the
        # only way the budget can run out while no session is live.
        burns_the_tick = kind in (TIMEOUT, FAILED_SLOW)
        self.clock.now += (kwargs.get("timeout") or 0) if burns_the_tick else 0.0
        return AgentResult(success=kind == SUCCESS, timed_out=kind == TIMEOUT)

    # -- accessors the assertions read ------------------------------------

    @property
    def prompts(self):
        return [c["prompt"] for c in self.calls]

    def session_id(self, i):
        return self.calls[i]["session"].session_id

    def starts_session(self, i):
        return self.calls[i]["session"].operation == "start"

    def resumes(self, i):
        return self.calls[i]["session"].operation == "resume"


@pytest.fixture
def checkin():
    """The rendered check-in messages, so assertions compare against the real
    template output rather than a copy that can drift from it."""
    g = PromptGenerator()
    return {k: g.generate_heartbeat_prompt(k) for k in g.HEARTBEAT_KINDS}


def make_runner(tmp_path, heartbeat=MIN_HEARTBEAT_SECONDS):
    repo = tmp_path / "repo"
    repo.mkdir()
    config = Config(
        repo_path=repo,
        output_dir=tmp_path / "out",
        replicate_heartbeat=heartbeat,
    )
    return ReplicationRunner(config)


def drive(tmp_path, monkeypatch, budget, script=(), heartbeat=MIN_HEARTBEAT_SECONDS):
    """Run the loop against a scripted provider; return (result, provider)."""
    runner = make_runner(tmp_path, heartbeat=heartbeat)
    clock = FakeClock()
    monkeypatch.setattr(runner_mod.time, "monotonic", clock.monotonic)

    fake = FakeProvider(clock, script)
    monkeypatch.setattr(runner, "_invoke_agent", fake)

    plan = ReplicationPlan(
        environment={},
        steps=[ReplicationStep(id=1, description="run it", command_hint="python x.py",
                               expected_outcome="results.json")],
    )
    result = runner._replicate_with_heartbeat(
        session_instructions=SESSION_INSTRUCTIONS,
        log_path=tmp_path / "transcript.jsonl",
        replication_plan=plan,
        budget=budget,
    )
    return result, fake


# -- normal completion ----------------------------------------------------


def test_agent_finishing_on_its_own_is_not_an_early_termination(tmp_path, monkeypatch):
    (terminated, reason), fake = drive(tmp_path, monkeypatch, budget=600, script=[SUCCESS])

    assert terminated is False
    assert reason == ""
    assert len(fake.calls) == 1, "no wrap-up is owed to an agent that finished"


def test_first_call_starts_a_session_with_the_full_instructions(tmp_path, monkeypatch):
    _, fake = drive(tmp_path, monkeypatch, budget=600, script=[SUCCESS])

    assert fake.starts_session(0)
    assert fake.prompts[0] == SESSION_INSTRUCTIONS
    assert fake.calls[0]["append"] is False, "the first call owns the transcript"


def test_later_calls_resume_the_same_session_and_append(tmp_path, monkeypatch, checkin):
    _, fake = drive(tmp_path, monkeypatch, budget=180, script=[TIMEOUT, SUCCESS])

    assert fake.resumes(1)
    assert fake.session_id(1) == fake.session_id(0)
    assert fake.prompts[1] == checkin["continue"]
    assert fake.calls[1]["append"] is True


# -- budget exhaustion and wrap-up ----------------------------------------


def test_budget_exhaustion_wraps_up_and_reports_the_cutoff(tmp_path, monkeypatch, checkin):
    (terminated, reason), fake = drive(tmp_path, monkeypatch, budget=120)

    assert terminated is True
    assert "120s replicate budget" in reason
    assert fake.prompts[-1] == checkin["wrap_up"]
    assert fake.resumes(len(fake.calls) - 1), "the wrap-up must reach the same session"


def test_wrap_up_is_capped_at_its_own_ceiling(tmp_path, monkeypatch):
    _, fake = drive(tmp_path, monkeypatch, budget=1200, heartbeat=1200)

    assert fake.calls[-1]["timeout"] == WRAP_UP_MAX_SECONDS


def test_wrap_up_never_outlasts_a_short_heartbeat(tmp_path, monkeypatch):
    _, fake = drive(tmp_path, monkeypatch, budget=120, heartbeat=MIN_HEARTBEAT_SECONDS)

    assert fake.calls[-1]["timeout"] == MIN_HEARTBEAT_SECONDS


def test_a_sliver_of_budget_goes_to_the_wrap_up_not_another_tick(tmp_path, monkeypatch, checkin):
    """budget 100 with a 60s heartbeat leaves 40s — less than a session needs
    to become resumable, so it must not be spent starting the agent again."""
    (terminated, _), fake = drive(tmp_path, monkeypatch, budget=100)

    assert terminated is True
    assert len(fake.calls) == 2
    assert fake.prompts[1] == checkin["wrap_up"]


def test_the_first_tick_always_runs_even_on_a_tiny_budget(tmp_path, monkeypatch):
    """The sliver check must not skip the work entirely."""
    _, fake = drive(tmp_path, monkeypatch, budget=5)

    assert len(fake.calls) >= 1
    assert fake.prompts[0] == SESSION_INSTRUCTIONS
    assert fake.calls[0]["timeout"] == 5


# -- stall detection ------------------------------------------------------


def test_a_stalled_run_switches_from_continue_to_the_nudge(tmp_path, monkeypatch, checkin):
    """No evidence ever appears on disk, so every tick after the first shows no
    objective progress; past STALL_THRESHOLD the message has to change."""
    _, fake = drive(tmp_path, monkeypatch, budget=600)

    assert checkin["stuck"] in fake.prompts, "a stuck run kept being told to continue"
    first_nudge = fake.prompts.index(checkin["stuck"])
    assert fake.prompts[1:first_nudge] == [checkin["continue"]] * (first_nudge - 1)
    assert first_nudge > STALL_THRESHOLD, "nudged before the threshold was passed"


# -- resume failure -------------------------------------------------------


def test_a_failed_resume_starts_a_replacement_session(tmp_path, monkeypatch):
    (terminated, _), fake = drive(
        tmp_path, monkeypatch, budget=600, script=[TIMEOUT, FAILED],
    )

    assert fake.starts_session(2), "expected a replacement session after the failed resume"
    assert fake.session_id(2) != fake.session_id(0), "reused the id that just failed"
    assert terminated is True, "the run was still cut short by the clock"


def test_the_replacement_session_is_told_to_resume_from_disk(tmp_path, monkeypatch, checkin):
    _, fake = drive(tmp_path, monkeypatch, budget=600, script=[TIMEOUT, FAILED])

    assert fake.prompts[2].startswith(SESSION_INSTRUCTIONS)
    assert checkin["fresh_session"] in fake.prompts[2]


def test_a_replacement_session_appends_to_the_existing_transcript(tmp_path, monkeypatch):
    """Truncating here would erase the record of everything already done."""
    _, fake = drive(tmp_path, monkeypatch, budget=600, script=[TIMEOUT, FAILED])

    assert fake.calls[2]["append"] is True


def test_resume_failures_stop_the_phase_once_the_ceiling_is_passed(tmp_path, monkeypatch):
    """Each replacement works, then loses its session again. The ceiling counts
    recoveries across the whole phase, not consecutive ones."""
    script = [TIMEOUT, FAILED] * (MAX_RESUME_FAILURES + 1)
    (terminated, reason), fake = drive(tmp_path, monkeypatch, budget=6000, script=script)

    assert terminated is True
    assert "resume failed" in reason.lower()
    assert str(MAX_RESUME_FAILURES + 1) in reason
    assert len(fake.calls) == len(script), "kept going past the retry ceiling"


def test_a_replacement_that_cannot_start_is_reported_as_a_cutoff(tmp_path, monkeypatch):
    """Back-to-back failures mean the second one is a session START failing,
    not a resume — sessions are broken and there is nothing left to try. The
    earlier tick still did real work, so this must not read as a clean run."""
    (terminated, reason), fake = drive(
        tmp_path, monkeypatch, budget=6000, script=[TIMEOUT, FAILED, FAILED],
    )

    assert terminated is True
    assert "replacement session" in reason.lower()
    assert len(fake.calls) == 3


def test_no_wrap_up_is_sent_to_a_session_that_was_never_used(tmp_path, monkeypatch, checkin):
    """A replacement id minted as the budget ran out has no live session behind
    it; resuming it would fail exactly as the resume before it did. Reachable
    only when the failed resume itself burned the remaining time."""
    (terminated, _), fake = drive(
        tmp_path, monkeypatch, budget=170, script=[TIMEOUT, FAILED_SLOW],
    )

    assert terminated is True
    assert checkin["wrap_up"] not in fake.prompts
    assert len(fake.calls) == 2


# -- genuine failure ------------------------------------------------------


def test_a_failed_first_call_is_a_real_failure_not_a_lost_session(tmp_path, monkeypatch):
    """There is no session to recover — the one that failed is the one just
    started, so this is the agent failing, not the resume mechanism."""
    (terminated, reason), fake = drive(tmp_path, monkeypatch, budget=600, script=[FAILED])

    assert terminated is False
    assert reason == ""
    assert len(fake.calls) == 1


# -- termination ----------------------------------------------------------


@pytest.mark.parametrize("budget", [1, 59, 60, 61, 100, 600, 3600])
def test_the_loop_always_terminates(tmp_path, monkeypatch, budget):
    """Every budget, with every tick timing out: the loop must end on its own
    and account for why. A hang here strands a whole benchmark sweep."""
    (terminated, reason), fake = drive(tmp_path, monkeypatch, budget=budget)

    assert terminated is True
    assert reason
    assert fake.calls
