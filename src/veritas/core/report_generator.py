"""Generate replication reports from per-claim verdicts and Replication Score."""

import json
import math
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import jinja2

from veritas.core.config import (
    Config,
    REPORT_SUBDIR,
    REPORT_MD_FILE,
    REPORT_PDF_FILE,
    REPORT_HTML_FILE,
    VERIFY_SUBDIR,
    VERDICTS_FILE,
    REPLICATION_SCORE_FILE,
    PAPER_CLAIMS_FILE,
    ANALYZE_SUBDIR,
    EVALUATION_SUBDIR,
    EVALUATION_FILE,
    CITATION_CHECK_FILE,
    WORKFLOW_LOG_FILE,
)
from veritas.core.models.paper_claims import (
    ClaimVerdict,
    PaperClaims,
    ReplicationScore,
)


# Header label for each tier in the report.
TIER_DISPLAY = {
    "headline": "Headline",
    "supporting": "Supporting",
    "setup": "Setup",
}

# Display label for each verdict status.
STATUS_DISPLAY = {
    "match": "match",
    "partial": "partial",
    "no_match": "no match",
    "not_attempted": "not attempted",
    "not_applicable": "n/a",
}

# Color per verdict status, for the HTML report.
STATUS_COLOR = {
    "match": "#1a7f37",
    "partial": "#9a6700",
    "no_match": "#cf222e",
    "not_attempted": "#57606a",
    "not_applicable": "#57606a",
    "missing": "#57606a",
}
SEVERITY_COLOR = {"minor": "#1a7f37", "major": "#9a6700", "critical": "#cf222e"}

# The em dash is the most common "this was written by an AI" tell, and the
# evaluation prompt forbids it. This is the deterministic guarantee: a stray em
# dash from the manager never reaches the rendered report. Only the em dash (—)
# is scrubbed, not the en dash (–), to avoid mangling numeric ranges.
_EMDASH_RE = re.compile(r"\s*—\s*")


