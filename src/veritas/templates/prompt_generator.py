"""Generate prompts for evaluation agents."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, List, TYPE_CHECKING
from jinja2 import Environment, FileSystemLoader, select_autoescape

from veritas.core.models.checklist import ChecklistItem

if TYPE_CHECKING:
    from veritas.core.models.replication import ExecutionEvidence, ReplicationPlan


CATEGORY_DISPLAY_NAMES = {
    "code": "Code Quality",
    "consistency": "Consistency",
    "generalization": "Generalization",
    "replication": "Replicability",
    "instruction_following": "Instruction Following",
}


class PromptGenerator:
    """Generates prompts for checklist generation, replication, and scoring."""

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

    def generate_checklist_prompt(
        self,
        repo_path: Path,
        output_dir: Path,
        paper_path: Optional[Path] = None,
    ) -> str:
        """Generate prompt for personalized checklist generation."""
        template = self.env.get_template("checklist_generation.md")

        context = {
            "repo_path": str(repo_path.absolute()),
            "output_dir": str(output_dir.absolute()),
            "has_paper": paper_path is not None,
            "paper_path": str(paper_path) if paper_path else "",
        }
        return template.render(**context)

    def generate_scoring_prompt(
        self,
        category_name: str,
        checklist_items: List[ChecklistItem],
        repo_path: Path,
        plan_path: Optional[Path],
        output_dir: Path,
        evidence: Optional[ExecutionEvidence] = None,
        fix_assessment: Optional[Any] = None,
    ) -> str:
        """Generate prompt for scoring a category's checklist items."""
        template = self.env.get_template("evaluation/scoring.txt")

        context = {
            "category_name": category_name,
            "category_display_name": CATEGORY_DISPLAY_NAMES.get(category_name, category_name),
            "checklist_items": checklist_items,
            "repo_path": str(repo_path.absolute()),
            "plan_path": str(plan_path.absolute()) if plan_path else None,
            "output_dir": str(output_dir.absolute()),
            "has_plan": plan_path is not None and plan_path.exists(),
            "has_evidence": evidence is not None,
            "evidence": evidence,
            "has_fix_assessment": fix_assessment is not None,
            "fix_assessment": fix_assessment,
        }
        return template.render(**context)

    def generate_replication_plan_prompt(
        self,
        repo_path: Path,
        output_dir: Path,
        checklist_items: List[ChecklistItem],
        paper_path: Optional[Path] = None,
        mode: str = "main",
    ) -> str:
        """Generate prompt for creating a replication plan."""
        template = self.env.get_template("replication/plan_generation.md")
        context = {
            "repo_path": str(repo_path.absolute()),
            "output_dir": str(output_dir.absolute()),
            "has_paper": paper_path is not None,
            "paper_path": str(paper_path) if paper_path else "",
            "checklist_items": checklist_items,
            "mode": mode,
        }
        return template.render(**context)

    def generate_replication_session_prompt(
        self,
        replication_plan: ReplicationPlan,
        paper_path: Optional[Path] = None,
    ) -> str:
        """Generate session instructions for the replication agent."""
        template = self.env.get_template("replication/session_instructions.md")
        context = {
            "replication_plan": replication_plan,
            "has_paper": paper_path is not None,
            "paper_path": str(paper_path) if paper_path else "",
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
