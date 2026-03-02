"""Main runner for replication evaluation."""

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any

from veritas.core.config import Config
from veritas.core.checklist import Checklist, parse_checklist_response
from veritas.core.plan_extractor import PlanExtractor
from veritas.core.report_generator import ReportGenerator
from veritas.templates.prompt_generator import PromptGenerator
from veritas.utils.pdf import read_pdf


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
    """Orchestrates the replication evaluation process."""

    def __init__(self, config: Config):
        self.config = config
        self.prompt_generator = PromptGenerator()
        self.plan_extractor = PlanExtractor()
        self.report_generator = ReportGenerator()

    def run(self) -> RunResult:
        """Run the full replication evaluation."""
        try:
            self._setup_output_dir()
            plan_path = self._get_or_extract_plan()
            checklist = self._generate_checklist()
            results = self._run_evaluations(plan_path, checklist)
            report_path, pdf_path = self._generate_report(results)

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
        (self.config.output_dir / "replications").mkdir(exist_ok=True)

    def _get_or_extract_plan(self) -> Optional[Path]:
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

    def _generate_checklist(self) -> Checklist:
        """Generate a personalized checklist.

        Uses paper text if available, otherwise generates from repo alone.
        Raises RuntimeError if generation fails.
        """
        print("Generating personalized checklist...")

        # Read paper if available
        paper_text = None
        if self.config.has_paper:
            paper_text = read_pdf(self.config.paper_path)

        # Generate prompt
        prompt = self.prompt_generator.generate_checklist_prompt(
            repo_path=self.config.repo_path,
            output_dir=self.config.output_dir,
            paper_text=paper_text,
        )

        # Save prompt for reference
        prompt_path = self.config.output_dir / "checklist_generation_prompt.txt"
        prompt_path.write_text(prompt, encoding='utf-8')

        # Invoke provider
        output_json_path = self.config.output_dir / "checklist.json"
        stdout = self._invoke_provider(
            prompt=prompt,
            working_dir=self.config.repo_path,
            output_path=output_json_path,
        )

        if stdout is None:
            raise RuntimeError("Checklist generation failed: provider returned no output")

        # Parse response — try file first, then stdout
        response_text = None
        if output_json_path.exists():
            response_text = output_json_path.read_text(encoding='utf-8')
        elif stdout:
            response_text = stdout

        if not response_text:
            raise RuntimeError("Checklist generation failed: no output to parse")

        checklist = parse_checklist_response(response_text)

        # Save parsed checklist
        output_json_path.write_text(
            json.dumps(checklist.to_dict(), indent=2), encoding='utf-8'
        )

        print(f"  Generated {len(checklist.items)} checklist items across {len(checklist.categories)} categories")
        return checklist

    def _run_evaluations(self, plan_path: Optional[Path], checklist: Checklist) -> List[EvaluationResult]:
        """Run scoring evaluations for all configured categories."""
        results = []

        for eval_name in self.config.evaluations:
            print(f"Running {eval_name} evaluation...")

            items = checklist.get_items_by_category(eval_name)
            if not items:
                print(f"  Skipping {eval_name} — no checklist items generated for this category")
                results.append(EvaluationResult(
                    name=eval_name,
                    success=True,
                    items=[],
                    pass_rate=None,
                ))
                continue

            result = self._run_single_evaluation(eval_name, items, plan_path)
            results.append(result)

            if result.success:
                pct = f"{result.pass_rate * 100:.1f}%" if result.pass_rate is not None else "N/A"
                print(f"  ✓ {eval_name} completed — {pct}")
            else:
                print(f"  ✗ {eval_name} failed: {result.error}")

        return results

    def _run_single_evaluation(
        self,
        eval_name: str,
        checklist_items: List,
        plan_path: Optional[Path],
    ) -> EvaluationResult:
        """Run scoring for one category's checklist items."""
        try:
            prompt = self.prompt_generator.generate_scoring_prompt(
                category_name=eval_name,
                checklist_items=checklist_items,
                repo_path=self.config.repo_path,
                plan_path=plan_path,
                output_dir=self.config.output_dir,
            )

            prompt_path = self.config.output_dir / f"{eval_name}_prompt.txt"
            prompt_path.write_text(prompt, encoding='utf-8')

            output_json_path = self.config.output_dir / f"{eval_name}_evaluation.json"

            stdout = self._invoke_provider(
                prompt=prompt,
                working_dir=self.config.repo_path,
                output_path=output_json_path,
            )

            if stdout and output_json_path.exists():
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
                capture_output=True, encoding='utf-8', shell=True,
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
                capture_output=True, encoding='utf-8', shell=True,
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
                capture_output=True, encoding='utf-8', shell=True,
            )
            return result.stdout if result.returncode == 0 else None
        except Exception as e:
            print(f"  Error invoking Gemini: {e}")
            return None

    def _generate_report(self, results):
        return self.report_generator.generate_from_results(
            results=results,
            config=self.config,
            output_dir=self.config.output_dir,
            generate_pdf=self.config.generate_pdf,
        )
