"""Extract structured plans from paper PDFs."""

import json
import re
from pathlib import Path
from typing import Optional, Dict, Any, List


class PlanExtractor:
    """Extracts structured research plans from paper PDFs."""

    def extract(
        self,
        paper_path: Path,
        with_evidence: bool = False
    ) -> str:
        """
        Extract a structured plan from a paper PDF.

        Args:
            paper_path: Path to the paper PDF
            with_evidence: Include evidence quotes with page numbers

        Returns:
            Markdown-formatted plan
        """
        # Read PDF content
        text, pages = self._read_pdf(paper_path)

        # Extract structured information
        plan_data = self._extract_plan_structure(text, pages if with_evidence else None)

        # Format as markdown
        if with_evidence:
            return self._format_plan_with_evidence(plan_data)
        else:
            return self._format_plan(plan_data)

    def _read_pdf(self, paper_path: Path) -> tuple[str, List[Dict]]:
        """Read text content from PDF."""
        try:
            import pdfplumber

            text_parts = []
            pages = []

            with pdfplumber.open(paper_path) as pdf:
                for i, page in enumerate(pdf.pages):
                    page_text = page.extract_text() or ""
                    text_parts.append(page_text)
                    pages.append({
                        "number": i + 1,
                        "text": page_text
                    })

            return "\n\n".join(text_parts), pages

        except ImportError:
            # Fallback to pypdf
            from pypdf import PdfReader

            reader = PdfReader(paper_path)
            text_parts = []
            pages = []

            for i, page in enumerate(reader.pages):
                page_text = page.extract_text() or ""
                text_parts.append(page_text)
                pages.append({
                    "number": i + 1,
                    "text": page_text
                })

            return "\n\n".join(text_parts), pages

    def _extract_plan_structure(
        self,
        text: str,
        pages: Optional[List[Dict]] = None
    ) -> Dict[str, Any]:
        """
        Extract structured plan from paper text.

        This performs basic extraction. In practice, this would be
        enhanced by an AI agent for more accurate extraction.
        """
        plan = {
            "objective": self._extract_objective(text),
            "hypothesis": self._extract_hypotheses(text),
            "methodology": self._extract_methodology(text),
            "experiments": self._extract_experiments(text),
        }

        if pages:
            plan["unknowns"] = self._identify_unknowns(text)
            plan = self._add_evidence(plan, pages)

        return plan

    def _extract_objective(self, text: str) -> Dict[str, Any]:
        """Extract the main objective from the paper."""
        # Look for common objective indicators
        patterns = [
            r"(?:The\s+)?(?:main\s+)?objective\s+(?:of\s+this\s+(?:paper|work|study))?\s*(?:is\s+)?to\s+(.+?)(?:\.|$)",
            r"(?:We|This\s+paper)\s+(?:aim|propose|present|introduce)s?\s+to\s+(.+?)(?:\.|$)",
            r"(?:Our\s+)?goal\s+is\s+to\s+(.+?)(?:\.|$)",
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
            if match:
                return {"text": match.group(1).strip()}

        # Fallback: use abstract or intro
        abstract_match = re.search(
            r"abstract[:\s]*(.+?)(?:introduction|1\.|keywords)",
            text,
            re.IGNORECASE | re.DOTALL
        )
        if abstract_match:
            # Take first 2 sentences
            abstract = abstract_match.group(1).strip()
            sentences = re.split(r'(?<=[.!?])\s+', abstract)
            return {"text": " ".join(sentences[:2])}

        return {"text": "Objective not clearly identified"}

    def _extract_hypotheses(self, text: str) -> Dict[str, Any]:
        """Extract hypotheses from the paper."""
        hypotheses = []

        # Look for hypothesis indicators
        patterns = [
            r"(?:We\s+)?hypothesize\s+that\s+(.+?)(?:\.|$)",
            r"(?:Our\s+)?hypothesis\s+(?:is\s+)?(?:that\s+)?(.+?)(?:\.|$)",
            r"H\d+[:\s]+(.+?)(?:\.|$)",
        ]

        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE | re.MULTILINE)
            hypotheses.extend([m.strip() for m in matches])

        if not hypotheses:
            # Look for research questions
            rq_pattern = r"(?:RQ|Research\s+Question)\s*\d*[:\s]+(.+?)(?:\?|$)"
            matches = re.findall(rq_pattern, text, re.IGNORECASE | re.MULTILINE)
            hypotheses.extend([f"{m.strip()}?" for m in matches])

        return {"items": hypotheses if hypotheses else ["No explicit hypotheses found"]}

    def _extract_methodology(self, text: str) -> Dict[str, Any]:
        """Extract methodology from the paper."""
        methods = []

        # Look for methodology section
        method_match = re.search(
            r"(?:methodology|methods?|approach)[:\s]*(.+?)(?:experiments?|results?|evaluation|\d+\.)",
            text,
            re.IGNORECASE | re.DOTALL
        )

        if method_match:
            method_text = method_match.group(1).strip()
            # Extract bullet points or numbered items
            items = re.findall(r'(?:^|\n)\s*(?:\d+\.|[-•])\s*(.+?)(?=\n|$)', method_text)
            if items:
                methods = [item.strip() for item in items]
            else:
                # Split by sentences
                sentences = re.split(r'(?<=[.!?])\s+', method_text)
                methods = sentences[:5]  # First 5 sentences

        return {"items": methods if methods else ["Methodology not clearly extracted"]}

    def _extract_experiments(self, text: str) -> Dict[str, Any]:
        """Extract experiment descriptions from the paper."""
        experiments = []

        # Look for experiment section
        exp_match = re.search(
            r"(?:experiments?|evaluation|empirical)[:\s]*(.+?)(?:results?|discussion|conclusion|\d+\.)",
            text,
            re.IGNORECASE | re.DOTALL
        )

        if exp_match:
            exp_text = exp_match.group(1).strip()

            # Try to identify individual experiments
            exp_patterns = [
                r"(?:Experiment|Exp\.?)\s*\d+[:\s]+(.+?)(?=Experiment|Exp\.?|\n\n|$)",
                r"(?:We\s+)?(?:conduct|perform|run)\s+(.+?)(?:\.|$)",
            ]

            for pattern in exp_patterns:
                matches = re.findall(pattern, exp_text, re.IGNORECASE)
                for match in matches:
                    experiments.append({
                        "name": match[:50].strip() + "..." if len(match) > 50 else match.strip(),
                        "description": match.strip()
                    })

        return {"items": experiments if experiments else [{"name": "Experiments not clearly extracted"}]}

    def _identify_unknowns(self, text: str) -> List[str]:
        """Identify unclear or missing information."""
        unknowns = []

        # Check for common missing information
        checks = [
            ("hyperparameters", r"hyperparameter|learning\s+rate|batch\s+size"),
            ("random seeds", r"random\s+seed|reproducib"),
            ("compute resources", r"GPU|TPU|compute|hardware"),
            ("dataset splits", r"train.*test.*split|cross.?validation"),
            ("statistical significance", r"p-value|significance|confidence\s+interval"),
        ]

        for name, pattern in checks:
            if not re.search(pattern, text, re.IGNORECASE):
                unknowns.append(f"No mention of {name}")

        return unknowns

    def _add_evidence(self, plan: Dict, pages: List[Dict]) -> Dict:
        """Add evidence quotes with page numbers."""
        # This is a simplified implementation
        # A full implementation would use an AI to identify relevant quotes
        for key in ["objective", "hypothesis", "methodology"]:
            if key in plan and "text" in plan[key]:
                search_text = plan[key].get("text", plan[key].get("items", [""])[0] if isinstance(plan[key].get("items"), list) else "")
                if search_text:
                    for page in pages:
                        if search_text[:50].lower() in page["text"].lower():
                            plan[key]["evidence"] = [{
                                "page": page["number"],
                                "quote": search_text[:200]
                            }]
                            break

        return plan

    def _format_plan(self, plan: Dict) -> str:
        """Format plan as markdown without evidence."""
        md = "# Research Plan\n\n"

        # Objective
        md += "## Objective\n\n"
        md += f"{plan['objective'].get('text', 'Not identified')}\n\n"

        # Hypotheses
        md += "## Hypotheses\n\n"
        for item in plan['hypothesis'].get('items', []):
            md += f"- {item}\n"
        md += "\n"

        # Methodology
        md += "## Methodology\n\n"
        for item in plan['methodology'].get('items', []):
            md += f"- {item}\n"
        md += "\n"

        # Experiments
        md += "## Experiments\n\n"
        for exp in plan['experiments'].get('items', []):
            if isinstance(exp, dict):
                md += f"### {exp.get('name', 'Unnamed')}\n\n"
                md += f"{exp.get('description', '')}\n\n"
            else:
                md += f"- {exp}\n"

        return md

    def _format_plan_with_evidence(self, plan: Dict) -> str:
        """Format plan as markdown with evidence."""
        md = self._format_plan(plan)

        # Add evidence section
        if "unknowns" in plan and plan["unknowns"]:
            md += "\n## Unknowns / Missing Information\n\n"
            for unknown in plan["unknowns"]:
                md += f"- {unknown}\n"

        # Add evidence quotes
        md += "\n## Evidence\n\n"
        for key in ["objective", "hypothesis", "methodology"]:
            if key in plan and "evidence" in plan[key]:
                md += f"### {key.title()}\n\n"
                for ev in plan[key]["evidence"]:
                    md += f"Page {ev['page']}: \"{ev['quote']}\"\n\n"

        return md
