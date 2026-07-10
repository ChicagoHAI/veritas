"""Generate replication reports from per-claim verdicts and Replication Score."""

import json
import math
import re
import subprocess
from collections import Counter
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
    ASSESS_SUBDIR,
    REPLICATION_SUBDIR,
    FIX_SEVERITY_FILE,
    EVALUATION_SUBDIR,
    EVALUATION_FILE,
    CITATION_CHECK_FILE,
    CITATION_AUDIT_FILE,
    WORKFLOW_LOG_FILE,
)
from veritas.core.models.fix_severity import FixSeverityAssessment
from veritas.core.pipeline_state import read_state_dict
from veritas.core.models.paper_claims import (
    ClaimVerdict,
    PaperClaims,
    ReplicationScore,
)
from veritas.core.replication import _extract_json, gather_evidence


# Header label for each tier in the report.
TIER_DISPLAY = {
    "headline": "Headline",
    "supporting": "Supporting",
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


def read_engines_from_state(output_dir) -> dict:
    """Resolved per-bucket engines recorded in the run's pipeline state.

    Returns ``{bucket: "provider:model"}``; empty when the state file is
    absent, unreadable, or predates engine tracking.
    """
    if not output_dir:
        return {}
    config = read_state_dict(output_dir).get("config") or {}
    prefix = "engine_"
    return {
        key[len(prefix):]: value
        for key, value in sorted(config.items())
        if key.startswith(prefix)
    }


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


def _normalize_audit_claim(claim) -> str:
    """Whitespace-collapsed, case-folded claim text for audit matching; empty
    for a missing or non-string claim."""
    if not isinstance(claim, str):
        return ""
    return " ".join(claim.split()).casefold()


def _build_audit_lookup(audit) -> dict:
    """Index audit items as ``(key, kind) -> [(normalized_claim, verdict)]``.

    Faithfulness verdicts are matched claim-first so one audit item cannot
    soften every row citing the same reference; normalization keeps the
    match tolerant of whitespace and case drift in the audit's copy of the
    claim text."""
    lookup = {}
    for it in (audit or {}).get("items") or []:
        if not (isinstance(it, dict) and isinstance(it.get("key"), str) and it.get("key")):
            continue
        # kind and audit_verdict are coerced so a wrong-typed (unhashable)
        # value degrades to an ignored item instead of a TypeError.
        lookup.setdefault((it.get("key"), _txt(it.get("kind"))), []).append(
            (_normalize_audit_claim(it.get("claim")), _txt(it.get("audit_verdict")))
        )
    return lookup


def _audit_verdict_for(lookup: dict, key, kind, claim=None, sole_row=False,
                       consumed=None):
    """Exact normalized-claim match first. A sole item for ``(key, kind)``
    also applies when it names no claim (integrity items and audits recorded
    before claim tracking) or when the check side has exactly one auditable
    row for the key (``sole_row``) — an unambiguous pairing, so the audit's
    paraphrase of the claim text cannot drop the verdict. Otherwise no
    verdict applies (conservative: better to keep the first-pass verdict
    than to soften the wrong row). Applied items are recorded in
    ``consumed`` so the caller can report audit items that paired with no
    row instead of dropping them silently."""
    if not isinstance(key, str):
        return None
    items = lookup.get((key, kind))
    if not items:
        return None
    normalized = _normalize_audit_claim(claim)
    if normalized:
        for i, (item_claim, verdict) in enumerate(items):
            if item_claim == normalized:
                if consumed is not None:
                    consumed.add((key, kind, i))
                return verdict
    if len(items) == 1 and (not items[0][0] or sole_row):
        if consumed is not None:
            consumed.add((key, kind, 0))
        return items[0][1]
    return None


def _txt(value) -> str:
    """Agent-JSON string field, coerced: non-strings render as empty."""
    return value if isinstance(value, str) else ""


def _safe_url(value) -> str:
    """Only http(s) URLs are emitted as links; other schemes render unlinked."""
    url = value if isinstance(value, str) else ""
    return url if url.startswith(("https://", "http://")) else ""


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
        # Reconstituted from artifacts so a from-disk re-render (report /
        # check-citations refresh) keeps the evidence and fixes sections the
        # original run rendered from in-memory data. The log is agent-written:
        # any malformed shape costs the evidence section, never the render.
        try:
            evidence = gather_evidence(replicate_dir / REPLICATION_SUBDIR)
        except Exception:
            evidence = None
        fix_assessment = self._load_fix_assessment(replicate_dir)
        citation = self._load_citation_check(replicate_dir)
        citation_audit = self._load_citation_audit(replicate_dir)

        engines = read_engines_from_state(replicate_dir)
        md_content = self._render(
            claims=claims, verdicts=verdicts, score=score,
            evidence=evidence, fix_assessment=fix_assessment,
            mode=mode,
            output_dir=replicate_dir,
            citation=citation, citation_audit=citation_audit,
            engines=engines,
        )
        html_content = self._render_html(self._build_html_context(
            claims, verdicts, score, evidence, fix_assessment, evaluation, mode,
            citation=citation, citation_audit=citation_audit, engines=engines,
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
        citation = self._load_citation_check(output_dir)
        citation_audit = self._load_citation_audit(output_dir)
        engines = read_engines_from_state(output_dir)
        md_content = self._render(
            claims=claims, verdicts=verdicts, score=score,
            evidence=evidence, fix_assessment=fix_assessment,
            mode=config.mode,
            output_dir=output_dir,
            citation=citation, citation_audit=citation_audit,
            engines=engines,
        )
        html_content = self._render_html(self._build_html_context(
            claims, verdicts, score, evidence, fix_assessment, evaluation, config.mode,
            citation=citation, citation_audit=citation_audit, engines=engines,
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
            data = json.loads(_extract_json(path.read_text(encoding="utf-8")))
            # Citation data is structured (keys, urls, counts), not free narrative,
            # so it is not run through _scrub_prose like the evaluation output.
            return data if isinstance(data, dict) else None
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def _load_citation_audit(self, replicate_dir: Optional[Path]) -> Optional[dict]:
        """Load the citation-audit output, if the audit pass ran. None if absent/malformed."""
        if replicate_dir is None:
            return None
        path = Path(replicate_dir) / EVALUATION_SUBDIR / CITATION_AUDIT_FILE
        if not path.exists():
            return None
        try:
            data = json.loads(_extract_json(path.read_text(encoding="utf-8")))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def _load_mode(self, replicate_dir: Path) -> Optional[str]:
        """Recover the input mode from pipeline_state.json, if available.

        Reads from ``state['config']`` — the canonical location, since ``mode``
        is a runtime configuration knob, not an input artifact.
        """
        config = read_state_dict(replicate_dir).get("config") or {}
        return config.get("mode")

    def _load_fix_assessment(self, replicate_dir: Path) -> Optional[FixSeverityAssessment]:
        """Load assess/fix_severity.json for a from-artifacts re-render.

        None when the assess phase didn't run or its output is malformed; the
        report then simply omits the fixes table.
        """
        path = replicate_dir / ASSESS_SUBDIR / FIX_SEVERITY_FILE
        if not path.exists():
            return None
        try:
            return FixSeverityAssessment.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError):
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
        citation: Optional[dict] = None,
        citation_audit: Optional[dict] = None,
        engines: Optional[dict] = None,
    ) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        report = f"# Replication Report\n\n**Generated:** {now}\n\n"
        engines = engines or {}
        if engines:
            report += "**Models:** " + ", ".join(
                f"{bucket}: {engine}" for bucket, engine in engines.items()
            ) + "\n\n"
        report += "---\n\n"

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
        # the score; rendered when present. Loaded once by the caller and
        # shared with the HTML context.
        report += self._render_citation_check(citation, citation_audit)

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

    _INTEGRITY_SEVERITY = {
        "likely_fabricated": 4, "metadata_mismatch": 3, "unresolved": 2,
        "inconclusive": 1, "verified": 0,
    }
    # "inaccessible" (the audit could not retrieve the source) is deliberately
    # absent: it carries no information, so it neither softens nor escalates.
    _FAITHFULNESS_SEVERITY = {"contradicted": 3, "not_mentioned": 2, "partially_supported": 1, "supported": 0}

    _INTEGRITY_LABEL = {
        "likely_fabricated": "likely fabricated",
        "metadata_mismatch": "metadata mismatch",
        "inconclusive": "inconclusive",
        "unresolved": "unresolved",
        "verified": "verified",
    }
    _FAITHFULNESS_LABEL = {
        "supported": "supported",
        "partially_supported": "partially supported",
        "contradicted": "contradicted",
        "not_mentioned": "not mentioned",
    }

    @staticmethod
    def _soften_verdict(first: str, audit, kind: str):
        """Conservative reconciliation: the final verdict is the less severe of the
        verify (``first``) and ``audit`` verdicts. The audit can only soften, never
        escalate. Returns (final_verdict, softened_bool). If the audit has no usable
        verdict for this item, keep ``first`` unchanged.
        """
        sev = (ReportGenerator._INTEGRITY_SEVERITY if kind == "integrity"
               else ReportGenerator._FAITHFULNESS_SEVERITY)
        if not audit or first not in sev or audit not in sev:
            return first, False
        if sev[audit] < sev[first]:
            return audit, True
        return first, False

    def _render_citation_check(self, citation: Optional[dict], audit: Optional[dict] = None) -> str:
        """Render the advisory citation-check section of the markdown report.

        Pure formatting: verdict reconciliation lives in ``_citation_view``,
        which the HTML/PDF section consumes too, so the two report formats
        cannot drift. Advisory: does not affect the Replication Score.
        """
        view = self._citation_view(citation, audit)
        if view is None:
            return ""

        def esc(text: str) -> str:
            return text.replace("|", "\\|")

        section = "## Citation Check\n\n"
        section += (
            "_Advisory reference check (does each cited work exist and is its "
            "metadata correct). This does not affect the Replication Score._\n\n"
        )
        section += (
            f"**{view['total']} references checked.** "
            f"{view['verified']} verified, "
            f"{view['mismatch']} metadata mismatch, "
            f"{view['fabricated']} likely fabricated, "
            f"{view['inconclusive']} inconclusive, "
            f"{view['unresolved']} unresolved.\n\n"
        )
        if view["has_audit"]:
            section += (
                "_Counts above are the first-pass result; the independent audit may "
                "have softened some verdicts, shown below._\n\n"
            )
        if not view["flagged"]:
            section += f"No reference issues flagged across {view['total']} references.\n\n"
        else:
            section += "| Status | Ref | Detail | Source |\n"
            section += "|--------|-----|--------|--------|\n"
            for row in view["flagged"]:
                src = f"[{row['source_label']}]({row['url']})" if row["url"] else ""
                section += (
                    f"| {esc(row['status_label'])} | `{esc(row['key'])}` "
                    f"| {esc(row['detail'])} | {esc(src)} |\n"
                )
            section += "\n"
        if view["support_not_checked"]:
            section += (
                "_Note: this checks that references exist and are described "
                "correctly. It does not check citation support (whether each "
                "cited paper actually backs the claim it is cited for)._\n\n"
            )

        if view["faith_checked"]:
            section += f"\n**Claim support ({view['faith_scope']} claims):** "
            section += (
                f"{view['faith_checked']} checked. "
                f"{view['faith_supported']} supported, "
                f"{view['faith_partial']} partially supported, "
                f"{view['faith_contradicted']} contradicted, "
                f"{view['faith_not_mentioned']} not mentioned, "
                f"{view['faith_inaccessible']} inaccessible.\n\n"
            )
            for row in view["faith_rows"]:
                section += f"- `{esc(row['key'])}` ({row['verdict_label']}): {row['claim']}"
                if row["quote"]:
                    section += f'  \n  Source says: "{row["quote"]}"'
                if row["source"]:
                    section += f"  \n  [source]({row['source']})"
                section += "\n"
            section += "\n"

        if view["softened_count"]:
            section += (
                f"\n_The independent audit softened {view['softened_count']} flagged "
                f"verdict(s) it could not confirm._\n\n"
            )
        if view["unmatched_audit"]:
            section += (
                f"\n_{view['unmatched_audit']} audit verdict(s) could not be "
                f"matched to a first-pass entry and were not applied._\n\n"
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

        for tier in ("headline", "supporting"):
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

    def _citation_view(self, citation: Optional[dict], audit: Optional[dict]) -> Optional[dict]:
        """Reconcile the citation check with its audit into presentation rows.

        The single home of verdict reconciliation: the markdown section and
        the HTML/PDF section are both thin formatters over these rows, so the
        two report formats cannot disagree. None when the check did not run.
        """
        if not citation:
            return None
        audit_lookup = _build_audit_lookup(audit)
        softened_count = 0
        consumed = set()

        s = citation.get("summary")
        s = s if isinstance(s, dict) else {}

        flagged_rows = []
        for f in citation.get("flagged") or []:
            if not isinstance(f, dict):
                continue
            first_status = _txt(f.get("status"))
            audit_verdict = _audit_verdict_for(
                audit_lookup, f.get("key"), "integrity", consumed=consumed)
            final_status, softened = self._soften_verdict(first_status, audit_verdict, "integrity")
            if softened:
                softened_count += 1
            status_label = self._INTEGRITY_LABEL.get(final_status, final_status)
            if softened:
                status_label += f" (audit softened from {first_status})"
            rec = f.get("matched_record")
            rec = rec if isinstance(rec, dict) else {}
            evidence = f.get("evidence")
            evidence = evidence if isinstance(evidence, list) else []
            url = _safe_url(rec.get("url"))
            source_label = (_txt(rec.get("source")) or "record") if url else ""
            if not url and evidence:
                url = _safe_url(evidence[0])
                source_label = "evidence" if url else ""
            flagged_rows.append({
                "status_label": status_label,
                "key": (_txt(f.get("key")).strip() or "?"),
                "detail": _txt(f.get("detail")).replace("\n", " ").strip(),
                "url": url,
                "source_label": source_label,
            })

        fsum = s.get("faithfulness")
        fsum = fsum if isinstance(fsum, dict) else {}
        faith_rows = []
        # Auditable rows per key — only verdicts the audit re-checks (see
        # _has_auditable_findings) can be the target of an audit item. With a
        # single such row, an item for the key pairs unambiguously even when
        # its claim text drifted.
        auditable_key_counts = Counter(
            _txt(f.get("key"))
            for f in citation.get("faithfulness") or []
            if isinstance(f, dict)
            and f.get("source_status") != "inaccessible"
            and f.get("verdict") in ("contradicted", "partially_supported")
        )
        if fsum.get("checked"):
            for f in citation.get("faithfulness") or []:
                if not isinstance(f, dict):
                    continue
                if f.get("source_status") == "inaccessible":
                    verdict_label = "source inaccessible"
                else:
                    first_verdict = _txt(f.get("verdict"))
                    audit_verdict = _audit_verdict_for(
                        audit_lookup, f.get("key"), "faithfulness", f.get("claim"),
                        sole_row=(
                            f.get("verdict") in ("contradicted", "partially_supported")
                            and auditable_key_counts[_txt(f.get("key"))] == 1
                        ),
                        consumed=consumed)
                    final_verdict, softened = self._soften_verdict(
                        first_verdict, audit_verdict, "faithfulness")
                    if softened:
                        softened_count += 1
                    verdict_label = self._FAITHFULNESS_LABEL.get(final_verdict, final_verdict or "?")
                    if softened:
                        verdict_label += f" (audit softened from {first_verdict})"
                    elif audit_verdict == "inaccessible":
                        verdict_label += " (audit could not retrieve the source)"
                faith_rows.append({
                    "key": (_txt(f.get("key")).strip() or "?"),
                    "verdict_label": verdict_label,
                    "claim": _txt(f.get("claim")).replace("\n", " ").strip(),
                    "quote": _txt(f.get("quote")).replace("\n", " ").strip(),
                    "source": _safe_url(f.get("source")),
                })

        return {
            "total": s.get("total", 0),
            "verified": s.get("verified", 0),
            "mismatch": s.get("metadata_mismatch", 0),
            "fabricated": s.get("likely_fabricated", 0),
            "inconclusive": s.get("inconclusive", 0),
            "unresolved": s.get("unresolved", 0),
            "flagged": flagged_rows,
            "has_audit": bool(audit_lookup),
            "unmatched_audit": (
                sum(len(v) for v in audit_lookup.values()) - len(consumed)
            ),
            "support_not_checked": citation.get("checked_support") is False,
            "faith_checked": fsum.get("checked", 0) or 0,
            "faith_scope": s.get("faithfulness_scope", "main"),
            "faith_supported": fsum.get("supported", 0),
            "faith_partial": fsum.get("partially_supported", 0),
            "faith_contradicted": fsum.get("contradicted", 0),
            "faith_not_mentioned": fsum.get("not_mentioned", 0),
            "faith_inaccessible": fsum.get("inaccessible", 0),
            "faith_rows": faith_rows,
            "softened_count": softened_count,
        }

    def _build_html_context(
        self, claims, verdicts, score, evidence, fix_assessment, evaluation, mode,
        citation=None, citation_audit=None, engines=None,
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
            "citations": self._citation_view(citation, citation_audit),
            "engines": engines or {},
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
