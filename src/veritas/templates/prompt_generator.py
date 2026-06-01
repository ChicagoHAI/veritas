"""Generate prompts for the claim-verification pipeline."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, List, TYPE_CHECKING
from jinja2 import Environment, FileSystemLoader, select_autoescape

if TYPE_CHECKING:
    from veritas.core.models.replication import ReplicationPlan
    from veritas.core.models.paper_claims import PaperClaims, PaperClaim


# Docker-mode defaults for paths that vary between docker and host runs.
# In docker, these reflect the layout the image bakes in / the wrapper
# bind-mounts. In host mode, the `veritas-host` wrapper exports the
# corresponding env vars so they resolve to host-side paths instead.
_DEFAULT_SKILLS_DIR = "/workspace/veritas-skills"
_DEFAULT_VENV_DIR = "/workspace/.venv"


class PromptGenerator:
    """Generates prompts for claim extraction, replication, and verification."""

    def __init__(self, templates_dir: Optional[Path] = None):
        if templates_dir is None:
            templates_dir = Path(__file__).parent.parent.parent.parent / "templates"

        self.templates_dir = templates_dir
        self.env = Environment(
            loader=FileSystemLoader(templates_dir),
            autoescape=select_autoescape(['html', 'xml']),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def _runtime_paths_context(
        self,
        output_dir: Optional[Path] = None,
        repo_path: Optional[Path] = None,
        data_path: Optional[Path] = None,
    ) -> dict:
        """Build the runtime-dependent path vars used across templates.

        `skills_dir` and `venv_dir` come from env vars with docker-mode
        defaults; the host wrapper exports them pointing at host paths.
        `codebase_dir` / `replication_dir` are derived from `output_dir`
        and mirror the layout the runner writes to disk.
        """
        ctx = {
            "skills_dir": os.environ.get("VERITAS_SKILLS_DIR", _DEFAULT_SKILLS_DIR),
            "venv_dir": os.environ.get("VERITAS_VENV_DIR", _DEFAULT_VENV_DIR),
        }
        if output_dir is not None:
            output_abs = Path(output_dir).absolute()
            ctx["output_dir"] = str(output_abs)
            ctx["replication_dir"] = str(output_abs / "replication")
            ctx["codebase_dir"] = str(output_abs / "replication" / "codebase")
        if repo_path is not None:
            ctx["repo_path"] = str(Path(repo_path).absolute())
        if data_path is not None:
            ctx["data_path"] = str(Path(data_path).absolute())
        return ctx

    def generate_paper_claims_prompt(
        self,
        repo_path: Optional[Path],
        output_dir: Path,
        paper_path: Optional[Path] = None,
        readme_path: Optional[Path] = None,
        claim_scope: str = "main",
    ) -> str:
        """Generate prompt for paper-claim extraction.

        Sources are mode-dependent: a paper PDF (modes 1 / 2) or a repo
        README (mode 3). At least one of ``paper_path`` or ``readme_path``
        should be supplied; the template branches on ``has_paper``.
        """
        template = self.env.get_template("analyze/paper_claims_extraction.md")
        context = {
            **self._runtime_paths_context(output_dir=output_dir, repo_path=repo_path),
            "paper_path": str(paper_path) if paper_path else "",
            "readme_path": str(readme_path) if readme_path else "",
            "has_paper": paper_path is not None,
            "has_repo": repo_path is not None,
            "claim_scope": claim_scope,
        }
        return template.render(**context)

    def generate_verify_prompt(
        self,
        claim: "PaperClaim",
        codebase_dir: Path,
        codebase_diff_path: Path,
        replication_log_path: Path,
        fix_severity_path: Path,
        plan_step_ids: List[int],
        output_dir: Path,
    ) -> str:
        """Generate per-claim verifier prompt."""
        template = self.env.get_template("verify/single_claim.md")
        context = {
            **self._runtime_paths_context(output_dir=output_dir),
            "claim": claim,
            "codebase_dir": str(codebase_dir.absolute()),  # explicit override (preserves caller's value)
            "codebase_diff_path": str(codebase_diff_path.absolute()),
            "replication_log_path": str(replication_log_path.absolute()),
            "fix_severity_path": str(fix_severity_path.absolute()),
            "plan_step_ids": plan_step_ids,
        }
        return template.render(**context)

    def generate_replication_plan_prompt(
        self,
        repo_path: Optional[Path],
        output_dir: Path,
        claims: "PaperClaims",
        paper_path: Optional[Path] = None,
        mode: str = "full",
        claim_scope: str = "main",
        data_path: Optional[Path] = None,
    ) -> str:
        """Generate prompt for creating a replication plan that targets claim IDs."""
        template = self.env.get_template("replication/plan_generation.md")
        context = {
            **self._runtime_paths_context(
                output_dir=output_dir, repo_path=repo_path, data_path=data_path
            ),
            "has_paper": paper_path is not None,
            "has_repo": repo_path is not None,
            "paper_path": str(paper_path) if paper_path else "",
            "claims": claims,
            "mode": mode,
            "claim_scope": claim_scope,
            "has_data": data_path is not None,
        }
        return template.render(**context)

    def generate_replication_session_prompt(
        self,
        replication_plan: ReplicationPlan,
        output_dir: Path,
        paper_path: Optional[Path] = None,
        repo_path: Optional[Path] = None,
        mode: str = "full",
        data_path: Optional[Path] = None,
    ) -> str:
        """Generate session instructions for the replication agent."""
        template = self.env.get_template("replication/session_instructions.md")
        context = {
            **self._runtime_paths_context(
                output_dir=output_dir, repo_path=repo_path, data_path=data_path
            ),
            "replication_plan": replication_plan,
            "has_paper": paper_path is not None,
            "paper_path": str(paper_path) if paper_path else "",
            "has_repo": repo_path is not None,
            "mode": mode,
            "has_data": data_path is not None,
        }
        return template.render(**context)

    def generate_codegen_prompt(
        self,
        paper_path: Path,
        output_dir: Path,
        data_path: Optional[Path] = None,
    ) -> str:
        """Generate session instructions for the codegen agent (paper-only mode).

        Rendered with only ``paper_path``, ``output_dir``, and the
        presence/absence of ``data_path`` — never with extracted claim
        content — so the codegen agent reads the paper directly but is
        structurally prevented from seeing paper-reported result values
        that would compromise downstream verification.
        """
        template = self.env.get_template("codegen/session_instructions.md")
        context = {
            **self._runtime_paths_context(output_dir=output_dir, data_path=data_path),
            "paper_path": str(paper_path),
            "has_data": data_path is not None,
        }
        return template.render(**context)

    def generate_fix_severity_prompt(
        self,
        fixes: List,
        output_dir: Path,
    ) -> str:
        """Generate prompt for assessing fix severity."""
        template = self.env.get_template("assess/fix_severity.md")
        context = {
            **self._runtime_paths_context(output_dir=output_dir),
            "fixes": fixes,
        }
        return template.render(**context)

    def generate_evaluation_prompt(
        self,
        output_dir: Path,
        mode: str,
        has_paper: bool,
        paper_path: Optional[Path] = None,
    ) -> str:
        """Generate the post-verify contextual-evaluation prompt.

        The external checker reads the replication artifacts, verdicts, and
        (when present) the paper, and produces an advisory cheating-monitor +
        contextual-evaluation JSON that does NOT alter the Replication Score.
        """
        template = self.env.get_template("evaluation/contextual_evaluation.md")
        context = {
            **self._runtime_paths_context(output_dir=output_dir),
            "mode": mode,
            "has_paper": has_paper,
        }
        if paper_path is not None:
            context["paper_path"] = str(Path(paper_path).absolute())
        return template.render(**context)

    def generate_insufficient_spec_report(
        self,
        mode: str,
        source_path: Path,
        has_paper: bool,
    ) -> str:
        """Render the bail report shown when analyze produces zero claims."""
        template = self.env.get_template("report/insufficient_spec.md")
        context = {
            "mode": mode,
            "source_path": str(source_path),
            "has_paper": has_paper,
        }
        return template.render(**context)
