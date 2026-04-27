"""Generate replication reports from evaluation results."""

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from veritas.core.config import Config


class ReportGenerator:
    """Generates comprehensive replication reports."""

    # Display names for each evaluation category
    EVAL_DISPLAY_NAMES = {
        "code": "Code Quality",
        "consistency": "Consistency",
        "generalization": "Generalization",
        "replication": "Replicability",
        "instruction_following": "Instruction Following",
    }

    def generate(
        self,
        evaluation_dir: Path,
        output_path: Optional[Path] = None,
        generate_pdf: bool = True,
        generate_md: bool = True,
    ) -> Tuple[Optional[Path], Optional[Path]]:
        """
        Generate a replication report from evaluation JSON files.

        Args:
            evaluation_dir: Directory containing evaluation JSON files
            output_path: Output path for the report
            generate_pdf: Whether to generate PDF
            generate_md: Whether to generate markdown

        Returns:
            Tuple of (markdown_path, pdf_path)
        """
        results = self._collect_results(evaluation_dir)
        md_content = self._generate_markdown_report(results)

        if output_path is None:
            output_path = evaluation_dir / "report" / "replication_report.md"
        else:
            output_path = Path(output_path)

        md_path = None
        pdf_path = None

        if generate_md:
            md_path = output_path.with_suffix(".md")
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(md_content, encoding='utf-8')

        if generate_pdf:
            pdf_path = output_path.with_suffix(".pdf")
            report_dir = output_path.parent
            report_dir.mkdir(parents=True, exist_ok=True)
            self._generate_pdf(md_content, pdf_path, report_dir)

        return md_path, pdf_path

    def generate_from_results(
        self,
        results: List[Any],  # List[EvaluationResult]
        config: Config,
        output_dir: Path,
        generate_pdf: bool = True,
        evidence=None,
        fix_assessment=None,
    ) -> Tuple[Optional[Path], Optional[Path]]:
        """
        Generate report from EvaluationResult objects.

        Args:
            results: List of EvaluationResult objects
            config: Configuration object
            output_dir: Output directory
            generate_pdf: Whether to generate PDF
            evidence: Optional ExecutionEvidence from replication phase
            fix_assessment: Optional FixSeverityAssessment from fix assessment phase

        Returns:
            Tuple of (markdown_path, pdf_path)
        """
        results_dict = {}
        for result in results:
            results_dict[result.name] = {
                "success": result.success,
                "items": result.items or [],
                "pass_rate": result.pass_rate,
                "error": result.error,
            }

        md_content = self._generate_markdown_report(results_dict, evidence=evidence, fix_assessment=fix_assessment)

        md_path = output_dir / "report" / "replication_report.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(md_content, encoding='utf-8')

        pdf_path = None
        if generate_pdf:
            pdf_path = output_dir / "report" / "replication_report.pdf"
            self._generate_pdf(md_content, pdf_path, output_dir / "report")

        return md_path, pdf_path

    def _collect_results(self, evaluation_dir: Path) -> Dict[str, Any]:
        """Collect all evaluation JSON results in the new items+pass_rate format."""
        results = {}

        eval_files = {
            "code": "evaluate/code_evaluation.json",
            "consistency": "evaluate/consistency_evaluation.json",
            "generalization": "evaluate/generalization_evaluation.json",
            "replication": "evaluate/replication_evaluation.json",
            "instruction_following": "evaluate/instruction_following_evaluation.json",
        }

        for eval_name, filename in eval_files.items():
            filepath = evaluation_dir / filename
            if filepath.exists():
                try:
                    with open(filepath, encoding='utf-8') as f:
                        data = json.load(f)
                    results[eval_name] = {
                        "success": True,
                        "items": data.get("items", []),
                        "pass_rate": data.get("pass_rate"),
                    }
                except Exception as e:
                    results[eval_name] = {"success": False, "error": str(e)}

        return results

    def _generate_markdown_report(self, results: Dict[str, Any], evidence=None, fix_assessment=None) -> str:
        """Generate the markdown report content."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Calculate overall score from all items across categories
        total_items = 0
        passed_items = 0
        for eval_data in results.values():
            if eval_data.get("success") and eval_data.get("items"):
                for item in eval_data["items"]:
                    total_items += 1
                    if item.get("answer", "").upper() == "YES":
                        passed_items += 1

        overall_score = (passed_items / total_items * 100) if total_items > 0 else 0

        # Build report
        report = f"""# Replication Report

**Generated:** {now}

---

## Executive Summary

**Overall Replicability Score: {overall_score:.1f}%** ({passed_items}/{total_items} checks passed)

