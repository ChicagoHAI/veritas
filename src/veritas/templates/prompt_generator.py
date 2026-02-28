"""Generate prompts for evaluation agents."""

from pathlib import Path
from typing import Optional, List
from jinja2 import Environment, FileSystemLoader, select_autoescape

from veritas.core.checklist import ChecklistItem


CATEGORY_DISPLAY_NAMES = {
    "code": "Code Quality",
    "consistency": "Consistency",
    "generalization": "Generalization",
    "replication": "Replicability",
    "instruction": "Instruction Following",
}


class PromptGenerator:
    """Generates prompts for checklist generation and scoring."""

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
        paper_text: Optional[str] = None,
    ) -> str:
        """Generate prompt for personalized checklist generation.

        Args:
            repo_path: Path to the repository being evaluated
            output_dir: Directory for output files
            paper_text: Extracted text from the paper PDF (optional)

        Returns:
            Complete prompt string for checklist generation
        """
        template = self.env.get_template("checklist_generation.md")

        context = {
            "repo_path": str(repo_path.absolute()),
            "output_dir": str(output_dir.absolute()),
            "has_paper": paper_text is not None,
            "paper_text": paper_text or "",
        }
        return template.render(**context)

    def generate_scoring_prompt(
        self,
        category_name: str,
        checklist_items: List[ChecklistItem],
        repo_path: Path,
        plan_path: Optional[Path],
        output_dir: Path,
    ) -> str:
        """Generate prompt for scoring a category's checklist items.

        Args:
            category_name: Category key (code, consistency, etc.)
            checklist_items: List of ChecklistItem for this category
            repo_path: Path to the repository
            plan_path: Path to plan file (if available)
            output_dir: Directory for output files

        Returns:
            Complete prompt string for scoring
        """
        template = self.env.get_template("evaluation/scoring.txt")

        context = {
            "category_name": category_name,
            "category_display_name": CATEGORY_DISPLAY_NAMES.get(category_name, category_name),
            "checklist_items": checklist_items,
            "repo_path": str(repo_path.absolute()),
            "plan_path": str(plan_path.absolute()) if plan_path else None,
            "output_dir": str(output_dir.absolute()),
            "has_plan": plan_path is not None and plan_path.exists(),
        }
        return template.render(**context)
