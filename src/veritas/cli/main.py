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
    repo: Optional[Path] = typer.Option(
        None,
        "--repo", "-r",
        help="Path to the repository to evaluate",
        exists=True,
        file_okay=False,
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output", "-o",
        help="Output directory (default: <repo>/evaluation or <paper-parent>/evaluation)",
    ),
    provider: str = typer.Option(
        "claude",
        "--provider",
        help="AI provider to use (claude, codex, gemini)",
    ),
    mode: str = typer.Option(
        "auto",
        "--mode",
        help=(
            "Input mode. 'auto' (default) infers from --paper/--repo presence. "
            "'full' = paper+repo. 'paper-only' = paper alone (agent writes the code). "
            "'repo-only' = repo alone (claims extracted from README)."
        ),
    ),
    claims: Optional[Path] = typer.Option(
        None,
        "--claims",
        help=(
            "Path to a user-authored claims JSON file (same schema as "
            "<output>/analyze/paper_claims.json). When provided, skips automatic "
            "claim extraction."
        ),
        exists=True,
        dir_okay=False,
    ),
    scope: str = typer.Option(
        "main",
        "--scope",
        help=(
            "Claim-extraction scope. 'main' targets headline+supporting (default); "
            "'full' (not yet implemented) includes setup tier."
        ),
    ),
    generate_pdf: bool = typer.Option(
        True,
        "--pdf/--no-pdf",
        help="Generate PDF version of the report",
    ),
    analyze_timeout: Optional[int] = typer.Option(
        None,
        "--analyze-timeout",
        help="Timeout in seconds for the analyze phase. Default: no timeout.",
    ),
    codegen_timeout: int = typer.Option(
        3600,
        "--codegen-timeout",
        help="Timeout in seconds for the codegen phase (paper-only mode only).",
    ),
    replicate_timeout: Optional[int] = typer.Option(
        None,
        "--replicate-timeout",
        help="Timeout in seconds for the replicate phase. Default: no timeout.",
    ),
    verify_timeout: Optional[int] = typer.Option(
        None,
        "--verify-timeout",
        help="Timeout in seconds for the verify phase (per claim). Default: no timeout.",
    ),
    restart: bool = typer.Option(
        False,
        "--restart",
        help="Discard previous run state and start fresh.",
    ),
):
    """
    Evaluate the replicability of a scientific paper against its code repository.

    Runs a multi-phase pipeline (analyze, plan, codegen [paper-only], replicate,
    assess_fixes, verify) and produces a Replication Score: a single tier-weighted
    number reflecting how many of the paper's structured claims the replication
    actually reproduced.
    """
    console.print("[bold blue]Veritas Replication Agent[/bold blue]")
    console.print()

    # Determine output directory: explicit --output wins; else <repo>/eval; else <paper-parent>/eval.
    # Config.__post_init__ also enforces this chain, but resolving here gives us a
    # path to write the .veritas/ state directory before constructing the runner.
    if output is not None:
        output_dir = output
    elif repo is not None:
        output_dir = repo / "evaluation"
    elif paper is not None:
        output_dir = paper.parent / "evaluation"
    else:
        console.print(
            "[bold red]Error:[/bold red] at least one of --paper or --repo is required"
        )
        raise typer.Exit(1)

    if restart:
        state_file = output_dir / ".veritas" / "pipeline_state.json"
        if state_file.exists():
            state_file.unlink()
            console.print("[yellow]Discarded previous pipeline state.[/yellow]")

    try:
        config = Config(
            paper_path=paper,
            repo_path=repo,
            output_dir=output_dir,
            provider=provider,
            generate_pdf=generate_pdf,
            analyze_timeout=analyze_timeout,
            codegen_timeout=codegen_timeout,
            replicate_timeout=replicate_timeout,
            verify_timeout=verify_timeout,
            claim_scope=scope,
            mode=mode,
            claims_path=claims,
        )
    except (ValueError, NotImplementedError) as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(1)

    console.print(f"[blue]Mode:[/blue] {config.mode}")

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
