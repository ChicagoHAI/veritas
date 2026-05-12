"""Main runner for replication evaluation."""

import json
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from veritas.core.config import Config, OUTPUT_SUBDIRS
from veritas.core.pipeline_state import PipelineState
from veritas.core.checklist import parse_checklist_response
from veritas.core.models.checklist import Checklist
from veritas.core.models.replication import ReplicationPlan, ExecutionEvidence
from veritas.core.models.fix_severity import FixSeverityAssessment
from veritas.core.replication import (
    parse_replication_plan_response,
    gather_evidence,
    _extract_json,
)
from veritas.core.plan_extractor import PlanExtractor
from veritas.core.report_generator import ReportGenerator
from veritas.templates.prompt_generator import PromptGenerator
from veritas.utils.security import sanitize_logs_directory, sanitize_text

# Provider invocation tables. Each provider has a CLI command (cli name plus
# any required positional subcommand or print flag), a transcript-output flag
# set (so the JSONL stream lands on stdout for capture), and a permission
# flag set (so non-interactive runs don't block on confirmation prompts).
CLI_COMMANDS: Dict[str, Tuple[str, ...]] = {
    "claude": ("claude", "-p"),
    "codex":  ("codex", "exec"),
    "gemini": ("gemini",),
}

TRANSCRIPT_FLAGS: Dict[str, Tuple[str, ...]] = {
    "claude": ("--verbose", "--output-format", "stream-json"),
    "codex":  ("--json",),
    "gemini": ("--output-format", "stream-json"),
}

PERMISSION_FLAGS: Dict[str, Tuple[str, ...]] = {
    "claude": ("--dangerously-skip-permissions",),
    "codex":  ("--full-auto",),
    "gemini": ("--yolo", "--skip-trust"),
}


@dataclass
class EvaluationResult:
    """Result of a single evaluation."""
    name: str
    success: bool
    items: Optional[List[Dict[str, str]]] = None
    pass_rate: Optional[float] = None
    error: Optional[str] = None
    output_path: Optional[Path] = None


@dataclass
class RunResult:
    """Result of the full replication run."""
    success: bool
    evaluations: Optional[List[EvaluationResult]] = None
    report_path: Optional[Path] = None
    pdf_path: Optional[Path] = None
    error: Optional[str] = None


