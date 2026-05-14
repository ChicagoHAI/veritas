"""Generate replication reports from per-claim verdicts and Replication Score."""

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from veritas.core.config import (
    Config,
    REPORT_SUBDIR,
    REPORT_MD_FILE,
    REPORT_PDF_FILE,
    VERIFY_SUBDIR,
    VERDICTS_FILE,
    REPLICATION_SCORE_FILE,
    PAPER_CLAIMS_FILE,
    ANALYZE_SUBDIR,
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


class ReportGenerator:
    """Generate the Replication Report."""

    def generate(
        self,
        evaluation_dir: Path,
        output_path: Optional[Path] = None,
        generate_pdf: bool = True,
        generate_md: bool = True,
    ) -> Tuple[Optional[Path], Optional[Path]]:
        """Re-render the report from on-disk artifacts.

        Reads paper_claims.json, verdicts.json, replication_score.json from
        the output directory and regenerates the markdown + PDF.
        """
        claims = self._load_claims(evaluation_dir)
        verdicts = self._load_verdicts(evaluation_dir)
        score = self._load_score(evaluation_dir)
        mode = self._load_mode(evaluation_dir)

        md_content = self._render(
            claims=claims, verdicts=verdicts, score=score,
            evidence=None, fix_assessment=None,
            mode=mode,
            output_dir=evaluation_dir,
        )

        if output_path is None:
            output_path = evaluation_dir / REPORT_SUBDIR / REPORT_MD_FILE
        else:
            output_path = Path(output_path)

        md_path: Optional[Path] = None
        pdf_path: Optional[Path] = None

        if generate_md:
            md_path = output_path.with_suffix(".md")
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(md_content, encoding='utf-8')

        if generate_pdf:
            pdf_path = output_path.with_suffix(".pdf")
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
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
        md_content = self._render(
            claims=claims, verdicts=verdicts, score=score,
            evidence=evidence, fix_assessment=fix_assessment,
            mode=config.mode,
            output_dir=output_dir,
        )

        report_dir = config.report_dir
        md_path = report_dir / REPORT_MD_FILE
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(md_content, encoding='utf-8')

        pdf_path: Optional[Path] = None
        if generate_pdf:
            pdf_path = report_dir / REPORT_PDF_FILE
            self._generate_pdf(md_content, pdf_path, report_dir)

        return md_path, pdf_path

    # -- Loading helpers (used by the standalone ``generate``) --------------

    def _load_claims(self, evaluation_dir: Path) -> Optional[PaperClaims]:
        path = evaluation_dir / ANALYZE_SUBDIR / PAPER_CLAIMS_FILE
        if not path.exists():
            return None
        with open(path, encoding='utf-8') as f:
            return PaperClaims.from_dict(json.load(f))

    def _load_verdicts(self, evaluation_dir: Path) -> List[ClaimVerdict]:
        path = evaluation_dir / VERIFY_SUBDIR / VERDICTS_FILE
        if not path.exists():
            return []
        with open(path, encoding='utf-8') as f:
            return [ClaimVerdict.from_dict(d) for d in json.load(f)]

    def _load_score(self, evaluation_dir: Path) -> Optional[ReplicationScore]:
        path = evaluation_dir / VERIFY_SUBDIR / REPLICATION_SCORE_FILE
        if not path.exists():
            return None
        with open(path, encoding='utf-8') as f:
            return ReplicationScore.from_dict(json.load(f))

    def _load_mode(self, evaluation_dir: Path) -> Optional[str]:
        """Recover the input mode from pipeline_state.json, if available."""
        state_path = evaluation_dir / ".veritas" / "pipeline_state.json"
        if not state_path.exists():
            return None
        try:
            with open(state_path, encoding='utf-8') as f:
                data = json.load(f)
            inputs = data.get("inputs") or {}
            return inputs.get("mode") or data.get("mode")
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

        report += self._render_executive_summary(score, mode=mode)

        if mode == "paper-only" and output_dir is not None:
            report += self._render_code_generation_section(output_dir)

        if claims is not None and verdicts:
            report += self._render_tier_breakdown(claims, verdicts, score)
            report += self._render_per_claim_table(claims, verdicts)

        if score is not None and score.flags:
            report += self._render_flags(score.flags)

        if evidence is not None:
            report += self._render_replication_section(evidence)

        if fix_assessment is not None and fix_assessment.total_fixes > 0:
            report += self._render_fixes_section(fix_assessment)

        report += "\n---\n\n*Report generated by Veritas Replication Agent*\n"
        return report

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
        """Mode-2 section: summarize the codebase that codegen produced."""
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

    def _render_fixes_section(self, fix_assessment) -> str:
        """Generate the Fixes Applied section."""
        section = "## Fixes Applied\n\n"
        section += f"**Total fixes:** {fix_assessment.total_fixes} "
        section += f"({fix_assessment.minor_count} minor, {fix_assessment.major_count} major, {fix_assessment.critical_count} critical)\n\n"

        if fix_assessment.summary:
            section += f"{fix_assessment.summary}\n\n"

        if fix_assessment.fixes:
            section += "| # | Description | Severity | Impact |\n"
            section += "|---|-------------|----------|--------|\n"
            for i, fix in enumerate(fix_assessment.fixes, 1):
                section += f"| {i} | {fix.fix_description} | {fix.severity} | {fix.reproducibility_impact} |\n"
            section += "\n"

        return section

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
