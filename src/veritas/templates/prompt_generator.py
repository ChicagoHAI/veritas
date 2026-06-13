"""Generate prompts for the claim-verification pipeline."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, List, TYPE_CHECKING
from jinja2 import Environment, FileSystemLoader, select_autoescape
from veritas.core.config import (
    CITATION_CHECK_FILE,
    CITATION_REFERENCES_FILE,
    CITATION_RESOLVER_VERDICTS_FILE,
    EVALUATION_SUBDIR,
)

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
        manager_guidance: Optional[object] = None,
    ) -> str:
        """Generate prompt for creating a replication plan that targets claim IDs.

        ``manager_guidance`` (a ``ManagerGuidance``) is set only on a
        manager-directed re-run that targets the plan phase; the template's
        ``{% if manager_guidance %}`` block then states the deficiency and the
        specific new instructions so the regenerated plan is genuinely different.
        """
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
            "manager_guidance": manager_guidance,
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
        manager_guidance: Optional[object] = None,
    ) -> str:
        """Generate session instructions for the replication agent.

        ``manager_guidance`` (a ``ManagerGuidance``) is set only on a
        manager-directed re-run; the template's ``{% if manager_guidance %}``
        block then prepends the deficiency + specific new instructions + what was
        already tried, so the re-run is genuinely different (never a blank repeat).
        """
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
            "manager_guidance": manager_guidance,
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

    def generate_citation_check_prompt(
        self,
        output_dir: Path,
        paper_path: Path,
        resolver_script_path: Path,
        faithfulness_scope: str = "main",
    ) -> str:
        """Render the citation-check subagent prompt.

        A single web-enabled provider invocation: extract the paper's reference
        list, run the deterministic resolver script (authoritative for
        existence/metadata), then web-search-escalate only the unresolved
        references. Does not alter the Replication Score.

        ``faithfulness_scope`` controls how many claim-bearing citations are
        checked for faithfulness: ``"main"`` checks only the citations central
        to the paper's core argument; ``"all"`` checks every claim-bearing
        citation.
        """
        eval_dir = Path(output_dir).absolute() / EVALUATION_SUBDIR
        template = self.env.get_template("evaluation/citation_check.md")
        context = {
            **self._runtime_paths_context(output_dir=output_dir),
            "paper_path": str(Path(paper_path).absolute()),
            "resolver_script_path": str(Path(resolver_script_path).absolute()),
            "references_path": str(eval_dir / CITATION_REFERENCES_FILE),
            "resolver_verdicts_path": str(eval_dir / CITATION_RESOLVER_VERDICTS_FILE),
            "citation_check_path": str(eval_dir / CITATION_CHECK_FILE),
            "faithfulness_scope": faithfulness_scope,
        }
        return template.render(**context)

    def generate_citation_audit_prompt(
        self,
        output_dir: Path,
        paper_path: Path,
    ) -> str:
        """Render the citation-audit prompt (independent re-check of flagged verdicts)."""
        from veritas.core.config import (
            CITATION_CHECK_FILE,
            CITATION_AUDIT_FILE,
            EVALUATION_SUBDIR,
        )
        eval_dir = Path(output_dir).absolute() / EVALUATION_SUBDIR
        template = self.env.get_template("evaluation/citation_audit.md")
        context = {
            **self._runtime_paths_context(output_dir=output_dir),
            "paper_path": str(Path(paper_path).absolute()),
            "citation_check_path": str(eval_dir / CITATION_CHECK_FILE),
            "citation_audit_path": str(eval_dir / CITATION_AUDIT_FILE),
        }
        return template.render(**context)

    def generate_manager_review_prompt(
        self,
        output_dir: Path,
        retries_remaining: int,
        iteration: int,
        manager_guidance: Optional[object] = None,
    ) -> str:
        """Generate the post-replicate manager-review (control-gate) prompt.

        Independent critic pass (fresh context, API keys stripped — it must not
        run paper code): reads the trajectory + evidence + diligence signals and
        emits a structured accept/revise verdict. Distinct from the post-verify
        contextual-evaluation report author. ``manager_guidance`` is the prior
        iteration's directive (so the manager can check whether it was followed);
        ``retries_remaining`` is the soft budget signal (hard cap is in code).
        """
        template = self.env.get_template("manager/replication_review.md")
        context = {
            **self._runtime_paths_context(output_dir=output_dir),
            "retries_remaining": retries_remaining,
            "iteration": iteration,
            "manager_guidance": manager_guidance,
        }
        return template.render(**context)

    def generate_research_prompt(
        self,
        template_name: str,
        output_dir: Path,
        need: str,
        rationale: str = "",
    ) -> str:
        """Render a research sub-agent prompt (resource- or literature-finder).

        ``template_name`` is one of ``research.KIND_TEMPLATES`` values
        (``research/resource_finder.md`` / ``research/literature_finder.md``).
        These are separate provider invocations with web-search/fetch access;
        they return findings + provenance, never the paper's reported values.
        """
        template = self.env.get_template(template_name)
        context = {
            **self._runtime_paths_context(output_dir=output_dir),
            "need": need,
            "rationale": rationale,
        }
        return template.render(**context)

    def generate_research_redactor_prompt(
        self,
        output_dir: Path,
        out_path: Path,
        kind: str,
        need: str,
        finding: str,
        sources: List[str],
    ) -> str:
        """Render the LLM redactor prompt (anti-leakage barrier b, primary layer).

        The redactor reads a research finding and removes the paper's reported
        result values by LLM judgment (no keyword matching), preserving
        methodology/resources and provenance. ``out_path`` is where the redactor
        must write its single JSON object.
        """
        template = self.env.get_template("research/redactor.md")
        context = {
            **self._runtime_paths_context(output_dir=output_dir),
            "out_path": str(out_path),
            "kind": kind,
            "need": need,
            "finding": finding,
            "sources": sources or [],
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