class ReplicationRunner:
    """Orchestrates the replication evaluation pipeline."""

    def __init__(self, config: Config):
        self.config = config
        self.prompt_generator = PromptGenerator()
        self.plan_extractor = PlanExtractor()
        self.report_generator = ReportGenerator()

    def run(self) -> RunResult:
        """Run the full pipeline: analyze -> replicate -> assess fixes -> evaluate -> report.

        Resumable: completed phases recorded in ``<output>/.veritas/pipeline_state.json``
        are skipped on re-invocation. Pass ``--restart`` at the CLI level to discard state.
        """
        try:
            self._setup_output_dir()
            state = PipelineState(self.config.output_dir)

            if state.state.get('inputs') is None:
                state.record_inputs(self.config.repo_path, self.config.paper_path)
            else:
                state.validate_inputs(self.config.repo_path, self.config.paper_path)
                self._announce_resume(state)

            plan_path = self._extract_plan()

            # analyze
            if state.is_stage_completed('analyze'):
                print("[OK] analyze: skipped (already completed)")
                checklist, replication_plan = self._load_analyze_artifacts()
            else:
                state.start_stage('analyze')
                try:
                    checklist, replication_plan = self._analyze()
                    state.complete_stage('analyze', success=True)
                except Exception:
                    state.complete_stage('analyze', success=False)
                    raise

            # replicate
            if state.is_stage_completed('replicate'):
                print("[OK] replicate: skipped (already completed)")
                evidence = gather_evidence(self.config.replication_dir)
            else:
                state.start_stage('replicate')
                try:
                    evidence = self._replicate(replication_plan)
                    state.complete_stage('replicate', success=True)
                except Exception:
                    state.complete_stage('replicate', success=False)
                    raise

            # assess_fixes
            if state.is_stage_completed('assess_fixes'):
                print("[OK] assess_fixes: skipped (already completed)")
                fix_assessment = self._load_fix_assessment()
            else:
                state.start_stage('assess_fixes')
                try:
                    fix_assessment = self._assess_fixes(evidence)
                    state.complete_stage('assess_fixes', success=True)
                except Exception:
                    state.complete_stage('assess_fixes', success=False)
                    raise

            # evaluate
            already_done = state.get_stage_outputs('evaluate').get('completed_categories', [])
            missing_categories = [name for name in self.config.evaluations if name not in already_done]

            if state.is_stage_completed('evaluate') and not missing_categories:
                print("[OK] evaluate: skipped (already completed)")
                results = self._load_evaluate_artifacts()
            else:
                if state.get_stage_status('evaluate') != 'in_progress':
                    state.start_stage('evaluate')
                    # start_stage zeroes the outputs dict; restore the categories
                    # completed in prior runs so _evaluate_with_resume can skip
                    # them rather than re-running.
                    if already_done:
                        state.update_stage_outputs('evaluate', {'completed_categories': already_done})
                try:
                    results = self._evaluate_with_resume(
                        checklist, evidence, plan_path, fix_assessment,
                        state, already_done=already_done,
                    )
                    state.complete_stage('evaluate', success=True)
                except Exception:
                    state.complete_stage('evaluate', success=False)
                    raise

            report_path, pdf_path = self._report(results, evidence, fix_assessment)
            state.mark_completed()

            return RunResult(
                success=True,
                evaluations=results,
                report_path=report_path,
                pdf_path=pdf_path,
            )

        except Exception as e:
            return RunResult(success=False, error=str(e))

        finally:
            try:
                sanitize_logs_directory(self.config.output_dir)
            except Exception:
                pass

    def _setup_output_dir(self):
        """Create the output directory structure."""
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        for subdir in OUTPUT_SUBDIRS:
            (self.config.output_dir / subdir).mkdir(exist_ok=True)

    def _extract_plan(self) -> Optional[Path]:
        """Get existing plan or extract from paper."""
        repo_plan = self.config.repo_path / "plan.md"
        if repo_plan.exists():
            return repo_plan

        if self.config.has_plan:
            return self.config.plan_path

        if self.config.has_paper:
            plan_content = self.plan_extractor.extract(
                self.config.paper_path, with_evidence=True
            )
            plan_path = self.config.extracted_plan_path
            plan_path.write_text(plan_content, encoding='utf-8')
            return plan_path

        return None

    # -- Phase 1: Analyze --------------------------------------------------

    def _analyze(self) -> Tuple[Checklist, Optional[ReplicationPlan]]:
        """Phase 1: Generate checklist and replication plan."""
        checklist = self._generate_checklist()
        replication_plan = self._generate_replication_plan(checklist)
        return checklist, replication_plan

    def _generate_checklist(self) -> Checklist:
        """Generate a personalized checklist."""
        print("Generating personalized checklist...")

        prompt = self.prompt_generator.generate_checklist_prompt(
            repo_path=self.config.repo_path,
            output_dir=self.config.output_dir,
            paper_path=self.config.paper_path if self.config.has_paper else None,
        )

        prompt_path = self.config.prompts_dir / "checklist_generation_prompt.txt"
        prompt_path.write_text(prompt, encoding='utf-8')

        output_json_path = self.config.checklist_path
        log_path = self.config.checklist_transcript_path

        success = self._invoke_provider(
            prompt=prompt,
            working_dir=self.config.repo_path,
            log_path=log_path,
            timeout=self.config.analyze_timeout,
        )

        if not success:
            raise RuntimeError(
                f"Checklist generation failed: provider invocation did not succeed (transcript: {log_path})"
            )

        if not output_json_path.exists():
            raise RuntimeError(
                f"Checklist generation failed: agent did not write {output_json_path}"
            )

        response_text = output_json_path.read_text(encoding='utf-8').strip()
        if not response_text:
            raise RuntimeError(
                f"Checklist generation failed: {output_json_path} is empty"
            )

        try:
            checklist = parse_checklist_response(response_text)
        except ValueError as e:
            print(f"  Warning: Could not parse checklist: {e}")
            print("  Retrying with repair prompt...")
            checklist = self._repair_json_response(
                original_prompt=prompt,
                broken_output=response_text,
                output_path=output_json_path,
                log_path=log_path,
                parser=parse_checklist_response,
                timeout=self.config.analyze_timeout,
            )

        if checklist is None:
            raise RuntimeError(
                "Checklist generation failed: could not parse response even after repair"
            )

        output_json_path.write_text(
            json.dumps(checklist.to_dict(), indent=2), encoding='utf-8'
        )

        print(f"  Generated {len(checklist.items)} checklist items across {len(checklist.categories)} categories")
        return checklist

    def _generate_replication_plan(self, checklist: Checklist) -> Optional[ReplicationPlan]:
        """Generate a replication plan based on the checklist."""
        print("Generating replication plan...")

        prompt = self.prompt_generator.generate_replication_plan_prompt(
            repo_path=self.config.repo_path,
            output_dir=self.config.output_dir,
            checklist_items=checklist.items,
            paper_path=self.config.paper_path if self.config.has_paper else None,
            mode=self.config.mode,
        )

        prompt_path = self.config.prompts_dir / "replication_plan_prompt.txt"
        prompt_path.write_text(prompt, encoding='utf-8')

        output_path = self.config.replication_plan_path
        log_path = self.config.replication_plan_transcript_path

        success = self._invoke_provider(
            prompt=prompt,
            working_dir=self.config.repo_path,
            log_path=log_path,
            timeout=self.config.analyze_timeout,
        )

        if not success:
            print(f"  Warning: Provider invocation did not succeed (transcript: {log_path}), skipping replication phase")
            return None

        if not output_path.exists():
            print(f"  Warning: Agent did not write {output_path}, skipping replication phase")
            return None

        response_text = output_path.read_text(encoding='utf-8').strip()
        if not response_text:
            print(f"  Warning: {output_path} is empty, skipping replication phase")
            return None

        try:
            plan = parse_replication_plan_response(response_text)
        except ValueError as e:
            print(f"  Warning: Could not parse replication plan: {e}")
            print("  Retrying with repair prompt...")
            plan = self._repair_json_response(
                original_prompt=prompt,
                broken_output=response_text,
                output_path=output_path,
                log_path=log_path,
                parser=parse_replication_plan_response,
                timeout=self.config.analyze_timeout,
            )

        if plan is None:
            return None

        output_path.write_text(
            json.dumps(plan.to_dict(), indent=2), encoding='utf-8'
        )
        print(f"  Generated replication plan with {len(plan.steps)} steps")
        return plan

    def _repair_json_response(
        self,
        original_prompt: str,
        broken_output: str,
        output_path: Path,
        log_path: Path,
        parser,
        timeout: Optional[int],
    ):
        """Re-prompt the provider to fix invalid JSON output.

        Appends the broken output to the original prompt and asks the
        provider to return valid JSON only. The transcript of this
        re-invocation is appended to ``log_path`` so the original failed
        attempt and the repair attempt share one file.
        """
        repair_prompt = (
            original_prompt
            + "\n\n---\n\n"
            + "Your last output was not valid JSON. Here is what you returned:\n\n"
            + broken_output[:2000]
            + "\n\nPlease return ONLY valid JSON, with no explanation or markdown formatting."
            + "\n\nCommon JSON mistakes to avoid:"
            + "\n- Double quotes inside strings MUST be escaped: use \\\" not \""
            + "\n- Backslash-single-quote (\\') is not valid JSON — just use '"
            + "\n- Regex patterns in strings need double-escaped backslashes: use \\\\s not \\s"
            + "\n- If a command_hint contains Python code with double quotes, escape them"
        )

        success = self._invoke_provider(
            prompt=repair_prompt,
            working_dir=self.config.repo_path,
            log_path=log_path,
            timeout=timeout,
            append=True,
        )

        if not success:
            print("  Warning: Repair invocation did not succeed")
            return None

        if not output_path.exists():
            print(f"  Warning: Repair did not produce {output_path}")
            return None

        response_text = output_path.read_text(encoding='utf-8').strip()
        if not response_text:
            print(f"  Warning: Repair produced empty {output_path}")
            return None

        try:
            return parser(response_text)
        except ValueError as e:
            print(f"  Warning: Repair also failed: {e}")
            return None

    # -- Phase 2: Replicate ------------------------------------------------

    def _replicate(self, replication_plan: Optional[ReplicationPlan]) -> Optional[ExecutionEvidence]:
        """Phase 2: Execute replication via the configured provider.

        Runs as an in-process subprocess. When the whole veritas CLI is
        running inside the veritas Docker image (the production path),
        the provider sees `/workspace/repo` as the read-only repo and
        `/workspace/output` as the writable scratch space. When run
        outside Docker (dev-time), paths come from `self.config` as-is.
        """
        if replication_plan is None:
            print("No replication plan available, skipping replication phase")
            return None

        print("Running replication phase...")

        session_instructions = self.prompt_generator.generate_replication_session_prompt(
            replication_plan,
            paper_path=self.config.paper_path,
        )

        log_path = self.config.replication_transcript_path
        log_path.parent.mkdir(parents=True, exist_ok=True)

        prompt_path = self.config.prompts_dir / "replication_session_prompt.txt"
        prompt_path.write_text(session_instructions, encoding='utf-8')

        success = self._invoke_provider(
            prompt=session_instructions,
            working_dir=self.config.repo_path,
            log_path=log_path,
            timeout=self.config.replicate_timeout,
        )

        if not success:
            print(f"  Warning: Provider invocation did not succeed (transcript: {log_path})")

        evidence = gather_evidence(self.config.replication_dir)

        if evidence:
            print(f"  Replication completed: {evidence.steps_succeeded}/{evidence.steps_attempted} steps succeeded")
        else:
            print("  Warning: No evidence collected from replication")

        return evidence

    # -- Fix Assessment ----------------------------------------------------

    def _assess_fixes(self, evidence: Optional[ExecutionEvidence]) -> FixSeverityAssessment:
        """Assess severity of fixes applied during replication.

        Runs a separate LLM pass over the fix records. Skips the LLM call
        entirely when no fixes were applied.
        """
        if evidence is None:
            return FixSeverityAssessment.empty()

        all_fixes = evidence.all_fixes_applied
        if not all_fixes:
            print("No fixes applied during replication, skipping severity assessment")
            return FixSeverityAssessment.empty()

        print(f"Assessing severity of {len(all_fixes)} fix(es)...")

        prompt = self.prompt_generator.generate_fix_severity_prompt(
            fixes=[f.to_dict() for f in all_fixes],
            output_dir=self.config.output_dir,
        )

        prompt_path = self.config.prompts_dir / "fix_severity_prompt.txt"
        prompt_path.write_text(prompt, encoding='utf-8')

        output_path = self.config.fix_severity_path
        log_path = self.config.fix_severity_transcript_path

        success = self._invoke_provider(
            prompt=prompt,
            working_dir=self.config.output_dir,
            log_path=log_path,
            timeout=self.config.evaluate_timeout,
        )

        if not success:
            print(f"  Warning: Provider invocation did not succeed (transcript: {log_path})")
            return FixSeverityAssessment.empty()

        if not output_path.exists():
            print(f"  Warning: Agent did not write {output_path}")
            return FixSeverityAssessment.empty()

        response_text = output_path.read_text(encoding='utf-8').strip()
        if not response_text:
            print(f"  Warning: {output_path} is empty")
            return FixSeverityAssessment.empty()

        try:
            raw = _extract_json(response_text)
            data = json.loads(raw)
            assessment = FixSeverityAssessment.from_dict(data)
            output_path.write_text(
                json.dumps(assessment.to_dict(), indent=2), encoding='utf-8'
            )
            print(f"  Fix assessment: {assessment.minor_count} minor, {assessment.major_count} major, {assessment.critical_count} critical")
            return assessment
        except (ValueError, json.JSONDecodeError) as e:
            print(f"  Warning: Could not parse fix severity assessment: {e}")
            return FixSeverityAssessment.empty()

    # -- Phase 3: Evaluate -------------------------------------------------

    def _run_single_evaluation(
        self,
        eval_name: str,
        checklist_items: List,
        plan_path: Optional[Path],
        evidence: Optional[ExecutionEvidence] = None,
        fix_assessment: Optional[FixSeverityAssessment] = None,
    ) -> EvaluationResult:
        """Run scoring for one category's checklist items."""
        try:
            prompt = self.prompt_generator.generate_scoring_prompt(
                category_name=eval_name,
                checklist_items=checklist_items,
                repo_path=self.config.repo_path,
                plan_path=plan_path,
                output_dir=self.config.output_dir,
                evidence=evidence,
                fix_assessment=fix_assessment,
            )

            prompt_path = self.config.prompts_dir / f"{eval_name}_prompt.txt"
            prompt_path.write_text(prompt, encoding='utf-8')

            output_json_path = self.config.evaluation_path(eval_name)
            log_path = self.config.evaluation_transcript_path(eval_name)

            success = self._invoke_provider(
                prompt=prompt,
                working_dir=self.config.repo_path,
                log_path=log_path,
                timeout=self.config.evaluate_timeout,
            )

            if not success:
                return EvaluationResult(
                    name=eval_name, success=False,
                    error=f"Provider invocation failed (transcript: {log_path})",
                )

            if not output_json_path.exists():
                return EvaluationResult(
                    name=eval_name, success=False,
                    error=f"Output file not produced: {output_json_path}",
                )

            with open(output_json_path, encoding='utf-8') as f:
                data = json.load(f)

            return EvaluationResult(
                name=eval_name,
                success=True,
                items=data.get("items", []),
                pass_rate=data.get("pass_rate"),
                output_path=output_json_path,
            )

        except Exception as e:
            return EvaluationResult(name=eval_name, success=False, error=str(e))

    # -- Report ------------------------------------------------------------

    def _report(self, results, evidence=None, fix_assessment=None):
        """Generate the final report."""
        return self.report_generator.generate_from_results(
            results=results,
            config=self.config,
            output_dir=self.config.output_dir,
            generate_pdf=self.config.generate_pdf,
            evidence=evidence,
            fix_assessment=fix_assessment,
        )

    # -- Resume helpers ----------------------------------------------------

    def _announce_resume(self, state: PipelineState) -> None:
        """Print the resume banner. Called when state file already had stages."""
        created = state.state.get('created_at', 'unknown time')
        print(f"WARNING: Found existing pipeline state from {created}. Resuming.")
        print("   Pass --restart to start fresh.")

    def _load_analyze_artifacts(self) -> Tuple[Checklist, Optional[ReplicationPlan]]:
        """Load checklist and replication plan from disk for a skipped analyze phase."""
        with open(self.config.checklist_path, encoding='utf-8') as f:
            checklist = Checklist.from_dict(json.load(f))

        replication_plan: Optional[ReplicationPlan] = None
        if self.config.replication_plan_path.exists():
            with open(self.config.replication_plan_path, encoding='utf-8') as f:
                replication_plan = ReplicationPlan.from_dict(json.load(f))
        return checklist, replication_plan

    def _load_fix_assessment(self) -> FixSeverityAssessment:
        """Load fix severity assessment from disk (or empty if no fixes were applied)."""
        if not self.config.fix_severity_path.exists():
            return FixSeverityAssessment.empty()
        with open(self.config.fix_severity_path, encoding='utf-8') as f:
            return FixSeverityAssessment.from_dict(json.load(f))

    def _load_evaluation_result(self, eval_name: str) -> EvaluationResult:
        """Read a single per-category evaluation JSON and build an EvaluationResult."""
        output_path = self.config.evaluation_path(eval_name)
        if not output_path.exists():
            return EvaluationResult(
                name=eval_name, success=False,
                error=f"Output file missing on resume: {output_path.name}",
            )
        with open(output_path, encoding='utf-8') as f:
            data = json.load(f)
        return EvaluationResult(
            name=eval_name,
            success=True,
            items=data.get("items", []),
            pass_rate=data.get("pass_rate"),
            output_path=output_path,
        )

    def _load_evaluate_artifacts(self) -> List[EvaluationResult]:
        """Load per-category evaluation results from disk for a skipped evaluate phase."""
        return [self._load_evaluation_result(name) for name in self.config.evaluations]

    def _evaluate_with_resume(
        self,
        checklist: Checklist,
        evidence: Optional[ExecutionEvidence],
        plan_path: Optional[Path],
        fix_assessment: Optional[FixSeverityAssessment],
        state: PipelineState,
        already_done: List[str],
    ) -> List[EvaluationResult]:
        """Score checklist items per category, skipping those already completed in a previous run.

        Records each newly-completed category in the state's ``outputs.completed_categories``
        list as it goes, so an interruption mid-loop can be resumed at the right point.
        """
        results: List[EvaluationResult] = []
        completed = list(already_done)

        for eval_name in self.config.evaluations:
            if eval_name in already_done:
                print(f"  Skipping {eval_name} (already complete from previous run)")
                results.append(self._load_evaluation_result(eval_name))
                continue

            print(f"Running {eval_name} evaluation...")
            items = checklist.get_items_by_category(eval_name)
            if not items:
                print(f"  Skipping {eval_name} - no checklist items generated for this category")
                results.append(EvaluationResult(
                    name=eval_name, success=True, items=[], pass_rate=None,
                ))
                completed.append(eval_name)
                state.update_stage_outputs('evaluate', {'completed_categories': completed})
                continue

            result = self._run_single_evaluation(
                eval_name, items, plan_path, evidence, fix_assessment,
            )
            results.append(result)

            if result.success:
                pct = f"{result.pass_rate * 100:.1f}%" if result.pass_rate is not None else "N/A"
                print(f"  {eval_name} completed - {pct}")
                completed.append(eval_name)
                state.update_stage_outputs('evaluate', {'completed_categories': completed})
            else:
                print(f"  {eval_name} failed: {result.error}")

        return results

    # -- Provider Invocation -----------------------------------------------

    def _invoke_provider(
        self,
        prompt: str,
        working_dir: Path,
        log_path: Path,
        timeout: Optional[int],
        append: bool = False,
    ) -> bool:
        """Run the configured provider as a subprocess; stream its JSONL
        transcript to ``log_path``; return True on success.

        The agent is expected to write its actual results (checklist JSON,
        replication plan JSON, etc.) to known disk paths during the run.
        ``log_path`` only captures the conversation transcript — it is
        never the source of the agent's answer.

        Wall-clock timeout enforcement uses a daemon ``threading.Timer``
        that calls ``process.kill()`` after ``timeout`` seconds. A plain
        ``process.wait(timeout=...)`` after a streaming loop does not
        enforce a wall-clock limit, since the loop blocks until the
        subprocess closes stdout (which happens when it exits anyway).

        With ``append=True`` the transcript file is opened in append mode,
        which is used by the repair-re-invocation path so the original
        failed attempt and the repair attempt land in one transcript file.
        """
        provider = self.config.provider.lower()
        if provider not in CLI_COMMANDS:
            raise ValueError(f"Unknown provider: {provider}")

        try:
            cli = self._resolve_cli(CLI_COMMANDS[provider][0])
        except FileNotFoundError as e:
            print(f"  {e}")
            return False

        cmd: List[str] = [
            cli,
            *CLI_COMMANDS[provider][1:],
            *TRANSCRIPT_FLAGS[provider],
            *PERMISSION_FLAGS[provider],
        ]

        log_path.parent.mkdir(parents=True, exist_ok=True)
        open_mode = "a" if append else "w"

        try:
            process = subprocess.Popen(
                cmd,
                cwd=working_dir,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except FileNotFoundError as e:
            print(f"  {e}")
            return False
        except Exception as e:
            print(f"  Error invoking {provider}: {e}")
            return False

        process.stdin.write(prompt)
        process.stdin.close()

        timed_out = False
        watchdog: Optional[threading.Timer] = None
        if timeout is not None and timeout > 0:
            def _kill_on_timeout() -> None:
                nonlocal timed_out
                timed_out = True
                try:
                    process.kill()
                except Exception:
                    pass

            watchdog = threading.Timer(timeout, _kill_on_timeout)
            watchdog.daemon = True
            watchdog.start()

        try:
            with open(log_path, open_mode, encoding="utf-8") as log_f:
                for line in iter(process.stdout.readline, ""):
                    line = sanitize_text(line)
                    print(line, end="")
                    log_f.write(line)
            return_code = process.wait()
        finally:
            if watchdog is not None:
                watchdog.cancel()
                watchdog.join()

        if return_code == 0:
            return True
        if timed_out:
            print(f"  Timeout after {timeout}s")
            return False
        return False

    @staticmethod
    def _resolve_cli(name: str) -> str:
        """Resolve a CLI tool name to its full path.

        On Windows, npm installs .cmd shims that subprocess can't find
        without shell=True. This resolves the full path instead.
        """
        resolved = shutil.which(name)
        if resolved is None:
            raise FileNotFoundError(f"{name} CLI not found on PATH")
        return resolved
