"""Generate replication reports from evaluation results."""

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from veritas.core.config import Config


class ReportGenerator:
    """Generates comprehensive replication reports."""

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
        # Collect all evaluation results
        results = self._collect_results(evaluation_dir)

        # Generate markdown report
        md_content = self._generate_markdown_report(results)

        # Determine output paths
        if output_path is None:
            output_path = evaluation_dir / "replication_report.md"
        else:
            output_path = Path(output_path)

        md_path = None
        pdf_path = None

        if generate_md:
            md_path = output_path.with_suffix(".md")
            md_path.write_text(md_content, encoding='utf-8')

        if generate_pdf:
            pdf_path = output_path.with_suffix(".pdf")
            self._generate_pdf(md_content, pdf_path, evaluation_dir)

        return md_path, pdf_path

    def generate_from_results(
        self,
        results: List[Any],  # List[EvaluationResult]
        config: Config,
        output_dir: Path,
        generate_pdf: bool = True,
    ) -> Tuple[Optional[Path], Optional[Path]]:
        """
        Generate report from EvaluationResult objects.

        Args:
            results: List of EvaluationResult objects
            config: Configuration object
            output_dir: Output directory
            generate_pdf: Whether to generate PDF

        Returns:
            Tuple of (markdown_path, pdf_path)
        """
        # Convert results to dict format
        results_dict = {}
        for result in results:
            results_dict[result.name] = {
                "success": result.success,
                "checklist": result.checklist or {},
                "rationale": result.rationale or {},
                "metrics": result.metrics or {},
                "error": result.error,
            }

        # Generate report
        md_content = self._generate_markdown_report(results_dict)

        # Write outputs
        md_path = output_dir / "replication_report.md"
        md_path.write_text(md_content, encoding='utf-8')

        pdf_path = None
        if generate_pdf:
            pdf_path = output_dir / "replication_report.pdf"
            self._generate_pdf(md_content, pdf_path, output_dir)

        return md_path, pdf_path

    def _collect_results(self, evaluation_dir: Path) -> Dict[str, Any]:
        """Collect all evaluation JSON results."""
        results = {}

        # Map of expected files
        eval_files = {
            "code": "code_evaluation.json",
            "consistency": "consistency_evaluation.json",
            "generalization": "generalization_evaluation.json",
            "replication": "replication_evaluation.json",
            "instruction": "instruction_evaluation.json",
        }

        for eval_name, filename in eval_files.items():
            filepath = evaluation_dir / filename
            if filepath.exists():
                try:
                    with open(filepath, encoding='utf-8') as f:
                        data = json.load(f)
                    results[eval_name] = {
                        "success": True,
                        "checklist": data.get("Checklist", {}),
                        "rationale": data.get("Rationale", {}),
                        "metrics": data.get("Metrics", {}),
                    }
                except Exception as e:
                    results[eval_name] = {
                        "success": False,
                        "error": str(e),
                    }

        return results

    def _generate_markdown_report(self, results: Dict[str, Any]) -> str:
        """Generate the markdown report content."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Calculate overall score
        total_checks = 0
        passed_checks = 0
        for eval_data in results.values():
            if eval_data.get("success") and eval_data.get("checklist"):
                for value in eval_data["checklist"].values():
                    if value != "NA":
                        total_checks += 1
                        if value == "PASS":
                            passed_checks += 1

        overall_score = (passed_checks / total_checks * 100) if total_checks > 0 else 0

        # Build report
        report = f"""# Replication Report

**Generated:** {now}

---

## Executive Summary

**Overall Replicability Score: {overall_score:.1f}%** ({passed_checks}/{total_checks} checks passed)

