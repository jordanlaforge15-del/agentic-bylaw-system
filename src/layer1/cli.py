from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from sqlalchemy.orm import Session

from layer1.db.base import CrossReference, Document, PageBlock, SourceFragment, SourceTable, SourceTableCell
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.config import get_settings
from layer1.models.enums import IngestionStatus
from layer1.pipeline.audit import audit_document_pages
from layer1.pipeline.export import export_document_json
from layer1.pipeline.ingest import ingest_file
from layer1.pipeline.ingest_dataset import ingest_geo_dataset
from layer1.profiles import available_profile_names, get_parsing_profile
from layer1.semantic.enrichment import enrich_document_semantics, validate_document_semantics
from layer1.validators.structural import validate_document_objects

app = typer.Typer(help="Layer 1 bylaw source normalization CLI")
console = Console()


@app.callback()
def main() -> None:
    """Normalize official bylaw sources into an addressable source model."""


@app.command()
def init_db(db_url: str | None = typer.Option(None, "--db-url", help="Database URL override")) -> None:
    create_all(db_url)
    console.print("[green]Database schema created.[/green]")


@app.command()
def ingest(
    file: Path = typer.Argument(..., exists=True, readable=True),
    db_url: str | None = typer.Option(None, "--db-url", help="Database URL override"),
    municipality: str | None = typer.Option(None, "--municipality"),
    bylaw_name: str | None = typer.Option(None, "--bylaw-name"),
    source_url: str | None = typer.Option(None, "--source-url"),
    profile: str = typer.Option(get_settings().parsing_profile, "--profile", help=f"Parsing profile ({', '.join(available_profile_names())})"),
    ocr: bool = typer.Option(False, "--ocr", help="Enable OCR where parser support is installed"),
    debug: bool = typer.Option(False, "--debug", help="Persist extra parser/debug metadata where available"),
    create_schema: bool = typer.Option(False, "--create-schema", help="Create tables before ingesting"),
    enrich: bool = typer.Option(False, "--enrich", help="Run semantic enrichment after successful ingest"),
) -> None:
    selected_profile = _parse_profile(profile)
    if create_schema:
        create_all(db_url)
    with session_scope(db_url) as session:
        document, run = ingest_file(
            session,
            file,
            municipality=municipality,
            bylaw_name=bylaw_name,
            source_url=source_url,
            ocr=ocr,
            debug=debug,
            profile=selected_profile,
        )
        if enrich and run.status != IngestionStatus.FAILED:
            report = enrich_document_semantics(session, document_id=document.id)
            console.print_json(data={"semantic_enrichment": report.model_dump()})
        _print_ingest_result(document.id, run.status.value, run.warnings_json, run.errors_json)
        if run.status == IngestionStatus.FAILED:
            raise typer.Exit(code=1)


@app.command("ingest-dir")
def ingest_dir(
    directory: Path = typer.Argument(..., exists=True, file_okay=False, readable=True),
    db_url: str | None = typer.Option(None, "--db-url", help="Database URL override"),
    profile: str = typer.Option(get_settings().parsing_profile, "--profile", help=f"Parsing profile ({', '.join(available_profile_names())})"),
    ocr: bool = typer.Option(False, "--ocr"),
    debug: bool = typer.Option(False, "--debug"),
    create_schema: bool = typer.Option(False, "--create-schema"),
) -> None:
    selected_profile = _parse_profile(profile)
    if create_schema:
        create_all(db_url)
    files = sorted(path for path in directory.iterdir() if path.suffix.lower() in {".pdf", ".txt", ".text", ".md"})
    if not files:
        console.print("[yellow]No ingestible files found.[/yellow]")
        return
    failed = 0
    with session_scope(db_url) as session:
        for file in files:
            console.print(f"Ingesting {file}")
            document, run = ingest_file(session, file, ocr=ocr, debug=debug, profile=selected_profile)
            _print_ingest_result(document.id, run.status.value, run.warnings_json, run.errors_json)
            failed += int(run.status == IngestionStatus.FAILED)
    if failed:
        raise typer.Exit(code=1)


@app.command()
def validate(
    document_id: int,
    db_url: str | None = typer.Option(None, "--db-url", help="Database URL override"),
) -> None:
    with session_scope(db_url) as session:
        report = _validate_from_db(session, document_id)
        console.print(report.model_dump_json(indent=2))
        if not report.ok:
            raise typer.Exit(code=1)


@app.command("enrich-semantics")
def enrich_semantics(
    document_id: int,
    db_url: str | None = typer.Option(None, "--db-url", help="Database URL override"),
) -> None:
    with session_scope(db_url) as session:
        if not session.get(Document, document_id):
            raise typer.BadParameter(f"Document {document_id} not found")
        report = enrich_document_semantics(session, document_id=document_id)
        console.print_json(data=report.model_dump())


@app.command("validate-semantics")
def validate_semantics(
    document_id: int,
    db_url: str | None = typer.Option(None, "--db-url", help="Database URL override"),
) -> None:
    with session_scope(db_url) as session:
        if not session.get(Document, document_id):
            raise typer.BadParameter(f"Document {document_id} not found")
        report = validate_document_semantics(session, document_id=document_id)
        console.print_json(data=report)
        if not report["ok"]:
            raise typer.Exit(code=1)


