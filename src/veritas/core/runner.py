"""Main runner for replication evaluation."""

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from veritas.core.config import Config
from veritas.core.checklist import Checklist, parse_checklist_response
from veritas.core.models import ReplicationPlan, ExecutionEvidence
from veritas.core.evidence import parse_replication_plan_response, gather_evidence
from veritas.core.container import (
    is_docker_available,
    has_gpu,
    build_container_command,
    execute_in_container,
)
from veritas.core.plan_extractor import PlanExtractor
from veritas.core.report_generator import ReportGenerator
from veritas.templates.prompt_generator import PromptGenerator
from veritas.utils.pdf import read_pdf
from veritas.utils.security import sanitize_log_file


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
    """Orchestrates the three-phase replication evaluation process."""

    def __init__(self, config: Config):
        self.config = config
        self.prompt_generator = PromptGenerator()
        self.plan_extractor = PlanExtractor()
        self.report_generator = ReportGenerator()

    def run(self) -> RunResult:
        """Run the full three-phase pipeline: analyze -> replicate -> evaluate -> report."""
        try:
            self._setup_output_dir()
            plan_path = self._extract_plan()
            checklist, replication_plan = self._analyze()
            evidence = self._replicate(replication_plan)
            results = self._evaluate(checklist, evidence, plan_path)
            report_path, pdf_path = self._report(results, evidence)

            return RunResult(
                success=True,
                evaluations=results,
                report_path=report_path,
                pdf_path=pdf_path,
            )

        except Exception as e:
            return RunResult(success=False, error=str(e))

    def _setup_output_dir(self):
        """Create the output directory structure."""
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        (self.config.output_dir / "replication").mkdir(exist_ok=True)

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
            plan_path = self.config.output_dir / "extracted_plan.md"
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

        paper_text = None
        if self.config.has_paper:
            paper_text = read_pdf(self.config.paper_path)

        prompt = self.prompt_generator.generate_checklist_prompt(
            repo_path=self.config.repo_path,
            output_dir=self.config.output_dir,
            paper_text=paper_text,
        )

        prompt_path = self.config.output_dir / "checklist_generation_prompt.txt"
        prompt_path.write_text(prompt, encoding='utf-8')

        output_json_path = self.config.output_dir / "checklist.json"
        stdout = self._invoke_provider(
            prompt=prompt,
            working_dir=self.config.repo_path,
            output_path=output_json_path,
        )

        if stdout is None:
            raise RuntimeError("Checklist generation failed: provider returned no output")

        response_text = None
        if output_json_path.exists():
            response_text = output_json_path.read_text(encoding='utf-8')
        elif stdout:
            response_text = stdout

        if not response_text:
            raise RuntimeError("Checklist generation failed: no output to parse")

        checklist = parse_checklist_response(response_text)

        output_json_path.write_text(
            json.dumps(checklist.to_dict(), indent=2), encoding='utf-8'
        )

        print(f"  Generated {len(checklist.items)} checklist items across {len(checklist.categories)} categories")
        return checklist

    def _generate_replication_plan(self, checklist: Checklist) -> Optional[ReplicationPlan]:
        """Generate a replication plan based on the checklist."""
        print("Generating replication plan...")

        paper_text = None
        if self.config.has_paper:
            paper_text = read_pdf(self.config.paper_path)

        prompt = self.prompt_generator.generate_replication_plan_prompt(
            repo_path=self.config.repo_path,
            output_dir=self.config.output_dir,
            paper_text=paper_text,
            checklist_items=checklist.items,
        )

        prompt_path = self.config.output_dir / "replication_plan_prompt.txt"
        prompt_path.write_text(prompt, encoding='utf-8')

        output_path = self.config.output_dir / "replication_plan.json"
        stdout = self._invoke_provider(
            prompt=prompt,
            working_dir=self.config.repo_path,
            output_path=output_path,
        )

        if stdout is None:
            print("  Warning: Replication plan generation failed, skipping replication phase")
            return None

        response_text = None
        if output_path.exists():
            response_text = output_path.read_text(encoding='utf-8')
        elif stdout:
            response_text = stdout

        if not response_text:
            print("  Warning: No replication plan output, skipping replication phase")
            return None

        try:
            plan = parse_replication_plan_response(response_text)
            output_path.write_text(
                json.dumps(plan.to_dict(), indent=2), encoding='utf-8'
            )
            print(f"  Generated replication plan with {len(plan.steps)} steps")
            return plan
        except ValueError as e:
            print(f"  Warning: Could not parse replication plan: {e}")
            return None

    # -- Phase 2: Replicate ------------------------------------------------

    def _replicate(self, replication_plan: Optional[ReplicationPlan]) -> Optional[ExecutionEvidence]:
        """Phase 2: Execute replication inside Docker container."""
        if not self.config.use_docker:
            print("Docker disabled (--no-docker), skipping replication phase")
            return None

        if replication_plan is None:
            print("No replication plan available, skipping replication phase")
            return None

        if not is_docker_available():
            print("Warning: Docker not available, skipping replication phase")
            print("  Install Docker and run 'veritas build-image' for full replication")
            return None

        print("Running replication inside Docker container...")

        session_instructions = self.prompt_generator.generate_replication_session_prompt(
            replication_plan,
        )

        provider_cmd = self._get_provider_cmd()

        gpu = self.config.gpu and has_gpu()

        # Look for .env file in veritas config dir (NOT the evaluated repo)
        env_file = Path.home() / ".veritas" / ".env"
        if not env_file.exists():
            env_file = None

        cmd = build_container_command(
            repo_path=self.config.repo_path,
            output_dir=self.config.output_dir,
            image=self.config.docker_image,
            provider_cmd=provider_cmd,
            gpu=gpu,
            env_file=env_file,
        )

        log_path = self.config.output_dir / "replication" / "execution_stdout.log"

        def _on_output(line: str):
            print(f"  [container] {line}", end="")

        returncode = execute_in_container(
            cmd=cmd,
            session_instructions=session_instructions,
            log_path=log_path,
            timeout=self.config.replication_timeout,
            on_output=_on_output,
        )

        if returncode != 0:
            print(f"  Warning: Container exited with code {returncode}")

        # Sanitize logs to redact any leaked API keys
        sanitize_log_file(log_path)

        evidence = gather_evidence(self.config.output_dir / "replication")

        if evidence:
            print(f"  Replication completed: {evidence.steps_succeeded}/{evidence.steps_attempted} steps succeeded")
        else:
            print("  Warning: No evidence collected from replication")

        return evidence

    def _get_provider_cmd(self) -> List[str]:
        """Get the CLI command for the configured provider."""
        provider = self.config.provider.lower()
        if provider == "claude":
            return ["claude", "-p", "--dangerously-skip-permissions", "--output-format", "text"]
        elif provider == "codex":
            return ["codex", "exec", "--full-auto", "-"]
        elif provider == "gemini":
            return ["gemini", "-p"]
        else:
            raise ValueError(f"Unknown provider: {provider}")

    # -- Phase 3: Evaluate -------------------------------------------------

    def _evaluate(
        self,
        checklist: Checklist,
        evidence: Optional[ExecutionEvidence],
        plan_path: Optional[Path],
    ) -> List[EvaluationResult]:
        """Phase 3: Score checklist items using evidence."""
        results = []

        for eval_name in self.config.evaluations:
            print(f"Running {eval_name} evaluation...")

            items = checklist.get_items_by_category(eval_name)
            if not items:
                print(f"  Skipping {eval_name} — no checklist items generated for this category")
                results.append(EvaluationResult(
                    name=eval_name, success=True, items=[], pass_rate=None,
                ))
                continue

            result = self._run_single_evaluation(eval_name, items, plan_path, evidence)
            results.append(result)

            if result.success:
                pct = f"{result.pass_rate * 100:.1f}%" if result.pass_rate is not None else "N/A"
                print(f"  {eval_name} completed — {pct}")
            else:
                print(f"  {eval_name} failed: {result.error}")

        return results

    def _run_single_evaluation(
        self,
        eval_name: str,
        checklist_items: List,
        plan_path: Optional[Path],
        evidence: Optional[ExecutionEvidence] = None,
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
            )

            prompt_path = self.config.output_dir / f"{eval_name}_prompt.txt"
            prompt_path.write_text(prompt, encoding='utf-8')

            output_json_path = self.config.output_dir / f"{eval_name}_evaluation.json"

            stdout = self._invoke_provider(
                prompt=prompt,
                working_dir=self.config.repo_path,
                output_path=output_json_path,
            )

            if stdout is not None and output_json_path.exists():
                with open(output_json_path, encoding='utf-8') as f:
                    data = json.load(f)

                return EvaluationResult(
                    name=eval_name,
                    success=True,
                    items=data.get("items", []),
                    pass_rate=data.get("pass_rate"),
                    output_path=output_json_path,
                )
            elif stdout is None:
                return EvaluationResult(
                    name=eval_name, success=False,
                    error="Provider invocation failed",
                )
            else:
                return EvaluationResult(
                    name=eval_name, success=False,
                    error=f"Output file not produced: {output_json_path.name}",
                )

        except Exception as e:
            return EvaluationResult(name=eval_name, success=False, error=str(e))

    # -- Report ------------------------------------------------------------

    def _report(self, results, evidence=None):
        """Generate the final report."""
        return self.report_generator.generate_from_results(
            results=results,
            config=self.config,
            output_dir=self.config.output_dir,
            generate_pdf=self.config.generate_pdf,
            evidence=evidence,
        )

    # -- Provider Invocation -----------------------------------------------

    def _invoke_provider(
        self, prompt: str, working_dir: Path, output_path: Path,
    ) -> Optional[str]:
        """Invoke the AI provider to run the evaluation."""
        provider = self.config.provider.lower()

        if provider == "claude":
            return self._invoke_claude(prompt, working_dir, output_path)
        elif provider == "codex":
            return self._invoke_codex(prompt, working_dir, output_path)
        elif provider == "gemini":
            return self._invoke_gemini(prompt, working_dir, output_path)
        else:
            raise ValueError(f"Unknown provider: {provider}")

    def _invoke_claude(self, prompt, working_dir, output_path):
        try:
            prompt_file = self.config.output_dir / f"current_prompt_{output_path.stem}.txt"
            prompt_file.write_text(prompt, encoding='utf-8')
            cmd = ["claude", "-p", str(prompt_file), "--output-format", "text", "--dangerously-skip-permissions"]
            result = subprocess.run(
                cmd, cwd=working_dir, timeout=self.config.timeout,
                capture_output=True, encoding='utf-8',
            )
            return result.stdout if result.returncode == 0 else None
        except subprocess.TimeoutExpired:
            print(f"  Timeout after {self.config.timeout}s")
            return None
        except FileNotFoundError:
            print("  Claude CLI not found. Please install claude-code.")
            return None
        except Exception as e:
            print(f"  Error invoking Claude: {e}")
            return None

    def _invoke_codex(self, prompt, working_dir, output_path):
        try:
            cmd = ["codex", "exec", "--full-auto", "-"]
            result = subprocess.run(
                cmd, cwd=working_dir, input=prompt, timeout=self.config.timeout,
                capture_output=True, encoding='utf-8',
            )
            return result.stdout if result.returncode == 0 else None
        except Exception as e:
            print(f"  Error invoking Codex: {e}")
            return None

    def _invoke_gemini(self, prompt, working_dir, output_path):
        try:
            prompt_file = self.config.output_dir / f"current_prompt_{output_path.stem}.txt"
            prompt_file.write_text(prompt, encoding='utf-8')
            cmd = ["gemini", "-p", str(prompt_file)]
            result = subprocess.run(
                cmd, cwd=working_dir, timeout=self.config.timeout,
                capture_output=True, encoding='utf-8',
            )
            return result.stdout if result.returncode == 0 else None
        except Exception as e:
            print(f"  Error invoking Gemini: {e}")
            return None