"""
        # Score interpretation
        if overall_score >= 80:
            report += "**High Replicability** - The project demonstrates strong reproducibility practices.\n\n"
        elif overall_score >= 60:
            report += "**Moderate Replicability** - Some areas need improvement for full reproducibility.\n\n"
        else:
            report += "**Low Replicability** - Significant issues identified that hinder reproduction.\n\n"

        # Replication evidence section (if available)
        if evidence:
            report += self._generate_replication_section(evidence)

        if fix_assessment and fix_assessment.total_fixes > 0:
            report += self._generate_fixes_section(fix_assessment)

        # Summary table
        report += "### Quick Summary\n\n"
        report += "| Evaluation | Pass Rate | Passed | Total |\n"
        report += "|------------|-----------|--------|-------|\n"

        for eval_type, display_name in self.EVAL_DISPLAY_NAMES.items():
            if eval_type in results:
                data = results[eval_type]
                if data.get("success") and data.get("items"):
                    items = data["items"]
                    total = len(items)
                    passed = sum(1 for it in items if it.get("answer", "").upper() == "YES")
                    pct = (passed / total * 100) if total > 0 else 0
                    report += f"| {display_name} | {pct:.1f}% | {passed} | {total} |\n"
                elif data.get("success"):
                    report += f"| {display_name} | - | 0 | 0 |\n"
                else:
                    report += f"| {display_name} | FAIL | - | - |\n"

        report += "\n---\n\n"

        # Detailed sections using generic method
        for eval_type, display_name in self.EVAL_DISPLAY_NAMES.items():
            if eval_type in results:
                report += self._generate_category_section(display_name, results[eval_type])

        # Recommendations
        report += self._generate_recommendations(results)

        return report

    def _generate_category_section(self, display_name: str, data: Dict) -> str:
        """Generate a report section for a single evaluation category.

        Args:
            display_name: Human-readable category name (e.g. "Code Quality")
            data: Dict with success, items, pass_rate, and optionally error

        Returns:
            Markdown string for the section
        """
        if not data.get("success"):
            error = data.get("error", "Unknown error")
            section = f"## {display_name}\n\n"
            section += f"[ERROR] Evaluation failed: {error}\n\n"
            return section

        items = data.get("items", [])
        if not items:
            section = f"## {display_name}\n\n"
            section += "No checklist items generated.\n\n"
            return section

        total = len(items)
        passed = sum(1 for it in items if it.get("answer", "").upper() == "YES")
        pass_rate = data.get("pass_rate")
        if pass_rate is not None:
            pct = pass_rate * 100
        else:
            pct = (passed / total * 100) if total > 0 else 0

        section = f"## {display_name} — {pct:.1f}% ({passed}/{total})\n\n"

        for item in items:
            question = item.get("question", "")
            answer = item.get("answer", "").upper()
            rationale = item.get("rationale", "")
            tag = "[YES]" if answer == "YES" else "[NO]"
            section += f"- {tag} {question}\n"
            if answer != "YES" and rationale:
                section += f"  - {rationale}\n"

        section += "\n"
        return section

    def _generate_replication_section(self, evidence) -> str:
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

    def _generate_fixes_section(self, fix_assessment) -> str:
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

    def _generate_recommendations(self, results: Dict) -> str:
        """Generate recommendations based on failed checklist items."""
        section = "## Recommendations\n\n"

        recommendations = []

        for eval_type, display_name in self.EVAL_DISPLAY_NAMES.items():
            if eval_type not in results:
                continue
            data = results[eval_type]
            if not data.get("success"):
                continue

            items = data.get("items", [])
            pass_rate = data.get("pass_rate")

            # Identify categories with failures
            failed_items = [it for it in items if it.get("answer", "").upper() != "YES"]
            if not failed_items:
                continue

            if pass_rate is not None and pass_rate < 0.5:
                recommendations.append(
                    f"**{display_name}** has a low pass rate ({pass_rate * 100:.0f}%). "
                    f"Address the following failed checks:"
                )
            elif failed_items:
                recommendations.append(
                    f"**{display_name}** has {len(failed_items)} failed check(s):"
                )

            for it in failed_items:
                q = it.get("question", "Unknown")
                r = it.get("rationale", "")
                detail = f"  - {q}"
                if r:
                    detail += f" — {r}"
                recommendations.append(detail)

        if recommendations:
            section += "\n"
            for rec in recommendations:
                section += f"{rec}\n"
        else:
            section += "No critical recommendations - the project demonstrates good reproducibility practices.\n"

        section += "\n---\n\n"
        section += "*Report generated by Veritas Replication Agent*\n"

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
