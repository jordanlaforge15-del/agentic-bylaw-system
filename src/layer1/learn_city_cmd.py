"""layer1 learn-city command: run the agentic learning pipeline for a municipality.

This module adds the ``learn-city`` sub-command to the ``layer1`` Typer app.
It bridges the installed ``layer1`` package and the ``abs-learning`` sibling
directory (which houses Discovery, StructureAnalyst, SemanticMapper, and
ValidationAgent). The abs-learning modules are not installed packages — this
module adds them to sys.path the first time the command is invoked.

Typical invocation::

    layer1 learn-city \\
        --jurisdiction-code HRM-MAINLAND \\
        --name "Halifax Regional Municipality" \\
        --province "Nova Scotia" \\
        --seed-url https://www.halifax.ca/.../halifax-plan-area \\
        --output abs-learning/output/HRM-MAINLAND/manifest.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console


# --------------------------------------------------------------------------- #
# abs-learning sys.path setup                                                  #
# --------------------------------------------------------------------------- #
def _setup_abs_learning_syspath() -> None:
    """Prepend abs-learning module roots to sys.path.

    ``__file__`` is ``{project_root}/src/layer1/learn_city_cmd.py``; walking
    two parents gives the project root, and abs-learning lives alongside src/.
    Safe to call multiple times.
    """
    project_root = Path(__file__).resolve().parents[2]
    for p in (
        str(project_root / "abs-learning"),
        str(project_root / "abs-learning" / "src"),
    ):
        if p not in sys.path:
            sys.path.insert(0, p)


# --------------------------------------------------------------------------- #
# Internal helpers                                                              #
# --------------------------------------------------------------------------- #
console = Console()

# Shown to the user before the pipeline is invoked so they can abort if
# the estimated spend is higher than expected.
_COST_ESTIMATE = "~18 LLM calls expected, ~$2 at default model (claude-sonnet-4-6)"


class _StubRetrievalClient:
    """Null retrieval client — always returns None.

    When ``--retrieval`` is not provided the ValidationAgent uses this
    client, which means ``citation_resolution_rate`` will always be 0.0.
    The CLI warns the user so they know the signal is meaningless.
    """

    def lookup_citation(self, citation_path: str) -> dict | None:
        return None


class _DbRetrievalClient:
    """Retrieval client backed by a layer1 SourceFragment direct lookup.

    Looks up the citation_path column on SourceFragment rows for the given
    document_id. Sufficient for the ValidationAgent's citation-resolution-rate
    check without requiring the full fuzzy-search service.
    """

    def __init__(self, document_id: int, db_url: str | None = None) -> None:
        self._document_id = document_id
        self._db_url = db_url

    def lookup_citation(self, citation_path: str) -> dict | None:
        from layer1.db.session import session_scope
        from layer1.db.base import SourceFragment

        with session_scope(self._db_url) as session:
            fragment = (
                session.query(SourceFragment)
                .filter_by(
                    document_id=self._document_id,
                    citation_path=citation_path,
                )
                .first()
            )
            if fragment is None:
                return None
            return {"text": fragment.text or ""}


def _make_direct_pdf_source(url: str):
    """Build a minimal primary ``SourceDocument`` for a known PDF URL."""
    from manifest.models import SourceDocument

    return SourceDocument(
        document_name="Primary Bylaw (direct PDF)",
        document_type="bylaw",
        document_role="primary",
        parent_document=None,
        source_url=url,
        format="pdf",
        access_method="direct_download",
        page_count_estimate=None,
        last_amended=None,
        amendment_series=None,
        in_scope=True,
    )


def _print_manifest_summary(manifest, output: Path, stub_retrieval: bool) -> None:
    console.print("")
    console.print("[bold]── Manifest Summary ──────────────────────────────[/bold]")
    console.print(f"  jurisdiction   : {manifest.municipality.jurisdiction_code}")
    console.print(f"  sources        : {len(manifest.sources)}")
    console.print(f"  pipeline_ready : {manifest.pipeline_ready}")
    if manifest.qa_report:
        r = manifest.qa_report
        console.print(f"  qa.status      : {r.status}")
        if stub_retrieval:
            console.print(
                "  qa.citation_resolution_rate : [yellow]0.0 (stub client — meaningless)[/yellow]"
            )
        else:
            console.print(
                f"  qa.citation_resolution_rate : {r.citation_resolution_rate:.0%}"
            )
        console.print(f"  qa.zone_completeness        : {r.zone_completeness:.0%}")
    if manifest.flags:
        console.print("  flags:")
        for flag in manifest.flags:
            console.print(f"    [yellow]· {flag}[/yellow]")
    console.print(f"\n[green]Manifest written to:[/green] {output}")


# --------------------------------------------------------------------------- #
# The command function (registered in cli.py via app.command("learn-city"))    #
# --------------------------------------------------------------------------- #
def learn_city_command(
    jurisdiction_code: str = typer.Option(
        ..., "--jurisdiction-code", help="Unique code for the jurisdiction (e.g. HRM-MAINLAND)"
    ),
    name: str = typer.Option(..., "--name", help="Full municipality name"),
    province: str = typer.Option(..., "--province", help="Province name"),
    governing_body: Optional[str] = typer.Option(
        None, "--governing-body", help="Governing body name (optional)"
    ),
    seed_url: Optional[str] = typer.Option(
        None,
        "--seed-url",
        help="Municipality website URL to crawl for documents (mutually exclusive with --direct-pdf)",
    ),
    output: Path = typer.Option(
        ...,
        "--output",
        help="Path to write the CityIntakeManifest JSON",
    ),
    direct_pdf: Optional[str] = typer.Option(
        None,
        "--direct-pdf",
        help="Bypass Discovery: use this PDF URL as the sole source document",
    ),
    retrieval: Optional[str] = typer.Option(
        None,
        "--retrieval",
        help=(
            "Use a real retrieval client in the form document-id=<N>. "
            "Without this, a stub client is used and citation_resolution_rate will be 0.0."
        ),
    ),
    citation_samples_file: Optional[Path] = typer.Option(
        None,
        "--citation-samples",
        help="Path to a JSON array of citation path strings used for validation",
    ),
    model: str = typer.Option(
        "claude-sonnet-4-6",
        "--model",
        help="LLM model for the pipeline agents",
    ),
    db_url: Optional[str] = typer.Option(
        None, "--db-url", help="Database URL override (used when --retrieval is provided)"
    ),
) -> None:
    """Run the agentic learning pipeline for a municipality.

    Sequences DiscoveryAgent → StructureAnalystAgent → SemanticMapperAgent →
    ValidationAgent and writes a CityIntakeManifest JSON to --output.

    \b
    Examples:
      # Full discovery run:
      layer1 learn-city --jurisdiction-code HRM-MAINLAND \\\\
          --name "Halifax Regional Municipality" --province "Nova Scotia" \\\\
          --seed-url https://www.halifax.ca/.../halifax-plan-area \\\\
          --output abs-learning/output/HRM-MAINLAND/manifest.json

      # Bypass Discovery (known PDF URL):
      layer1 learn-city --jurisdiction-code HRM-MAINLAND \\\\
          --name "Halifax Regional Municipality" --province "Nova Scotia" \\\\
          --direct-pdf https://cdn.halifax.ca/mainland-lub.pdf \\\\
          --output abs-learning/output/HRM-MAINLAND/manifest.json

      # With real retrieval client (ingested document ID 4):
      layer1 learn-city ... --retrieval document-id=4
    """
    # ── Validate flag constraints ─────────────────────────────────────────
    if seed_url is None and direct_pdf is None:
        raise typer.BadParameter(
            "Provide either --seed-url (for discovery) or --direct-pdf (to bypass discovery)."
        )
    if seed_url is not None and direct_pdf is not None:
        raise typer.BadParameter(
            "--seed-url and --direct-pdf are mutually exclusive. Use one or the other."
        )

    # ── Ensure abs-learning is on sys.path ────────────────────────────────
    _setup_abs_learning_syspath()

    # All abs-learning imports are lazy (inside this function) so that the
    # layer1 package can be imported without pdfplumber / docling being
    # installed. Tests mock ``pipeline`` in sys.modules before invoking.
    try:
        import pipeline as _pipeline_mod
        from manifest.models import Municipality
        from agents.discovery import DiscoveryAgent
    except ImportError as exc:
        console.print(
            f"[red]Error:[/red] abs-learning modules are not available. "
            f"Ensure the abs-learning/ directory exists alongside src/. "
            f"Original error: {exc}"
        )
        raise typer.Exit(code=1)

    # ── Build Municipality ────────────────────────────────────────────────
    municipality = Municipality(
        name=name,
        jurisdiction_code=jurisdiction_code,
        province=province,
        governing_body=governing_body,
    )

    # ── Load citation samples ─────────────────────────────────────────────
    citation_samples: List[str] = []
    if citation_samples_file is not None:
        try:
            citation_samples = json.loads(
                citation_samples_file.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise typer.BadParameter(
                f"Could not load citation samples from {citation_samples_file}: {exc}"
            ) from exc

    # ── Build retrieval client ────────────────────────────────────────────
    use_stub_retrieval = True
    if retrieval is not None:
        parts = retrieval.split("=", 1)
        if len(parts) != 2 or parts[0].strip() != "document-id":
            raise typer.BadParameter(
                "--retrieval must be in the form 'document-id=<N>' (e.g. document-id=4)"
            )
        try:
            document_id = int(parts[1].strip())
        except ValueError as exc:
            raise typer.BadParameter(
                f"Invalid document ID in --retrieval: {parts[1]!r}"
            ) from exc
        retrieval_client = _DbRetrievalClient(document_id, db_url)
        use_stub_retrieval = False
    else:
        retrieval_client = _StubRetrievalClient()
        console.print(
            "[yellow]Note:[/yellow] No --retrieval provided; using stub client. "
            "citation_resolution_rate will be 0.0 (signal is meaningless without a real client)."
        )

    # ── Discover or use direct PDF ────────────────────────────────────────
    if direct_pdf is not None:
        sources = [_make_direct_pdf_source(direct_pdf)]
        console.print(f"[bold]Source:[/bold] {direct_pdf} (direct PDF, discovery skipped)")
    else:
        console.print(f"[bold]Discovery:[/bold] Crawling {seed_url} ...")
        from layer1._claude_code_client import ClaudeCodeClient

        discovery_agent = DiscoveryAgent(ClaudeCodeClient(), model=model)
        sources = discovery_agent.discover(municipality, seed_url)
        console.print(f"[green]Discovered {len(sources)} source document(s).[/green]")

    # ── Cost estimate ─────────────────────────────────────────────────────
    console.print(f"\n[bold]Cost estimate:[/bold] {_COST_ESTIMATE}")
    console.print(f"[bold]Running pipeline for {jurisdiction_code} ...[/bold]\n")

    # ── Run the learning pipeline ─────────────────────────────────────────
    # Using the module reference (_pipeline_mod.run_learning_pipeline) rather
    # than a local binding so tests can patch pipeline.run_learning_pipeline
    # in sys.modules and the patch is visible here.
    from layer1._claude_code_client import ClaudeCodeClient as _CCC

    manifest = _pipeline_mod.run_learning_pipeline(
        municipality=municipality,
        sources=sources,
        citation_samples=citation_samples,
        retrieval_client=retrieval_client,
        anthropic_client=_CCC(),
        model=model,
    )

    # ── Write output ──────────────────────────────────────────────────────
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    # ── Print summary ─────────────────────────────────────────────────────
    _print_manifest_summary(manifest, output, use_stub_retrieval)

    if not manifest.pipeline_ready:
        console.print(
            "\n[yellow]pipeline_ready=False[/yellow] — review the flags above "
            "before ingesting with [bold]layer1 ingest --manifest[/bold]."
        )
        raise typer.Exit(code=1)