"""
        # Score interpretation
        if overall_score >= 80:
            report += "✅ **High Replicability** - The project demonstrates strong reproducibility practices.\n\n"
        elif overall_score >= 60:
            report += "⚠️ **Moderate Replicability** - Some areas need improvement for full reproducibility.\n\n"
        else:
            report += "❌ **Low Replicability** - Significant issues identified that hinder reproduction.\n\n"

        # Summary table
        report += "### Quick Summary\n\n"
        report += "| Evaluation | Status | Passed | Total |\n"
        report += "|------------|--------|--------|-------|\n"

        eval_names = {
            "code": "Code Quality",
            "consistency": "Consistency",
            "generalization": "Generalization",
            "replication": "Replicability",
            "instruction": "Instruction Following",
        }

        for eval_type, display_name in eval_names.items():
            if eval_type in results:
                data = results[eval_type]
                if data.get("success") and data.get("checklist"):
                    checklist = data["checklist"]
                    passed = sum(1 for v in checklist.values() if v == "PASS")
                    total = sum(1 for v in checklist.values() if v != "NA")
                    status = "✅" if passed == total else "⚠️" if passed > 0 else "❌"
                    report += f"| {display_name} | {status} | {passed} | {total} |\n"
                else:
                    report += f"| {display_name} | ❌ | - | - |\n"

        report += "\n---\n\n"

        # Detailed sections
        report += self._generate_code_section(results.get("code", {}))
        report += self._generate_consistency_section(results.get("consistency", {}))
        report += self._generate_generalization_section(results.get("generalization", {}))
        report += self._generate_replication_section(results.get("replication", {}))
        report += self._generate_instruction_section(results.get("instruction", {}))

        # Recommendations
        report += self._generate_recommendations(results)

        return report

    def _generate_code_section(self, data: Dict) -> str:
        """Generate code quality section."""
        section = "## Code Quality Assessment\n\n"

        if not data.get("success"):
            section += f"❌ Evaluation failed: {data.get('error', 'Unknown error')}\n\n"
            return section

        checklist = data.get("checklist", {})
        rationale = data.get("rationale", {})
        metrics = data.get("metrics", {})

        items = {
            "C1": "All core analysis code is runnable",
            "C2": "All implementations are correct",
            "C3": "No redundant code",
            "C4": "No irrelevant code",
        }

        for key, description in items.items():
            status = checklist.get(key, "N/A")
            icon = "✅" if status == "PASS" else "❌" if status == "FAIL" else "➖"
            section += f"- {icon} **{key}**: {description} - **{status}**\n"
            if key in rationale:
                section += f"  - {rationale[key]}\n"

        if metrics:
            section += "\n### Metrics\n\n"
            section += f"- Runnable: {metrics.get('runnable_pct', 'N/A')}%\n"
            section += f"- Incorrect: {metrics.get('incorrect_pct', 'N/A')}%\n"
            section += f"- Redundant: {metrics.get('redundant_pct', 'N/A')}%\n"
            section += f"- Irrelevant: {metrics.get('irrelevant_pct', 'N/A')}%\n"
            section += f"- Total blocks analyzed: {metrics.get('total_blocks', 'N/A')}\n"

        section += "\n"
        return section

    def _generate_consistency_section(self, data: Dict) -> str:
        """Generate consistency section."""
        section = "## Consistency Analysis\n\n"

        if not data.get("success"):
            section += f"❌ Evaluation failed: {data.get('error', 'Unknown error')}\n\n"
            return section

        checklist = data.get("checklist", {})
        rationale = data.get("rationale", {})

        items = {
            "CS1": "Results match conclusions",
            "CS2": "Implementation follows plan",
            "CS3": "Effect sizes are non-trivial",
            "CS4": "Design choices are justified",
            "CS5": "Statistical significance reported",
        }

        for key, description in items.items():
            status = checklist.get(key, "N/A")
            icon = "✅" if status == "PASS" else "❌" if status == "FAIL" else "➖"
            section += f"- {icon} **{key}**: {description} - **{status}**\n"
            if key in rationale:
                section += f"  - {rationale[key]}\n"

        section += "\n"
        return section

    def _generate_generalization_section(self, data: Dict) -> str:
        """Generate generalization section."""
        section = "## Generalization Results\n\n"

        if not data.get("success"):
            section += f"❌ Evaluation failed: {data.get('error', 'Unknown error')}\n\n"
            return section

        checklist = data.get("checklist", {})
        rationale = data.get("rationale", {})

        items = {
            "GT1": "Model generalization",
            "GT2": "Data generalization",
            "GT3": "Method generalization",
        }

        for key, description in items.items():
            status = checklist.get(key, "N/A")
            icon = "✅" if status == "PASS" else "❌" if status == "FAIL" else "➖"
            section += f"- {icon} **{key}**: {description} - **{status}**\n"
            if key in rationale:
                section += f"  - {rationale[key]}\n"

        section += "\n"
        return section

    def _generate_replication_section(self, data: Dict) -> str:
        """Generate replication section."""
        section = "## Replicability Assessment\n\n"

        if not data.get("success"):
            section += f"❌ Evaluation failed: {data.get('error', 'Unknown error')}\n\n"
            return section

        checklist = data.get("checklist", {})
        rationale = data.get("rationale", {})

        items = {
            "RP1": "Implementation reconstructable from documentation",
            "RP2": "Environment reproducible",
            "RP3": "Results deterministic and stable",
        }

        for key, description in items.items():
            status = checklist.get(key, "N/A")
            icon = "✅" if status == "PASS" else "❌" if status == "FAIL" else "➖"
            section += f"- {icon} **{key}**: {description} - **{status}**\n"
            if key in rationale:
                section += f"  - {rationale[key]}\n"

        section += "\n"
        return section

    def _generate_instruction_section(self, data: Dict) -> str:
        """Generate instruction following section."""
        section = "## Instruction Following\n\n"

        if not data.get("success"):
            section += f"❌ Evaluation failed: {data.get('error', 'Unknown error')}\n\n"
            return section

        checklist = data.get("checklist", {})
        rationale = data.get("rationale", {})

        items = {
            "TS1": "Goals align with research objectives",
            "TS2": "Methodology covers required analyses",
            "TS3": "All hypotheses tested",
            "TS4": "Components match described functions",
        }

        for key, description in items.items():
            status = checklist.get(key, "N/A")
            icon = "✅" if status == "PASS" else "❌" if status == "FAIL" else "➖"
            section += f"- {icon} **{key}**: {description} - **{status}**\n"
            if key in rationale:
                section += f"  - {rationale[key]}\n"

        section += "\n"
        return section

    def _generate_recommendations(self, results: Dict) -> str:
        """Generate recommendations based on failed checks."""
        section = "## Recommendations\n\n"

        recommendations = []

        # Code recommendations
        if "code" in results and results["code"].get("success"):
            checklist = results["code"].get("checklist", {})
            if checklist.get("C1") == "FAIL":
                recommendations.append("Fix code execution errors to ensure all code is runnable")
            if checklist.get("C2") == "FAIL":
                recommendations.append("Review and correct implementation logic errors")
            if checklist.get("C3") == "FAIL":
                recommendations.append("Remove duplicate/redundant code blocks")
            if checklist.get("C4") == "FAIL":
                recommendations.append("Remove code that doesn't contribute to project goals")

        # Consistency recommendations
        if "consistency" in results and results["consistency"].get("success"):
            checklist = results["consistency"].get("checklist", {})
            if checklist.get("CS1") == "FAIL":
                recommendations.append("Ensure conclusions are supported by actual results")
            if checklist.get("CS2") == "FAIL":
                recommendations.append("Align implementation with documented plan")
            if checklist.get("CS5") == "FAIL":
                recommendations.append("Add statistical significance tests and uncertainty measures")

        # Replication recommendations
        if "replication" in results and results["replication"].get("success"):
            checklist = results["replication"].get("checklist", {})
            if checklist.get("RP1") == "FAIL":
                recommendations.append("Improve documentation to enable reconstruction without guesswork")
            if checklist.get("RP2") == "FAIL":
                recommendations.append("Document environment setup with exact package versions")
            if checklist.get("RP3") == "FAIL":
                recommendations.append("Set random seeds and ensure deterministic execution")

        if recommendations:
            section += "\n"  # Ensure blank line before list for pandoc
            for i, rec in enumerate(recommendations, 1):
                section += f"{i}. {rec}\n"
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
            "✅": "[PASS]",
            "❌": "[FAIL]",
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