def _scrub_prose(obj):
    """Recursively replace em dashes with a comma in every string in a parsed
    JSON structure (the manager's evaluation output). Leaves non-strings as-is."""
    if isinstance(obj, str):
        return re.sub(r",\s*,", ",", _EMDASH_RE.sub(", ", obj))
    if isinstance(obj, dict):
        return {k: _scrub_prose(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub_prose(v) for v in obj]
    return obj


class ReportGenerator:
    """Generate the Replication Report."""

    def generate(
        self,
        replicate_dir: Path,
        output_path: Optional[Path] = None,
        generate_pdf: bool = True,
        generate_md: bool = True,
    ) -> Tuple[Optional[Path], Optional[Path]]:
        """Re-render the report from on-disk artifacts.

        Reads paper_claims.json, verdicts.json, replication_score.json from
        the output directory and regenerates the markdown + PDF.
        """
        claims = self._load_claims(replicate_dir)
        verdicts = self._load_verdicts(replicate_dir)
        score = self._load_score(replicate_dir)
        mode = self._load_mode(replicate_dir)
        evaluation = self._load_evaluation(replicate_dir)

        md_content = self._render(
            claims=claims, verdicts=verdicts, score=score,
            evidence=None, fix_assessment=None,
            mode=mode,
            output_dir=replicate_dir,
        )
        html_content = self._render_html(self._build_html_context(
            claims, verdicts, score, None, None, evaluation, mode,
        ))

        if output_path is None:
            output_path = replicate_dir / REPORT_SUBDIR / REPORT_MD_FILE
        else:
            output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        md_path: Optional[Path] = None
        pdf_path: Optional[Path] = None

        if generate_md:
            md_path = output_path.with_suffix(".md")
            md_path.write_text(md_content, encoding='utf-8')

        output_path.with_suffix(".html").write_text(html_content, encoding='utf-8')

        if generate_pdf:
            pdf_path = output_path.with_suffix(".pdf")
            if not self._generate_pdf_from_html(html_content, pdf_path):
                self._generate_pdf(md_content, pdf_path, pdf_path.parent)

        return md_path, pdf_path

    def generate_from_results(
        self,
        claims: PaperClaims,
        verdicts: List[ClaimVerdict],
        score: ReplicationScore,
        config: Config,
        output_dir: Path,
        generate_pdf: bool = True,
        evidence=None,
        fix_assessment=None,
    ) -> Tuple[Optional[Path], Optional[Path]]:
        """Render the report from live in-memory data."""
        evaluation = self._load_evaluation(output_dir)
        md_content = self._render(
            claims=claims, verdicts=verdicts, score=score,
            evidence=evidence, fix_assessment=fix_assessment,
            mode=config.mode,
            output_dir=output_dir,
        )
        html_content = self._render_html(self._build_html_context(
            claims, verdicts, score, evidence, fix_assessment, evaluation, config.mode,
        ))

        report_dir = config.report_dir
        report_dir.mkdir(parents=True, exist_ok=True)
        md_path = report_dir / REPORT_MD_FILE
        md_path.write_text(md_content, encoding='utf-8')
        (report_dir / REPORT_HTML_FILE).write_text(html_content, encoding='utf-8')

        pdf_path: Optional[Path] = None
        if generate_pdf:
            pdf_path = report_dir / REPORT_PDF_FILE
            if not self._generate_pdf_from_html(html_content, pdf_path):
                self._generate_pdf(md_content, pdf_path, report_dir)

        return md_path, pdf_path

    # -- Loading helpers (used by the standalone ``generate``) --------------

    def _load_claims(self, replicate_dir: Path) -> Optional[PaperClaims]:
        path = replicate_dir / ANALYZE_SUBDIR / PAPER_CLAIMS_FILE
        if not path.exists():
            return None
        with open(path, encoding='utf-8') as f:
            return PaperClaims.from_dict(json.load(f))

    def _load_verdicts(self, replicate_dir: Path) -> List[ClaimVerdict]:
        path = replicate_dir / VERIFY_SUBDIR / VERDICTS_FILE
        if not path.exists():
            return []
        with open(path, encoding='utf-8') as f:
            return [ClaimVerdict.from_dict(d) for d in json.load(f)]

    def _load_score(self, replicate_dir: Path) -> Optional[ReplicationScore]:
        path = replicate_dir / VERIFY_SUBDIR / REPLICATION_SCORE_FILE
        if not path.exists():
            return None
        with open(path, encoding='utf-8') as f:
            return ReplicationScore.from_dict(json.load(f))

    def _load_evaluation(self, replicate_dir: Optional[Path]) -> Optional[dict]:
        """Load the manager/external-checker output, if the evaluation phase ran.

        Returns the parsed dict, or None when the phase was not run (opt-in via
        ``--evaluate``) or its output is absent/malformed. The report degrades
        gracefully to deterministic-only in that case — the score and tables do
        not depend on this.
        """
        if replicate_dir is None:
            return None
        path = replicate_dir / EVALUATION_SUBDIR / EVALUATION_FILE
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            return _scrub_prose(data) if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def _load_citation_check(self, replicate_dir: Optional[Path]) -> Optional[dict]:
        """Load the citation-check output, if the submodule ran.

        Returns the parsed dict, or None when the submodule was not run (opt-in
        via ``--check-citations``) or its output is absent/malformed. The report
        degrades gracefully — the score and tables never depend on this.
        """
        if replicate_dir is None:
            return None
        path = Path(replicate_dir) / EVALUATION_SUBDIR / CITATION_CHECK_FILE
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def _load_mode(self, replicate_dir: Path) -> Optional[str]:
        """Recover the input mode from pipeline_state.json, if available.

        Reads from ``state['config']`` — the canonical location, since ``mode``
        is a runtime configuration knob, not an input artifact.
        """
        state_path = replicate_dir / ".veritas" / "pipeline_state.json"
        if not state_path.exists():
            return None
        try:
            with open(state_path, encoding='utf-8') as f:
                data = json.load(f)
            config = data.get("config") or {}
            return config.get("mode")
        except (OSError, json.JSONDecodeError):
            return None

    # -- Rendering ----------------------------------------------------------

    def _render(
        self,
        claims: Optional[PaperClaims],
        verdicts: List[ClaimVerdict],
        score: Optional[ReplicationScore],
        evidence=None,
        fix_assessment=None,
        mode: Optional[str] = None,
        output_dir: Optional[Path] = None,
    ) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        report = f"# Replication Report\n\n**Generated:** {now}\n\n---\n\n"

        # Manager / external-checker narrative (advisory; None when --evaluate
        # was not run or the output is malformed — the report stays complete).
        evaluation = self._load_evaluation(output_dir)

        report += self._render_executive_summary(score, mode=mode)

        # Narrative synthesis leads, when available; the deterministic tables
        # below remain the authoritative, auditable record.
        if evaluation is not None:
            report += self._render_synthesis(evaluation)

        if mode == "paper-only" and output_dir is not None:
            report += self._render_code_generation_section(output_dir)

        if claims is not None and verdicts:
            report += self._render_tier_breakdown(claims, verdicts, score)
            report += self._render_per_claim_table(claims, verdicts)

        if score is not None and score.flags:
            report += self._render_flags(score.flags)

        # Advisory citation check (opt-in via --check-citations). Independent of
        # the score; rendered when present.
        report += self._render_citation_check(self._load_citation_check(output_dir))

        # Manager retry-loop trajectory (only when the loop ran, i.e. >1
        # iteration or a hand-off). The Replication Score above is unaffected by
        # the iteration count; this section records what each re-run changed.
        if output_dir is not None:
            report += self._render_iterations(output_dir)

        if evidence is not None:
            report += self._render_replication_section(evidence)

        # Limitations: deterministic fix-severity table + the manager's
        # code-quality narrative (either may be present independently).
        report += self._render_limitations(
            fix_assessment,
            (evaluation or {}).get("report", {}) if evaluation else {},
        )

        report += "\n---\n\n*Report generated by Veritas Replication Agent*\n"
        return report

    def _render_synthesis(self, evaluation: dict) -> str:
        """Render the manager's narrative sections from the evaluation output.

        Reliability: every field is optional. Empty/missing fields are omitted
        rather than printed as filler. The cheating-monitor is surfaced only
        when risk is not low, so a clean run stays uncluttered.
        """
        report_block = evaluation.get("report") or {}
        # Ordered (heading, key) pairs; only non-empty strings are rendered.
        sections = [
            ("Important Claims", "important_claims"),
            ("Replication Summary", "replication_summary"),
            ("What Did Not Replicate", "did_not_replicate"),
            ("Whole-Paper Consistency", "whole_paper_consistency"),
            ("Methodology Correspondence", "methodology_correspondence"),
            ("Repository Divergence", "repo_divergence"),
        ]
        body = ""
        for heading, key in sections:
            val = report_block.get(key)
            if isinstance(val, str) and val.strip():
                body += f"### {heading}\n\n{val.strip()}\n\n"

        cm = evaluation.get("cheating_monitor") or {}
        risk = cm.get("risk")
        if isinstance(risk, str) and risk.lower() in ("medium", "high"):
            body += f"### Integrity Flag — cheating risk: {risk}\n\n"
            if cm.get("rationale"):
                body += f"{str(cm['rationale']).strip()}\n\n"

        if not body:
            return ""
        intro = (
            "## Evaluation & Synthesis\n\n"
            "*Authored by the post-replication evaluator; advisory narrative, "
            "not part of the Replication Score.*\n\n"
        )
        return intro + body

    def _render_citation_check(self, citation: Optional[dict]) -> str:
        """Render the advisory citation-check section (existence/metadata only).

        Headline counts plus a per-reference breakdown for every flagged item.
        Advisory: stated to NOT affect the Replication Score. Honest that
        citation support (faithfulness) was not checked.
        """
        if not citation:
            return ""
        s = citation.get("summary") or {}
        total = s.get("total", 0)
        section = "## Citation Check\n\n"
        section += (
            "_Advisory reference check (does each cited work exist and is its "
            "metadata correct). This does not affect the Replication Score._\n\n"
        )
        section += (
            f"**{total} references checked** — "
            f"{s.get('verified', 0)} verified, "
            f"{s.get('metadata_mismatch', 0)} metadata mismatch, "
            f"{s.get('likely_fabricated', 0)} likely fabricated, "
            f"{s.get('inconclusive', 0)} inconclusive.\n\n"
        )
        flagged = citation.get("flagged") or []
        if not flagged:
            section += f"No reference issues found: all {total} references verified.\n\n"
        else:
            section += "| Status | Ref | Detail | Source |\n"
            section += "|--------|-----|--------|--------|\n"
            label = {
                "likely_fabricated": "likely fabricated",
                "metadata_mismatch": "metadata mismatch",
                "inconclusive": "inconclusive",
                "unresolved": "unresolved",
            }
            for f in flagged:
                status = label.get(f.get("status", ""), f.get("status", ""))
                key = (f.get("key") or "").strip() or "?"
                detail = (f.get("detail") or "").replace("|", "\\|").replace("\n", " ").strip()
                rec = f.get("matched_record") or {}
                evidence = f.get("evidence") or []
                src = ""
                if isinstance(rec, dict) and rec.get("url"):
                    src = f"[{rec.get('source', 'record')}]({rec['url']})"
                elif evidence:
                    src = f"[evidence]({evidence[0]})"
                section += f"| {status} | `{key}` | {detail} | {src} |\n"
            section += "\n"
        if citation.get("checked_support") is False:
            section += (
                "_Note: this checks that references exist and are described "
                "correctly. It does not check citation support (whether each "
                "cited paper actually backs the claim it is cited for)._\n\n"
            )
        return section

    def _render_limitations(self, fix_assessment, report_block: dict) -> str:
        """Combine the deterministic fix-severity record with the manager's
        code-quality narrative into one Limitations section.

        Renders nothing when there is neither a fix record nor a narrative.
        """
        has_fixes = fix_assessment is not None and getattr(fix_assessment, "total_fixes", 0) > 0
        code_note = report_block.get("code_quality_limitations") if report_block else None
        has_note = isinstance(code_note, str) and code_note.strip()
        if not has_fixes and not has_note:
            return ""

        section = "## Limitations & Code Quality\n\n"
        if has_note:
            section += f"{code_note.strip()}\n\n"
        if has_fixes:
            section += (
                f"**Fixes the replication agent applied to make the work run:** "
                f"{fix_assessment.total_fixes} "
                f"({fix_assessment.minor_count} minor, {fix_assessment.major_count} major, "
                f"{fix_assessment.critical_count} critical). These are evidence about "
                f"the provided code/paper, not the replication.\n\n"
            )
            if fix_assessment.summary:
                section += f"{fix_assessment.summary}\n\n"
            if fix_assessment.fixes:
                section += "| # | Description | Severity | Impact |\n"
                section += "|---|-------------|----------|--------|\n"
                for i, fix in enumerate(fix_assessment.fixes, 1):
                    section += f"| {i} | {fix.fix_description} | {fix.severity} | {fix.reproducibility_impact} |\n"
                section += "\n"
        return section

    def _render_executive_summary(self, score: Optional[ReplicationScore], mode: Optional[str] = None) -> str:
        s = "## Executive Summary\n\n"
        if mode is not None:
            s += f"**Mode:** {mode}\n\n"
        if score is None or score.score is None:
            s += "**Replication Score: not computable** "
            if score is not None:
                s += f"({score.counted_claims}/{score.total_claims} claims counted)\n\n"
            else:
                s += "(no score data on disk)\n\n"
            return s
        s += f"**Replication Score: {score.score * 100:.1f}%** "
        s += f"({score.counted_claims}/{score.total_claims} claims counted)\n\n"
        return s

    def _render_code_generation_section(self, output_dir: Path) -> str:
        """Summarize the codebase that codegen produced (paper-only mode)."""
        codebase_dir = output_dir / "replication" / "codebase"
        section = "## Code Generation\n\n"
        if not codebase_dir.exists():
            section += "*No codegen artifacts found.*\n\n"
            return section

        py_files = list(codebase_dir.rglob("*.py"))
        n_py = len(py_files)
        total_loc = 0
        for p in py_files:
            try:
                total_loc += len(p.read_text(encoding='utf-8', errors='ignore').splitlines())
            except OSError:
                pass

        section += f"**Files written:** {n_py} Python module(s); total lines: {total_loc}\n\n"

        transcript = output_dir / "replication" / "codegen_transcript.jsonl"
        if transcript.exists():
            try:
                rel = transcript.relative_to(output_dir)
                section += f"**Codegen transcript:** `{rel}`\n\n"
            except ValueError:
                section += f"**Codegen transcript:** `{transcript}`\n\n"

        return section

    def _render_tier_breakdown(
        self,
        claims: PaperClaims,
        verdicts: List[ClaimVerdict],
        score: Optional[ReplicationScore],
    ) -> str:
        s = "## Tier Breakdown\n\n"
        s += "| Tier | Match | Partial | No match | Not attempted | n/a | Missing | Total |\n"
        s += "|---|---|---|---|---|---|---|---|\n"
        if score is None:
            return s + "\n*(no score data on disk)*\n\n"

        for tier in ("headline", "supporting", "setup"):
            counts = getattr(score, tier)
            total = sum(counts.values())
            if total == 0:
                continue
            s += (
                f"| {TIER_DISPLAY[tier]} "
                f"| {counts.get('match', 0)} "
                f"| {counts.get('partial', 0)} "
                f"| {counts.get('no_match', 0)} "
                f"| {counts.get('not_attempted', 0)} "
                f"| {counts.get('not_applicable', 0)} "
                f"| {counts.get('missing', 0)} "
                f"| {total} |\n"
            )
        return s + "\n"

    def _render_per_claim_table(
        self,
        claims: PaperClaims,
        verdicts: List[ClaimVerdict],
    ) -> str:
        verdict_by_id = {v.claim_id: v for v in verdicts}
        s = "## Per-Claim Verdicts\n\n"
        s += "| ID | Tier | Type | Status | Rationale | Evidence |\n"
        s += "|---|---|---|---|---|---|\n"
        for c in claims.claims:
            v = verdict_by_id.get(c.id)
            if v is None:
                s += f"| {c.id} | {c.tier} | {c.type} | **missing** | (no verdict produced) | — |\n"
                continue
            rationale = (v.rationale or "").replace("|", "\\|").replace("\n", " ")
            if len(rationale) > 180:
                rationale = rationale[:177] + "..."
            evidence_count = len(v.evidence_refs)
            s += (
                f"| {c.id} | {c.tier} | {c.type} "
                f"| {STATUS_DISPLAY.get(v.status, v.status)} "
                f"| {rationale} "
                f"| {evidence_count} file(s) |\n"
            )
        return s + "\n"

    def _render_flags(self, flags: List[str]) -> str:
        if not flags:
            return ""
        s = "## Flags\n\n"
        for f in flags:
            s += f"- {f}\n"
        return s + "\n"

    def _render_iterations(self, output_dir: Path) -> str:
        """Render the manager retry-loop trajectory from the workflow log.

        Reads ``<output>/.veritas/workflow.jsonl``. Returns an empty string when
        the loop did not run (no log, or only a single replicate pass with no
        manager review), so single-pass runs stay uncluttered. The Replication
        Score is unaffected by iteration count; this only notes what changed.
        """
        log_path = Path(output_dir) / ".veritas" / WORKFLOW_LOG_FILE
        if not log_path.exists():
            return ""
        records = []
        for line in log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        reviews = [r for r in records if r.get("phase") == "manager_review"]
        handoff = next(
            (r.get("handoff") for r in reversed(records) if r.get("phase") == "handoff"),
            None,
        )
        # No manager involvement -> loop effectively off; don't render.
        if not reviews and handoff is None:
            return ""

        n_iters = max(
            (r.get("iteration", 1) for r in records if r.get("iteration")),
            default=1,
        )
        s = "## Replication Iterations\n\n"
        s += (
            f"The manager-controlled retry loop ran. **Iterations: {n_iters}.** "
            "The Replication Score is computed deterministically and is "
            "unaffected by the iteration count; this section records the "
            "manager's review decisions and what each re-run changed.\n\n"
        )
        for r in reviews:
            verdict = r.get("manager_verdict") or {}
            it = r.get("iteration")
            decision = (verdict.get("decision") or "?").upper()
            s += f"- **Iteration {it} review:** {decision}"
            genuine = verdict.get("deficiency_is_genuine")
            if genuine:
                s += f" ({genuine})"
            s += "\n"
            if verdict.get("reason"):
                s += f"  - Reason: {verdict['reason']}\n"
            if decision == "REVISE" and verdict.get("directive"):
                s += f"  - New directive: {verdict['directive']}\n"
        if handoff is not None:
            s += (
                "\n**Unresolved hand-off (manager did not accept within the cap):** "
                f"{handoff.get('where_it_falls_short', '')}\n"
            )
            if handoff.get("what_to_try_next"):
                s += f"  - What to try next: {handoff['what_to_try_next']}\n"
        return s + "\n"

    def _render_replication_section(self, evidence) -> str:
        """Generate the Replication Attempt section."""
        section = "## Replication Attempt\n\n"

        # Environment
        env = evidence.environment
        env_parts = []
        if env.get("python_version"):
            env_parts.append(f"Python {env['python_version']}")
        if env.get("gpu_model"):
            env_parts.append(f"GPU: {env['gpu_model']}")
        elif env.get("gpu_available"):
            env_parts.append("GPU: Available")
        else:
            env_parts.append("GPU: None")

        pkgs = env.get("key_packages", {})
        if pkgs:
            pkg_strs = [f"{k} {v}" for k, v in list(pkgs.items())[:5]]
            env_parts.append(f"Packages: {', '.join(pkg_strs)}")

        section += f"**Environment:** {', '.join(env_parts)}\n"
        section += f"**Duration:** {evidence.total_duration_seconds:.0f}s\n"
        section += f"**Steps completed:** {evidence.steps_succeeded}/{evidence.steps_attempted}\n\n"

        # Steps table
        section += "| Step | Description | Result | Duration |\n"
        section += "|------|-------------|--------|----------|\n"

        for step in evidence.step_outcomes:
            status = "Success" if step.succeeded else "Failed"
            section += f"| {step.step_id} | {step.description} | {status} | {step.duration_seconds:.0f}s |\n"

        section += "\n"

        # Failures detail
        failures = [s for s in evidence.step_outcomes if not s.succeeded]
        if failures:
            section += "### Failures\n\n"
            for step in failures:
                section += f"**Step {step.step_id} — {step.description}**\n"
                if step.stderr:
                    section += f"```\n{step.stderr[:1000]}\n```\n"
                if step.fixes_applied:
                    section += "Fixes attempted:\n"
                    for fix in step.fixes_applied:
                        section += f"  - {fix.description} ({fix.file_path})\n"
                section += "\n"

        section += "\n"
        return section

    # -- HTML report (styled, human-facing) ---------------------------------

    def _templates_dir(self) -> Path:
        return Path(__file__).resolve().parents[3] / "templates"

    def _render_html(self, ctx: dict) -> str:
        env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(self._templates_dir())),
            autoescape=True,  # escape all {{ }} values; literal HTML in the template stays
        )
        return env.get_template("report/report.html.j2").render(**ctx)

    def _build_html_context(
        self, claims, verdicts, score, evidence, fix_assessment, evaluation, mode,
    ) -> dict:
        verdict_by_id = {v.claim_id: v for v in verdicts}
        claim_list = claims.claims if claims is not None else []
        total = len(claim_list)

        counts = {k: 0 for k in STATUS_COLOR}
        for c in claim_list:
            v = verdict_by_id.get(c.id)
            st = v.status if v is not None else "missing"
            counts[st] = counts.get(st, 0) + 1
        n_match, n_partial, n_nomatch = counts["match"], counts["partial"], counts["no_match"]
        n_missing = counts["missing"]
        n_other = counts["not_attempted"] + counts["not_applicable"] + n_missing

        score_val = score.score if (score is not None and score.score is not None) else None
        score_pct = score_val * 100 if score_val is not None else None
        if score_pct is None:
            verdict_label, badge_color = "No score", "#57606a"
        elif score_val >= 0.85:
            verdict_label, badge_color = "Reproduced", "#1a7f37"
        elif score_val >= 0.5:
            verdict_label, badge_color = "Partially reproduced", "#9a6700"
        else:
            verdict_label, badge_color = "Not reproduced", "#cf222e"
        circ = 2 * math.pi * 46

        def pct(n: int) -> float:
            return round(100 * n / total, 2) if total else 0

        chips, claims_table = [], []
        for c in claim_list:
            v = verdict_by_id.get(c.id)
            st = v.status if v is not None else "missing"
            color = STATUS_COLOR.get(st, "#57606a")
            chips.append({"id": c.id, "type": c.type, "status": st, "color": color})
            rat = ((v.rationale if v is not None else "") or "(no verdict produced)").replace("\n", " ")
            if len(rat) > 240:
                rat = rat[:237] + "..."
            claims_table.append({
                "id": c.id, "tier": c.tier, "type": c.type,
                "status_label": STATUS_DISPLAY.get(st, st), "color": color,
                "graded_by": (getattr(v, "graded_by", None) if v is not None else None), "rationale": rat,
            })

        rep = (evaluation or {}).get("report", {}) if evaluation else {}
        narrative = {
            k: ((rep.get(k) or "").strip() if isinstance(rep.get(k), str) else "")
            for k in ("important_claims", "replication_summary", "did_not_replicate",
                      "code_quality_limitations", "whole_paper_consistency",
                      "methodology_correspondence", "repo_divergence")
        }
        bottom_line = (rep.get("bottom_line") or "").strip() or None if isinstance(rep.get("bottom_line"), str) else None
        cheating_risk = ((evaluation or {}).get("cheating_monitor") or {}).get("risk") if evaluation else None

        steps, environment, duration_str, steps_ok = [], "", "", 0
        if evidence is not None:
            for s in evidence.step_outcomes:
                steps.append({"id": s.step_id, "desc": s.description, "ok": s.succeeded,
                              "duration": f"{s.duration_seconds:.0f}"})
            steps_ok = evidence.steps_succeeded
            duration_str = f"{evidence.total_duration_seconds:.0f}s"
            env = evidence.environment or {}
            parts = []
            if env.get("python_version"):
                parts.append(f"Python {env['python_version']}")
            if env.get("gpu_model"):
                parts.append(f"GPU {env['gpu_model']}")
            pkgs = env.get("key_packages", {})
            if pkgs:
                parts.append(", ".join(f"{k} {v}" for k, v in list(pkgs.items())[:5]))
            environment = " · ".join(parts)

        fixes, n_minor, n_major, n_critical, total_fixes = [], 0, 0, 0, 0
        if fix_assessment is not None and getattr(fix_assessment, "total_fixes", 0) > 0:
            total_fixes = fix_assessment.total_fixes
            n_minor, n_major, n_critical = (fix_assessment.minor_count,
                                            fix_assessment.major_count, fix_assessment.critical_count)
            for fx in fix_assessment.fixes:
                fixes.append({"desc": fx.fix_description, "severity": fx.severity,
                              "color": SEVERITY_COLOR.get(fx.severity, "#57606a")})

        return {
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "mode": mode, "paper_title": None,
            "score_pct": score_pct, "score_pct_str": f"{score_pct:.0f}%" if score_pct is not None else "—",
            "verdict_label": verdict_label, "badge_color": badge_color,
            "gauge_dash": round((score_val or 0) * circ, 1), "gauge_circ": round(circ, 1),
            "counted_match": n_match, "counted_total": (score.counted_claims if score is not None else total),
            "n_match": n_match, "n_partial": n_partial, "n_nomatch": n_nomatch,
            "n_other": n_other, "n_missing": n_missing,
            "tier_total": total,
            "seg_match": pct(n_match), "seg_partial": pct(n_partial),
            "seg_nomatch": pct(n_nomatch), "seg_other": pct(n_other),
            "claim_chips": chips, "claims_table": claims_table,
            "narrative": narrative, "bottom_line": bottom_line, "cheating_risk": cheating_risk,
            "steps": steps, "steps_ok": steps_ok, "duration_str": duration_str, "environment": environment,
            "fixes": fixes, "total_fixes": total_fixes,
            "n_minor": n_minor, "n_major": n_major, "n_critical": n_critical,
            "flags": (score.flags if (score is not None and score.flags) else []),
            "has_evaluation": evaluation is not None,
        }

    def _generate_pdf_from_html(self, html_content: str, output_path: Path) -> bool:
        """Render the styled HTML to PDF via WeasyPrint (keeps the HTML look).
        Returns True on success, False if WeasyPrint isn't available so the
        caller can fall back to the pandoc/LaTeX path."""
        try:
            from weasyprint import HTML  # type: ignore
        except Exception:
            return False
        try:
            HTML(string=html_content).write_pdf(str(output_path))
            return True
        except Exception as e:  # noqa: BLE001 — never let PDF break the run
            print(f"  Warning: WeasyPrint PDF failed ({e}); falling back to pandoc")
            return False

    def _preprocess_markdown_for_pdf(self, md_content: str) -> str:
        """Pre-process markdown for PDF rendering via pdflatex.

        Replaces emoji characters that can't be rendered by pdflatex's
        default Computer Modern font, which would break table rendering.
        """
        replacements = {
            "✅": "[YES]",
            "❌": "[NO]",
            "⚠️": "[WARN]",
            "⚠": "[WARN]",
            "➖": "[-]",
        }
        result = md_content
        for emoji, text in replacements.items():
            result = result.replace(emoji, text)
        return result

    def _get_latex_header(self) -> str:
        """Return LaTeX preamble for PDF rendering fixes."""
        return (
            "\\usepackage{microtype}\n"
            "\\usepackage{url}\n"
            "\\urlstyle{same}\n"
            "\\emergencystretch=3em\n"
            "\\setlength{\\parskip}{0.5em}\n"
        )

    def _generate_pdf(self, md_content: str, output_path: Path, working_dir: Path):
        """Generate PDF from markdown content."""
        # Pre-process markdown for PDF rendering
        pdf_md_content = self._preprocess_markdown_for_pdf(md_content)

        # Try pandoc first
        try:
            md_file = working_dir / "temp_report.md"
            md_file.write_text(pdf_md_content, encoding='utf-8')

            header_file = working_dir / "temp_header.tex"
            header_file.write_text(self._get_latex_header(), encoding='utf-8')

            subprocess.run(
                ["pandoc", str(md_file), "-o", str(output_path),
                 "--pdf-engine=pdflatex",
                 "-V", "geometry:margin=1in",
                 "-V", "colorlinks=true",
                 "--include-in-header", str(header_file)],
                check=True,
                capture_output=True,
            )

            md_file.unlink()
            header_file.unlink()
            return

        except (subprocess.CalledProcessError, FileNotFoundError):
            # Clean up temp files on failure
            for f in [working_dir / "temp_report.md", working_dir / "temp_header.tex"]:
                if f.exists():
                    f.unlink()
            pass

        # Try markdown-pdf or other tools
        try:
            subprocess.run(
                ["markdown-pdf", "-o", str(output_path)],
                input=md_content.encode(),
                check=True,
                capture_output=True,
            )
            return
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

        # If no PDF tool available, create a simple LaTeX file
        self._generate_latex_pdf(md_content, output_path, working_dir)

    def _generate_latex_pdf(self, md_content: str, output_path: Path, working_dir: Path):
        """Generate PDF via LaTeX."""
        # Convert markdown to simple LaTeX
        latex_content = self._markdown_to_latex(md_content)

        tex_file = working_dir / "temp_report.tex"
        tex_file.write_text(latex_content, encoding='utf-8')

        try:
            subprocess.run(
                ["pdflatex", "-interaction=nonstopmode", "-output-directory", str(working_dir), str(tex_file)],
                check=True,
                capture_output=True,
                cwd=working_dir,
            )

            # Move PDF to output path
            generated_pdf = working_dir / "temp_report.pdf"
            if generated_pdf.exists():
                generated_pdf.rename(output_path)

            # Cleanup
            for ext in [".tex", ".aux", ".log"]:
                temp = working_dir / f"temp_report{ext}"
                if temp.exists():
                    temp.unlink()

        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(f"  Warning: Could not generate PDF: {e}")

    def _markdown_to_latex(self, md_content: str) -> str:
        """Convert markdown to LaTeX (simplified)."""
        import re

        latex = r"""\documentclass[11pt]{article}
\usepackage[utf8]{inputenc}
\usepackage[margin=1in]{geometry}
\usepackage{hyperref}
\usepackage{longtable}
\usepackage{booktabs}
\usepackage{microtype}
\usepackage{url}
\urlstyle{same}
\emergencystretch=3em
\setlength{\parskip}{0.5em}

\begin{document}

"""
        # Pre-process: replace emoji with text
        content = self._preprocess_markdown_for_pdf(md_content)

        # Step 1: Extract and convert tables before escaping
        table_blocks = []
        table_pattern = re.compile(
            r'(^\|.+\|$\n^\|[-| :]+\|$\n(?:^\|.+\|$\n?)+)',
            re.MULTILINE
        )

        def convert_table(match):
            table_md = match.group(0)
            rows = table_md.strip().split('\n')
            # First row is header, second is separator, rest are data
            header_cells = [c.strip() for c in rows[0].strip('|').split('|')]
            num_cols = len(header_cells)
            col_spec = 'l' * num_cols

            table_latex = '\\begin{longtable}{' + col_spec + '}\n'
            table_latex += '\\toprule\n'
            table_latex += ' & '.join(header_cells) + ' \\\\\n'
            table_latex += '\\midrule\n'

            for row in rows[2:]:  # skip header and separator
                if row.strip():
                    cells = [c.strip() for c in row.strip('|').split('|')]
                    table_latex += ' & '.join(cells) + ' \\\\\n'

            table_latex += '\\bottomrule\n'
            table_latex += '\\end{longtable}'

            placeholder = f'%%TABLE_{len(table_blocks)}%%'
            table_blocks.append(table_latex)
            return placeholder

        content = table_pattern.sub(convert_table, content)

        # Step 2: Escape special LaTeX characters in raw markdown
        content = content.replace('&', '\\&')
        content = content.replace('%', '\\%')
        content = content.replace('_', '\\_')

        # Step 3: Restore table blocks (already properly formatted)
        for i, table_latex in enumerate(table_blocks):
            content = content.replace(f'%%TABLE\\_{i}%%', table_latex)

        # Step 4: Convert markdown to LaTeX commands
        content = re.sub(r'^# (.+)$', r'\\section*{\1}', content, flags=re.MULTILINE)
        content = re.sub(r'^## (.+)$', r'\\subsection*{\1}', content, flags=re.MULTILINE)
        content = re.sub(r'^### (.+)$', r'\\subsubsection*{\1}', content, flags=re.MULTILINE)

        # Convert horizontal rules to LaTeX
        content = re.sub(r'^---$', r'\\bigskip\\hrule\\bigskip', content, flags=re.MULTILINE)

        # Convert bold
        content = re.sub(r'\*\*(.+?)\*\*', r'\\textbf{\1}', content)

        # Convert italic
        content = re.sub(r'\*(.+?)\*', r'\\textit{\1}', content)

        # Convert numbered lists
        numbered_pattern = re.compile(r'^\d+\.\s+(.+)$', re.MULTILINE)
        content_lines = content.split('\n')
        in_enum = False
        in_list = False
        new_lines = []
        for line in content_lines:
            stripped = line.strip()
            is_numbered = bool(re.match(r'^\d+\.\s+', stripped))
            is_bullet = stripped.startswith('- ')

            if is_numbered:
                item_text = re.sub(r'^\d+\.\s+', '', stripped)
                if not in_enum:
                    if in_list:
                        new_lines.append('\\end{itemize}')
                        in_list = False
                    new_lines.append('\\begin{enumerate}')
                    in_enum = True
                new_lines.append(f'\\item {item_text}')
            elif is_bullet:
                item_text = stripped[2:]
                if not in_list:
                    if in_enum:
                        new_lines.append('\\end{enumerate}')
                        in_enum = False
                    new_lines.append('\\begin{itemize}')
                    in_list = True
                new_lines.append(f'\\item {item_text}')
            else:
                if in_enum:
                    new_lines.append('\\end{enumerate}')
                    in_enum = False
                if in_list:
                    new_lines.append('\\end{itemize}')
                    in_list = False
                new_lines.append(line)
        if in_enum:
            new_lines.append('\\end{enumerate}')
        if in_list:
            new_lines.append('\\end{itemize}')

        content = '\n'.join(new_lines)

        latex += content
        latex += r"""

\end{document}
"""
        return latex
