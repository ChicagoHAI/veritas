"""Generate prompts for the claim-verification pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, List, TYPE_CHECKING
from jinja2 import Environment, FileSystemLoader, select_autoescape

if TYPE_CHECKING:
    from veritas.core.models.replication import ReplicationPlan
    from veritas.core.models.paper_claims import PaperClaims, PaperClaim


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
            "repo_path": str(Path(repo_path).absolute()) if repo_path else "",
            "output_dir": str(output_dir.absolute()),
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
            "claim": claim,
            "codebase_dir": str(codebase_dir.absolute()),
            "codebase_diff_path": str(codebase_diff_path.absolute()),
            "replication_log_path": str(replication_log_path.absolute()),
            "fix_severity_path": str(fix_severity_path.absolute()),
            "plan_step_ids": plan_step_ids,
            "output_dir": str(output_dir.absolute()),
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
            "repo_path": str(Path(repo_path).absolute()) if repo_path else "",
            "output_dir": str(output_dir.absolute()),
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
        paper_path: Optional[Path] = None,
        repo_path: Optional[Path] = None,
        mode: str = "full",
        data_path: Optional[Path] = None,
    ) -> str:
        """Generate session instructions for the replication agent."""
        template = self.env.get_template("replication/session_instructions.md")
        context = {
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
            "paper_path": str(paper_path),
            "output_dir": str(output_dir),
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
            "fixes": fixes,
            "output_dir": str(output_dir.absolute()),
        }
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
