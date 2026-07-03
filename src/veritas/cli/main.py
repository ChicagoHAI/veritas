"""Main CLI entry point for Veritas."""

import json
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
def replicate(
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
        help="Path to the repository to replicate",
        exists=True,
        file_okay=False,
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output", "-o",
        help="Output directory (default: <repo>/replicate or <paper-parent>/replicate)",
    ),
    provider: str = typer.Option(
        "claude",
        "--provider",
        help="AI provider to use (claude, codex, gemini, openrouter)",
    ),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        help=(
            "Global default model (bare name; the provider comes from "
            "--provider). Default: the provider CLI's own default."
        ),
    ),
    analyze_model: Optional[str] = typer.Option(
        None,
        "--analyze-model",
        help="Engine for the analyze bucket (claims + plan), as [provider:]model.",
    ),
    codegen_model: Optional[str] = typer.Option(
        None,
        "--codegen-model",
        help="Engine for the codegen bucket (paper-only mode), as [provider:]model.",
    ),
    replicate_model: Optional[str] = typer.Option(
        None,
        "--replicate-model",
        help="Engine for the replication session, as [provider:]model.",
    ),
    assess_model: Optional[str] = typer.Option(
        None,
        "--assess-model",
        help="Engine for the fix-severity assessment, as [provider:]model.",
    ),
    verify_model: Optional[str] = typer.Option(
        None,
        "--verify-model",
        help="Engine for per-claim verification, as [provider:]model.",
    ),
    evaluate_model: Optional[str] = typer.Option(
        None,
        "--evaluate-model",
        help=(
            "Engine for the evaluate bucket (manager review, research, "
            "contextual evaluation, citation check), as [provider:]model."
        ),
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
    data: Optional[Path] = typer.Option(
        None,
        "--data",
        help=(
            "Directory of pre-positioned data files. Mounted at /workspace/data/ "
            "(read-only) inside the container. Use when the agent shouldn't have "
            "to procure data from the network."
        ),
        exists=True,
        file_okay=False,
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
    codegen_timeout: Optional[int] = typer.Option(
        None,
        "--codegen-timeout",
        help="Timeout in seconds for the codegen phase (paper-only mode only). Default: no timeout.",
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
    evaluate: bool = typer.Option(
        False,
        "--evaluate/--no-evaluate",
        help="Run the post-verify contextual-evaluation phase (external checker: "
             "cheating monitor + consistency grader). Advisory; does not change the "
             "Replication Score. Default: off.",
    ),
    evaluate_timeout: Optional[int] = typer.Option(
        None,
        "--evaluate-timeout",
        help="Timeout in seconds for the contextual-evaluation phase. Default: no timeout.",
    ),
    check_citations: bool = typer.Option(
        False,
        "--check-citations/--no-check-citations",
        help="Run the opt-in citation-check submodule (verifies the paper's "
             "references exist and carry correct metadata via free scholarly "
             "APIs). Advisory; does not change the Replication Score. Requires "
             "--paper. Default: off.",
    ),
    citation_timeout: Optional[int] = typer.Option(
        None,
        "--citation-timeout",
        help="Timeout in seconds for the citation-check phase. Default: no timeout.",
    ),
    check_citations_faithfulness: Optional[str] = typer.Option(
        None,
        "--check-citations-faithfulness",
        help="Citation faithfulness scope when --check-citations is on: 'main' "
             "(central attributed claims only, default) or 'all' (every "
             "claim-bearing citation). Default: VERITAS_CITATION_FAITHFULNESS_SCOPE "
             "or 'main'.",
    ),
    max_iters: Optional[int] = typer.Option(
        None,
        "--max-iters",
        help=(
            "Max manager-controlled retry iterations for the replicate phase. "
            "1 (default) = single-pass (loop OFF; benchmark-comparable, identical "
            "to prior behavior). >1 enables the post-replicate manager gate that "
            "may re-run replication with new instructions, bounded by this cap. "
            "When unset, falls back to VERITAS_MAX_ITERS if set in .env, else 1."
        ),
    ),
    restart: bool = typer.Option(
        False,
        "--restart",
        help="Discard previous run state and start fresh.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Stop after resource estimation and print the estimate without running replication.",
    ),
):
    """
    Run the replication pipeline against a paper and/or its code repository.

    Runs a multi-phase pipeline (analyze, plan, codegen [paper-only], replicate,
    assess_fixes, verify) and produces a Replication Score: a single tier-weighted
    number reflecting how many of the paper's structured claims the replication
    actually reproduced.
    """
    console.print("[bold blue]Veritas Replication Agent[/bold blue]")
    console.print()

    # Determine output directory: explicit --output wins; else <repo>/replicate; else <paper-parent>/replicate.
    # Config.__post_init__ also enforces this chain, but resolving here gives us a
    # path to write the .veritas/ state directory before constructing the runner.
    if output is not None:
        output_dir = output
    elif repo is not None:
        output_dir = repo / "replicate"
    elif paper is not None:
        output_dir = paper.parent / "replicate"
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

    # Resolve max-iters (highest wins): --max-iters flag -> VERITAS_MAX_ITERS env
    # (only when explicitly set in the environment) -> 1 (single-pass default for
    # `replicate`, so the benchmark stays single-pass and behavior is identical
    # to before). The loop only engages when the resolved value is > 1.
    import os as _os
    if max_iters is not None:
        resolved_max_iters = max_iters
    elif _os.environ.get("VERITAS_MAX_ITERS", "").strip():
        resolved_max_iters = None  # let Config read VERITAS_MAX_ITERS itself
    else:
        resolved_max_iters = 1

    try:
        config_kwargs = dict(
            paper_path=paper,
            repo_path=repo,
            output_dir=output_dir,
            provider=provider,
            model=model,
            analyze_model=analyze_model,
            codegen_model=codegen_model,
            replicate_model=replicate_model,
            assess_model=assess_model,
            verify_model=verify_model,
            evaluate_model=evaluate_model,
            generate_pdf=generate_pdf,
            analyze_timeout=analyze_timeout,
            codegen_timeout=codegen_timeout,
            replicate_timeout=replicate_timeout,
            verify_timeout=verify_timeout,
            evaluate_timeout=evaluate_timeout,
            run_evaluation=evaluate,
            run_citation_check=check_citations,
            citation_timeout=citation_timeout,
            mode=mode,
            claims_path=claims,
            data_path=data,
        )
        if check_citations_faithfulness is not None:
            config_kwargs["faithfulness_scope"] = check_citations_faithfulness
        if resolved_max_iters is not None:
            config_kwargs["max_iters"] = resolved_max_iters
        config = Config(**config_kwargs)
    except (ValueError, NotImplementedError) as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(1)

    console.print(f"[blue]Mode:[/blue] {config.mode}")
    if config.max_iters > 1:
        console.print(
            f"[blue]Manager retry loop:[/blue] ON (max {config.max_iters} iterations)"
        )

    runner = ReplicationRunner(config)

    try:
        result = runner.run(dry_run=dry_run)
        if result.success and dry_run:
            console.print()
            console.print("[bold green]Dry run complete.[/bold green] No replication was run.")
        elif result.success:
            console.print()
            console.print("[bold green]Replication completed successfully![/bold green]")
            console.print(f"Report saved to: {result.report_path}")
            if result.pdf_path:
                console.print(f"PDF saved to: {result.pdf_path}")
        else:
            console.print()
            console.print(f"[bold red]Replication failed:[/bold red] {result.error}")
            raise typer.Exit(1)
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(1)


@app.command()
def estimate(
    paper: Optional[Path] = typer.Option(None, "--paper", "-p", help="Path to the paper PDF file", exists=True, dir_okay=False),
    repo: Optional[Path] = typer.Option(None, "--repo", "-r", help="Path to the repository", exists=True, file_okay=False),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output directory"),
    provider: str = typer.Option(
        "claude", "--provider",
        help="AI provider (claude, codex, gemini, openrouter)",
    ),
    model: Optional[str] = typer.Option(
        None, "--model",
        help="Global default model for the estimate (bare name).",
    ),
    analyze_model: Optional[str] = typer.Option(
        None, "--analyze-model",
        help="Engine for the analyze bucket (claims, plan, resource estimate), as [provider:]model.",
    ),
    mode: str = typer.Option("auto", "--mode"),
):
    """
    Estimate the compute and cost required to replicate a paper, without running replication.

    Runs analyze + plan + resource estimation, prints the estimate, and exits.
    """
    console.print("[bold blue]Veritas Resource Estimator[/bold blue]")
    console.print()

    if output is not None:
        output_dir = output
    elif repo is not None:
        output_dir = repo / "estimate"
    elif paper is not None:
        output_dir = paper.parent / "estimate"
    else:
        console.print("[bold red]Error:[/bold red] at least one of --paper or --repo is required")
        raise typer.Exit(1)

    try:
        config = Config(paper_path=paper, repo_path=repo, output_dir=output_dir,
                        provider=provider, model=model, analyze_model=analyze_model,
                        mode=mode)
    except (ValueError, NotImplementedError) as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(1)

    runner = ReplicationRunner(config)
    try:
        result = runner.run(dry_run=True)
        if not result.success:
            console.print(f"[bold red]Estimation failed:[/bold red] {result.error}")
            raise typer.Exit(1)
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(1)


@app.command()
def report(
    replicate_dir: Path = typer.Argument(
        ...,
        help="Path to the replication output directory containing JSON results",
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
    Generate a replication report from a prior run's output.

    This aggregates the JSON files produced by a replication run and generates a comprehensive report.
    """
    from veritas.core.report_generator import ReportGenerator

    console.print(f"[blue]Generating report from:[/blue] {replicate_dir}")

    generator = ReportGenerator()

    try:
        report_path, pdf_path = generator.generate(
            replicate_dir=replicate_dir,
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


@app.command()
def evaluate(
    replicate_dir: Path = typer.Argument(
        ...,
        help="An existing replication output directory to evaluate.",
        exists=True,
        file_okay=False,
    ),
    paper: Optional[Path] = typer.Option(
        None, "--paper",
        help="Paper PDF (overrides the path recovered from the run's saved config).",
    ),
    provider: str = typer.Option(
        "claude", "--provider",
        help="AI provider for the evaluation manager (claude, codex, gemini, openrouter).",
    ),
    model: Optional[str] = typer.Option(
        None, "--model",
        help="Global default model for the evaluation (bare name).",
    ),
    evaluate_model: Optional[str] = typer.Option(
        None, "--evaluate-model",
        help="Engine for the evaluate bucket, as [provider:]model.",
    ),
    evaluate_timeout: Optional[int] = typer.Option(
        None, "--evaluate-timeout",
        help="Timeout in seconds for the evaluation phase. Default: no timeout.",
    ),
    generate_pdf: bool = typer.Option(
        True, "--pdf/--no-pdf", help="Render the PDF report."
    ),
):
    """
    Run the evaluation manager on an existing replication and render the report.

    Adds the product layer (contextual evaluation + the human-facing report) to a
    directory produced by `./veritas replicate`, without re-running the pipeline.
    Replicate once (e.g. for a benchmark), evaluate later.
    """
    console.print(f"[blue]Evaluating replication at:[/blue] {replicate_dir}")

    # Recover the original inputs/mode from the run's state; fall back to the
    # patched codebase so evaluation works even if the source inputs moved.
    state_path = replicate_dir / ".veritas" / "pipeline_state.json"
    mode = "auto"
    repo = data = None
    recovered_paper = None
    if state_path.exists():
        try:
            st = json.loads(state_path.read_text(encoding="utf-8"))
            cfg = st.get("config") or {}
            inp = st.get("inputs") or {}
            mode = cfg.get("mode", "auto")
            recovered_paper = Path(inp["paper_path"]) if inp.get("paper_path") else None
            repo = Path(inp["repo_path"]) if inp.get("repo_path") else None
            data = Path(inp["data_path"]) if inp.get("data_path") else None
        except (OSError, ValueError):
            pass

    if paper is None:
        paper = recovered_paper
        if paper is not None and not paper.exists():
            console.print(
                f"[yellow]Note:[/yellow] the run's recorded paper path {paper} "
                f"does not resolve here; evaluating without the paper "
                f"(pass --paper to supply it)."
            )
            paper = None
    elif not paper.exists():
        console.print(f"[bold red]Error:[/bold red] --paper file not found: {paper}")
        raise typer.Exit(1)
    if repo is not None and not repo.exists():
        repo = None
    if repo is None:
        codebase = replicate_dir / "replication" / "codebase"
        repo = codebase if codebase.exists() else None
    # A paper-dependent mode can't validate without the paper; fall back.
    if paper is None and mode in ("full", "paper-only"):
        mode = "repo-only" if repo is not None else "auto"
    if repo is None and paper is None:
        console.print(
            "[bold red]Error:[/bold red] could not recover the original inputs "
            "(paper/repo) for this run, and no patched codebase is present. "
            "Cannot evaluate."
        )
        raise typer.Exit(1)

    try:
        config = Config(
            paper_path=paper,
            repo_path=repo,
            output_dir=replicate_dir,
            provider=provider,
            model=model,
            evaluate_model=evaluate_model,
            mode=mode,
            run_evaluation=True,
            evaluate_timeout=evaluate_timeout,
            generate_pdf=generate_pdf,
            data_path=data if (data and data.exists()) else None,
        )
    except (ValueError, NotImplementedError) as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(1)

    result = ReplicationRunner(config).evaluate_existing()
    if result.success:
        console.print("[bold green]Evaluation + report complete.[/bold green]")
        console.print(f"Report: {result.report_path}")
        if result.pdf_path:
            console.print(f"PDF: {result.pdf_path}")
    else:
        console.print(f"[bold red]Evaluation failed:[/bold red] {result.error}")
        raise typer.Exit(1)


@app.command(name="check-citations")
def check_citations(
    replicate_dir: Path = typer.Argument(
        ..., help="An existing replication output directory to citation-check.",
        exists=True, file_okay=False,
    ),
    paper: Optional[Path] = typer.Option(
        None, "--paper", help="Paper PDF (overrides the path recovered from the run's saved config).",
    ),
    check_citations_faithfulness: Optional[str] = typer.Option(
        None, "--check-citations-faithfulness",
        help="Faithfulness scope: 'main' or 'all'. Default: "
             "VERITAS_CITATION_FAITHFULNESS_SCOPE or 'main'.",
    ),
    provider: str = typer.Option(
        "claude", "--provider",
        help="AI provider for the citation check (claude, codex, gemini, openrouter).",
    ),
    model: Optional[str] = typer.Option(
        None, "--model",
        help="Global default model for the citation check (bare name).",
    ),
    evaluate_model: Optional[str] = typer.Option(
        None, "--evaluate-model",
        help="Engine for the evaluate bucket (the citation check rides it), as [provider:]model.",
    ),
    citation_timeout: Optional[int] = typer.Option(
        None, "--citation-timeout", help="Timeout (seconds) for the citation check.",
    ),
    generate_pdf: bool = typer.Option(True, "--pdf/--no-pdf", help="Render the PDF report."),
):
    """
    Run the citation check on an existing replication directory, then refresh the report.

    Recovers the paper path from the run's saved .veritas config (use --paper to
    override if the file moved). Advisory; does not change the Replication Score.
    """
    console.print(f"[blue]Citation-checking replication at:[/blue] {replicate_dir}")

    recovered_paper = paper
    if recovered_paper is None:
        state_path = replicate_dir / ".veritas" / "pipeline_state.json"
        if state_path.exists():
            try:
                st = json.loads(state_path.read_text(encoding="utf-8"))
                inp = st.get("inputs") or {}
                if inp.get("paper_path"):
                    recovered_paper = Path(inp["paper_path"])
            except (OSError, ValueError):
                pass
    if recovered_paper is None:
        console.print(
            "[bold red]Error:[/bold red] no paper path was found for this run "
            "(none recorded in the run's saved config). Pass --paper <path> "
            "(the citation check reads the paper's references)."
        )
        raise typer.Exit(1)
    if not recovered_paper.exists():
        console.print(
            f"[bold red]Error:[/bold red] the paper path {recovered_paper} does not "
            f"exist (the file may have moved). Pass --paper <path> with its current location."
        )
        raise typer.Exit(1)

    try:
        citation_kwargs = {}
        if check_citations_faithfulness is not None:
            citation_kwargs["faithfulness_scope"] = check_citations_faithfulness
        config = Config(
            paper_path=recovered_paper,
            output_dir=replicate_dir,
            provider=provider,
            model=model,
            evaluate_model=evaluate_model,
            mode="auto",
            run_citation_check=True,
            citation_timeout=citation_timeout,
            generate_pdf=generate_pdf,
            **citation_kwargs,
        )
    except (ValueError, NotImplementedError) as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(1)

    result = ReplicationRunner(config).check_citations_existing()
    if result.success:
        console.print("[bold green]Citation check + report complete.[/bold green]")
        if result.report_path:
            console.print(f"Report: {result.report_path}")
        if result.pdf_path:
            console.print(f"PDF: {result.pdf_path}")
    else:
        console.print(f"[bold red]Citation check failed:[/bold red] {result.error}")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