@app.command("export-json")
def export_json(
    document_id: int,
    out: Path = typer.Option(..., "--out", "-o"),
    db_url: str | None = typer.Option(None, "--db-url", help="Database URL override"),
) -> None:
    with session_scope(db_url) as session:
        export_document_json(session, document_id, out)
    console.print(f"[green]Exported document {document_id} to {out}[/green]")


@app.command("show-summary")
def show_summary(
    document_id: int,
    db_url: str | None = typer.Option(None, "--db-url", help="Database URL override"),
) -> None:
    with session_scope(db_url) as session:
        document = session.get(Document, document_id)
        if not document:
            console.print(f"[red]Document {document_id} not found[/red]")
            raise typer.Exit(code=1)
        counts = {
            "page_blocks": session.query(PageBlock).filter_by(document_id=document_id).count(),
            "fragments": session.query(SourceFragment).filter_by(document_id=document_id).count(),
            "tables": session.query(SourceTable).filter_by(document_id=document_id).count(),
            "cross_references": session.query(CrossReference).filter_by(document_id=document_id).count(),
        }
        console.print(
            {
                "id": document.id,
                "municipality": document.municipality,
                "bylaw_name": document.bylaw_name,
                "page_count": document.page_count,
                "parser_version": document.parser_version,
                **counts,
            }
        )


@app.command("ingest-dataset")
def ingest_dataset(
    config: Path = typer.Argument(..., exists=True, readable=True, help="Path to dataset YAML config"),
    db_url: str | None = typer.Option(None, "--db-url", help="Database URL override"),
    create_schema: bool = typer.Option(False, "--create-schema", help="Create tables before ingesting"),
) -> None:
    if create_schema:
        create_all(db_url)
    with session_scope(db_url) as session:
        result = ingest_geo_dataset(session, config)
        console.print(
            {
                "dataset_id": result.dataset.id,
                "name": result.dataset.name,
                "feature_count": result.dataset.feature_count,
                "parse_status": result.dataset.parse_status.value,
                "feature_warnings": result.feature_warnings,
                "warnings": result.warnings[:10],
            }
        )


@app.command("audit-pages")
def audit_pages(
    document_id: int,
    sample: int = typer.Option(5, "--sample", min=1, help="Number of high-risk pages to audit"),
    pages: str | None = typer.Option(None, "--pages", help="Comma-separated explicit pages, e.g. 5,12,26"),
    llm: bool = typer.Option(False, "--llm", help="Run structured LLM review on each selected page"),
    model: str | None = typer.Option(None, "--model", help="LLM model override for --llm mode"),
    out: Path | None = typer.Option(None, "--out", help="Write JSON audit report to a file"),
    db_url: str | None = typer.Option(None, "--db-url", help="Database URL override"),
) -> None:
    explicit_pages = _parse_pages_option(pages)
    with session_scope(db_url) as session:
        report = audit_document_pages(
            session,
            document_id,
            page_numbers=explicit_pages,
            sample_size=sample,
            use_llm=llm,
            llm_model=model,
        )
    _emit_json_report(report.model_dump(mode="json"), out)


@app.command("audit-page")
def audit_page(
    document_id: int,
    page: int = typer.Argument(..., min=1),
    llm: bool = typer.Option(False, "--llm", help="Run structured LLM review for the page"),
    model: str | None = typer.Option(None, "--model", help="LLM model override for --llm mode"),
    out: Path | None = typer.Option(None, "--out", help="Write JSON audit report to a file"),
    db_url: str | None = typer.Option(None, "--db-url", help="Database URL override"),
) -> None:
    with session_scope(db_url) as session:
        report = audit_document_pages(
            session,
            document_id,
            page_numbers=[page],
            sample_size=1,
            use_llm=llm,
            llm_model=model,
        )
    _emit_json_report(report.model_dump(mode="json"), out)


def _validate_from_db(session: Session, document_id: int):
    document = session.get(Document, document_id)
    if not document:
        raise typer.BadParameter(f"Document {document_id} not found")
    blocks = session.query(PageBlock).filter_by(document_id=document_id).all()
    fragments = session.query(SourceFragment).filter_by(document_id=document_id).all()
    tables = session.query(SourceTable).filter_by(document_id=document_id).all()
    table_ids = [table.id for table in tables]
    cells = session.query(SourceTableCell).filter(SourceTableCell.table_id.in_(table_ids)).all() if table_ids else []
    refs = session.query(CrossReference).filter_by(document_id=document_id).all()
    return validate_document_objects(
        page_count=document.page_count,
        blocks=blocks,
        fragments=fragments,
        tables=tables,
        table_cells=cells,
        cross_references=refs,
    )


def _print_ingest_result(document_id: int, status: str, warnings: list[str], errors: list[str]) -> None:
    console.print(f"Document {document_id}: {status}")
    for warning in warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")
    for error in errors:
        console.print(f"[red]error:[/red] {error}")


def _parse_pages_option(pages: str | None) -> list[int] | None:
    if not pages:
        return None
    parsed = []
    for raw in pages.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            parsed.append(int(raw))
        except ValueError as exc:
            raise typer.BadParameter(f"Invalid page number: {raw}") from exc
    return parsed or None


def _emit_json_report(payload: dict, out: Path | None) -> None:
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        console.print(f"[green]Wrote audit report to {out}[/green]")
        return
    console.print_json(data=payload)


def _parse_profile(name: str):
    try:
        return get_parsing_profile(name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
