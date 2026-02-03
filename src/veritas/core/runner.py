"""Main runner for replication evaluation."""

import json
import subprocess
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from veritas.core.config import Config
from veritas.core.plan_extractor import PlanExtractor
from veritas.core.report_generator import ReportGenerator
from veritas.templates.prompt_generator import PromptGenerator


@dataclass
class EvaluationResult:
    """Result of a single evaluation."""
    name: str
    success: bool
    checklist: Dict[str, str] = None
    rationale: Dict[str, str] = None
    metrics: Dict[str, Any] = None
    error: Optional[str] = None
    output_path: Optional[Path] = None


@dataclass
class RunResult:
    """Result of the full replication run."""
    success: bool
    evaluations: List[EvaluationResult] = None
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
            # Setup output directory
            self._setup_output_dir()

            # Extract or load plan
            plan_path = self._get_or_extract_plan()

            # Run evaluations
            results = self._run_evaluations(plan_path)

            # Generate report
            report_path, pdf_path = self._generate_report(results)

            return RunResult(
                success=True,
                evaluations=results,
                report_path=report_path,
                pdf_path=pdf_path,
            )

        except Exception as e:
            return RunResult(
                success=False,
                error=str(e),
            )

    def _setup_output_dir(self):
        """Create the output directory structure."""
        self.config.output_dir.mkdir(parents=True, exist_ok=True)

        # Create subdirectories
        (self.config.output_dir / "replications").mkdir(exist_ok=True)

    def _get_or_extract_plan(self) -> Optional[Path]:
        """Get existing plan or extract from paper."""
        # Check for existing plan in repo
        repo_plan = self.config.repo_path / "plan.md"
        if repo_plan.exists():
            return repo_plan

        # Check for provided plan
        if self.config.has_plan:
            return self.config.plan_path

        # Extract from paper if available
        if self.config.has_paper:
            plan_content = self.plan_extractor.extract(
                self.config.paper_path,
                with_evidence=True
            )
            plan_path = self.config.output_dir / "extracted_plan.md"
            plan_path.write_text(plan_content)
            return plan_path

        return None

    def _run_evaluations(self, plan_path: Optional[Path]) -> List[EvaluationResult]:
        """Run all configured evaluations."""
        results = []

        for eval_name in self.config.evaluations:
            print(f"Running {eval_name} evaluation...")

            result = self._run_single_evaluation(eval_name, plan_path)
            results.append(result)

            if result.success:
                print(f"  ✓ {eval_name} completed")
            else:
                print(f"  ✗ {eval_name} failed: {result.error}")

        return results

    def _run_single_evaluation(
        self,
        eval_name: str,
        plan_path: Optional[Path]
    ) -> EvaluationResult:
        """Run a single evaluation using the AI provider."""
        try:
            # Generate the prompt for this evaluation
            prompt = self.prompt_generator.generate_evaluation_prompt(
                eval_type=eval_name,
                repo_path=self.config.repo_path,
                plan_path=plan_path,
                output_dir=self.config.output_dir,
            )

            # Save the prompt for reference
            prompt_path = self.config.output_dir / f"{eval_name}_prompt.txt"
            prompt_path.write_text(prompt)

            # Run the evaluation via AI provider
            output_json_path = self.config.output_dir / f"{eval_name}_evaluation.json"

            success = self._invoke_provider(
                prompt=prompt,
                working_dir=self.config.repo_path,
                output_path=output_json_path,
            )

            if success and output_json_path.exists():
                # Parse the results
                with open(output_json_path) as f:
                    data = json.load(f)

                return EvaluationResult(
                    name=eval_name,
                    success=True,
                    checklist=data.get("Checklist", {}),
                    rationale=data.get("Rationale", {}),
                    metrics=data.get("Metrics", {}),
                    output_path=output_json_path,
                )
            else:
                return EvaluationResult(
                    name=eval_name,
                    success=False,
                    error="Evaluation did not produce expected output",
                )

        except Exception as e:
            return EvaluationResult(
                name=eval_name,
                success=False,
                error=str(e),
            )

    def _invoke_provider(
        self,
        prompt: str,
        working_dir: Path,
        output_path: Path,
    ) -> bool:
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

    def _invoke_claude(
        self,
        prompt: str,
        working_dir: Path,
        output_path: Path,
    ) -> bool:
        """Invoke Claude CLI to run evaluation."""
        try:
            # Write prompt to temp file
            prompt_file = self.config.output_dir / "current_prompt.txt"
            prompt_file.write_text(prompt)

            # Build command
            cmd = [
                "claude",
                "-p", str(prompt_file),
                "--output-format", "text",
            ]

            # Run with timeout
            result = subprocess.run(
                cmd,
                cwd=working_dir,
                timeout=self.config.timeout,
                capture_output=True,
                text=True,
            )

            # Check for JSON output in response
            if result.returncode == 0:
                # Try to extract JSON from output
                output = result.stdout
                return self._extract_json_result(output, output_path)

            return False

        except subprocess.TimeoutExpired:
            print(f"  Timeout after {self.config.timeout}s")
            return False
        except FileNotFoundError:
            print("  Claude CLI not found. Please install claude-code.")
            return False
        except Exception as e:
            print(f"  Error invoking Claude: {e}")
            return False

    def _invoke_codex(
        self,
        prompt: str,
        working_dir: Path,
        output_path: Path,
    ) -> bool:
        """Invoke Codex CLI to run evaluation."""
        try:
            cmd = ["codex", "exec", "--stdin"]

            result = subprocess.run(
                cmd,
                cwd=working_dir,
                input=prompt,
                timeout=self.config.timeout,
                capture_output=True,
                text=True,
            )

            if result.returncode == 0:
                return self._extract_json_result(result.stdout, output_path)

            return False

        except Exception as e:
            print(f"  Error invoking Codex: {e}")
            return False

    def _invoke_gemini(
        self,
        prompt: str,
        working_dir: Path,
        output_path: Path,
    ) -> bool:
        """Invoke Gemini CLI to run evaluation."""
        try:
            prompt_file = self.config.output_dir / "current_prompt.txt"
            prompt_file.write_text(prompt)

            cmd = ["gemini", "-p", str(prompt_file)]

            result = subprocess.run(
                cmd,
                cwd=working_dir,
                timeout=self.config.timeout,
                capture_output=True,
                text=True,
            )

            if result.returncode == 0:
                return self._extract_json_result(result.stdout, output_path)

            return False

        except Exception as e:
            print(f"  Error invoking Gemini: {e}")
            return False

    def _extract_json_result(self, output: str, output_path: Path) -> bool:
        """Extract JSON result from provider output."""
        try:
            # Try to find JSON in the output
            import re

            # Look for JSON block
            json_match = re.search(r'\{[\s\S]*"Checklist"[\s\S]*\}', output)
            if json_match:
                json_str = json_match.group()
                # Validate it's proper JSON
                data = json.loads(json_str)

                # Save to output path
                with open(output_path, 'w') as f:
                    json.dump(data, f, indent=2)

                return True

            # If no JSON found, check if output file was created directly
            if output_path.exists():
                return True

            return False

        except json.JSONDecodeError:
            return False

    def _generate_report(
        self,
        results: List[EvaluationResult]
    ) -> tuple[Optional[Path], Optional[Path]]:
        """Generate the final replication report."""
        return self.report_generator.generate_from_results(
            results=results,
            config=self.config,
            output_dir=self.config.output_dir,
            generate_pdf=self.config.generate_pdf,
        )
