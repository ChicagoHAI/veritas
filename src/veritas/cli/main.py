"""Main CLI entry point for Veritas."""

import typer
from pathlib import Path
from typing import Optional
from rich.console import Console

from veritas.core.runner import ReplicationRunner
from veritas.core.config import Config

app = typer.Typer(
    name="veritas",
    help="Veritas: A replication agent for evaluating scientific reproducibility",
    no_args_is_help=True,
)
console = Console()


@app.command()
def evaluate(
    paper: Optional[Path] = typer.Option(
        None,
        "--paper", "-p",
        help="Path to the paper PDF file",
        exists=True,
        dir_okay=False,
    ),
    repo: Path = typer.Option(
        ...,
        "--repo", "-r",
        help="Path to the repository to evaluate",
        exists=True,
        file_okay=False,
    ),
    plan: Optional[Path] = typer.Option(
        None,
        "--plan",
        help="Path to an existing plan file (optional, will extract from paper if not provided)",
        exists=True,
        dir_okay=False,
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output", "-o",
        help="Output directory for the replication report (default: repo/evaluation)",
    ),
    provider: str = typer.Option(
        "claude",
        "--provider",
        help="AI provider to use (claude, codex, gemini)",
    ),
    generate_pdf: bool = typer.Option(
        True,
        "--pdf/--no-pdf",
        help="Generate PDF version of the report",
    ),
    evaluations: Optional[str] = typer.Option(
        None,
        "--evaluations", "-e",
        help="Comma-separated list of evaluations to run (default: all). "
             "Options: code,consistency,generalization,replication,instruction_following",
    ),
    analyze_timeout: Optional[int] = typer.Option(
        None,
        "--analyze-timeout",
        help="Timeout in seconds for the analyze phase (per LLM call). Default: no timeout.",
    ),
    replicate_timeout: Optional[int] = typer.Option(
        None,
        "--replicate-timeout",
        help="Timeout in seconds for the replicate phase (per LLM call). Default: no timeout.",
    ),
    evaluate_timeout: Optional[int] = typer.Option(
        None,
        "--evaluate-timeout",
        help="Timeout in seconds for the evaluate phase (per evaluation category). Default: no timeout.",
    ),
    mode: str = typer.Option(
        "main",
        "--mode",
        help="Replication scope: 'main' targets key claims, 'full' targets all results (not yet implemented)",
    ),
):
    """
    Evaluate the replicability of a scientific project.

    Takes a paper (PDF) and repository as input, and produces a comprehensive
    replication report assessing code quality, consistency, generalizability,
    , reproducibility and instruction following.
    """
    console.print("[bold blue]Veritas Replication Agent[/bold blue]")
    console.print()

    # Determine output directory
    output_dir = output or (repo / "evaluation")

    # Parse evaluations
    eval_list = None
    if evaluations:
        eval_list = [e.strip() for e in evaluations.split(",")]

    # Create config
    config = Config(
        paper_path=paper,
        repo_path=repo,
        plan_path=plan,
        output_dir=output_dir,
        provider=provider,
        generate_pdf=generate_pdf,
        evaluations=eval_list,
        analyze_timeout=analyze_timeout,
        replicate_timeout=replicate_timeout,
        evaluate_timeout=evaluate_timeout,
        mode=mode,
    )

    # Run evaluation
    runner = ReplicationRunner(config)

    try:
        result = runner.run()

        if result.success:
            console.print()
            console.print("[bold green]Evaluation completed successfully![/bold green]")
            console.print(f"Report saved to: {result.report_path}")
            if result.pdf_path:
                console.print(f"PDF saved to: {result.pdf_path}")
        else:
            console.print()
            console.print(f"[bold red]Evaluation failed:[/bold red] {result.error}")
            raise typer.Exit(1)

    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(1)


@app.command()
def extract_plan(
    paper: Path = typer.Argument(
        ...,
        help="Path to the paper PDF file",
        exists=True,
        dir_okay=False,
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output", "-o",
        help="Output path for the plan file",
    ),
    with_evidence: bool = typer.Option(
        False,
        "--with-evidence",
        help="Include evidence quotes from the paper",
    ),
):
    """
    Extract a structured plan from a paper PDF.

    This generates a plan.md file that can be used as input for evaluation.
    """
    from veritas.core.plan_extractor import PlanExtractor

    console.print(f"[blue]Extracting plan from:[/blue] {paper}")

    extractor = PlanExtractor()

    try:
        plan = extractor.extract(paper, with_evidence=with_evidence)

        # Determine output path
        if output is None:
            output = paper.parent / f"{paper.stem}_plan.md"

        output.write_text(plan, encoding='utf-8')
        console.print(f"[green]Plan saved to:[/green] {output}")

    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(1)


@app.command()
def report(
    evaluation_dir: Path = typer.Argument(
        ...,
        help="Path to the evaluation directory containing JSON results",
        exists=True,
        file_okay=False,
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output", "-o",
        help="Output path for the report",
    ),
    output_format: str = typer.Option(
        "all",
        "--format", "-f",
        help="Output format: md, pdf, or all",
    ),
):
    """
    Generate a replication report from evaluation results.

    This aggregates the evaluation JSON files and generates a comprehensive report.
    """
    from veritas.core.report_generator import ReportGenerator

    console.print(f"[blue]Generating report from:[/blue] {evaluation_dir}")

    generator = ReportGenerator()

    try:
        report_path, pdf_path = generator.generate(
            evaluation_dir=evaluation_dir,
            output_path=output,
            generate_pdf=(output_format in ["pdf", "all"]),
            generate_md=(output_format in ["md", "all"]),
        )

        if report_path:
            console.print(f"[green]Markdown report saved to:[/green] {report_path}")
        if pdf_path:
            console.print(f"[green]PDF report saved to:[/green] {pdf_path}")

    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(1)




if __name__ == "__main__":
    app()
