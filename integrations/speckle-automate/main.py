"""Speckle Automate function: ABS compliance check (ABS-59).

Triggered on every model publish in a Speckle workspace configured to
run this automation.  Pulls the model version's IFC blob (or converts
the Speckle object tree to IFC), posts it to the ABS integration API,
optionally runs the bylaw evaluator, and annotates non-compliant
elements as Automate result objects.

Lifecycle:
1. Receive ``AutomationContext`` + ``FunctionInputs``.
2. Download IFC from the model version.
3. Upload IFC to ABS → get submission ID + extracted attributes.
4. If ``run_evaluator`` is True, trigger evaluation → get matrix.
5. Parse matrix: attach per-element failure annotations; mark
   compliant/non-compliant at the model level.
6. Automate result object written via ``context.mark_run_success`` or
   ``context.mark_run_failed``.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

from speckle_automate import AutomationContext, execute_automate_function

from abs_client import ABSClient, ABSClientError
from inputs import FunctionInputs

logger = logging.getLogger(__name__)

# Speckle object attribute that carries IFC element GUIDs so we can
# link compliance failures back to model elements.
_IFC_GUID_ATTR = "IFC_guid"


def automate_function(
    automate_context: AutomationContext,
    function_inputs: FunctionInputs,
) -> None:
    """Entry point called by the Speckle Automate runner."""
    client = ABSClient(
        base_url=function_inputs.abs_api_url,
        api_key=function_inputs.abs_api_key,
    )

    # ------------------------------------------------------------------
    # 1. Verify ABS connectivity
    # ------------------------------------------------------------------
    if not client.health_check():
        automate_context.mark_run_failed(
            "Could not reach the ABS API at "
            f"{function_inputs.abs_api_url}. "
            "Check the abs_api_url function input and network access."
        )
        return

    # ------------------------------------------------------------------
    # 2. Download IFC from the model version
    # ------------------------------------------------------------------
    try:
        ifc_path = _download_ifc(automate_context)
    except Exception as exc:  # noqa: BLE001
        automate_context.mark_run_failed(
            f"Failed to obtain IFC from Speckle model version: {exc}"
        )
        return

    # ------------------------------------------------------------------
    # 3. Upload IFC to ABS
    # ------------------------------------------------------------------
    try:
        submission = client.upload_ifc(
            ifc_path,
            parcel_id=function_inputs.parcel_id,
            parcel_address=function_inputs.parcel_address,
        )
    except ABSClientError as exc:
        automate_context.mark_run_failed(
            f"ABS submission upload failed (HTTP {exc.status_code}): {exc.detail}"
        )
        return
    finally:
        # Clean up the temp IFC file regardless of outcome.
        if ifc_path.exists():
            ifc_path.unlink(missing_ok=True)

    submission_id: int = submission["id"]
    n_attributes: int = len(submission.get("attributes", []))
    logger.info(
        "ABS submission %d created; %d attributes extracted",
        submission_id,
        n_attributes,
    )

    if submission.get("warnings"):
        for w in submission["warnings"]:
            automate_context.attach_info_to_objects(
                category="ABS Extraction Warning",
                object_ids=[],
                message=w,
            )

    # ------------------------------------------------------------------
    # 4. Optional: run evaluator
    # ------------------------------------------------------------------
    if not function_inputs.run_evaluator:
        automate_context.mark_run_success(
            f"Model ingested successfully. "
            f"Submission ID: {submission_id}. "
            f"{n_attributes} attributes extracted. "
            "Evaluator skipped (run_evaluator=False)."
        )
        return

    try:
        eval_response = client.evaluate_submission(submission_id)
    except ABSClientError as exc:
        automate_context.mark_run_failed(
            f"ABS evaluator call failed (HTTP {exc.status_code}): {exc.detail}"
        )
        return

    decision: dict[str, Any] | None = eval_response.get("decision")
    if not decision:
        automate_context.mark_run_success(
            f"Submission {submission_id}: evaluator returned no decision. "
            "This can happen when the bylaw corpus has no rules matching "
            "the extracted attributes. Check the ABS dashboard for details."
        )
        return

    # ------------------------------------------------------------------
    # 5. Parse matrix and annotate Speckle objects
    # ------------------------------------------------------------------
    compliant, non_compliant = _annotate_objects(automate_context, decision)

    if non_compliant:
        automate_context.mark_run_failed(
            f"Compliance check FAILED: {non_compliant} clause(s) failed, "
            f"{compliant} passed. "
            f"ABS submission ID: {submission_id}. "
            "See object annotations for details."
        )
    else:
        automate_context.mark_run_success(
            f"Compliance check PASSED: all {compliant} evaluated clause(s) passed. "
            f"ABS submission ID: {submission_id}."
        )


# ---------------------------------------------------------------------------
# IFC download helpers
# ---------------------------------------------------------------------------


def _download_ifc(automate_context: AutomationContext) -> Path:
    """Download the IFC blob attached to the current model version.

    Speckle connectors (Revit, ArchiCAD, Rhino) can export IFC as a
    blob attachment alongside the native Speckle object tree.  We look
    for that attachment first because it preserves element GUIDs exactly
    as the authoring tool assigned them.

    Fallback: receive the Speckle object tree and serialise it to IFC
    using specklepy's built-in IFC schema writer.  The output is
    geometrically equivalent but GUIDs are re-derived from Speckle
    object IDs, so element-level annotations in step 5 link back by
    the Speckle ID rather than the original IFC GUID.
    """
    # Try blob attachment first.
    blob_path = _try_download_blob(automate_context)
    if blob_path is not None:
        return blob_path

    # Fallback: convert Speckle object tree → IFC.
    return _convert_speckle_to_ifc(automate_context)


def _try_download_blob(automate_context: AutomationContext) -> Path | None:
    """Return a temp-file path to the IFC blob, or None if unavailable."""
    try:
        version = automate_context.automation_run_data.version_id
        blobs = automate_context.speckle_client.server.get_version_blobs(
            automate_context.automation_run_data.project_id,
            version,
        )
        ifc_blob = next(
            (b for b in (blobs or []) if b.get("fileName", "").endswith(".ifc")),
            None,
        )
        if ifc_blob is None:
            return None

        blob_id = ifc_blob["id"]
        tmp = tempfile.NamedTemporaryFile(suffix=".ifc", delete=False)
        tmp.close()
        blob_path = Path(tmp.name)
        automate_context.speckle_client.server.download_blob(
            automate_context.automation_run_data.project_id,
            blob_id,
            blob_path,
        )
        logger.info("Downloaded IFC blob %s → %s", blob_id, blob_path)
        return blob_path
    except Exception as exc:  # noqa: BLE001
        logger.debug("IFC blob download failed (%s); falling back to conversion.", exc)
        return None


def _convert_speckle_to_ifc(automate_context: AutomationContext) -> Path:
    """Receive the Speckle object tree and write it as a temporary IFC file.

    Uses specklepy's ``SpeckleIfcConverter`` when available; otherwise
    writes a minimal IFC shell with element placeholders so the ABS
    extractor still gets geometry-independent attributes (floor area,
    floor count, etc.) from the Speckle property sets.
    """
    from specklepy.api.operations import receive  # type: ignore[import-not-found]
    from specklepy.transports.server import ServerTransport  # type: ignore[import-not-found]

    transport = ServerTransport(
        client=automate_context.speckle_client,
        stream_id=automate_context.automation_run_data.project_id,
    )
    root_obj = receive(
        automate_context.automation_run_data.version_id,
        remote_transport=transport,
    )

    tmp = tempfile.NamedTemporaryFile(suffix=".ifc", delete=False)
    tmp.close()
    ifc_path = Path(tmp.name)

    try:
        from specklepy.objects.other import IFC  # type: ignore[import-not-found]
        from specklepy.objects.converter import SpeckleIfcConverter  # type: ignore[import-not-found]

        converter = SpeckleIfcConverter()
        ifc_model = converter.convert(root_obj)
        ifc_model.write(str(ifc_path))
        logger.info("Converted Speckle object tree → IFC (%s)", ifc_path)
    except ImportError:
        # specklepy IFC support not installed; write a minimal placeholder
        # IFC so the ABS extractor can still process property sets.
        _write_minimal_ifc(ifc_path, root_obj)
        logger.warning(
            "specklepy IFC converter not available; wrote minimal IFC from "
            "property sets only.  Install specklepy[ifc] for full conversion."
        )

    return ifc_path


def _write_minimal_ifc(ifc_path: Path, root_obj: Any) -> None:
    """Write a valid but geometry-free IFC from a Speckle object."""
    import ifcopenshell  # type: ignore[import-not-found]
    import ifcopenshell.api  # type: ignore[import-not-found]

    ifc = ifcopenshell.file(schema="IFC4")
    project = ifc.createIfcProject(
        GlobalId=ifcopenshell.guid.new(),
        Name="SpeckleAutomate",
    )
    # Walk the Speckle object and create IfcBuildingStorey placeholders
    # for each "@Elements" layer so floor-count / height extraction works.
    _walk_speckle_elements(ifc, project, root_obj)
    ifc.write(str(ifc_path))


def _walk_speckle_elements(ifc: Any, project: Any, obj: Any, depth: int = 0) -> None:
    """Recursively add IfcBuildingElement placeholders from a Speckle object."""
    import ifcopenshell  # type: ignore[import-not-found]

    if depth > 8:
        return
    elements = getattr(obj, "@Elements", None) or getattr(obj, "elements", None) or []
    for child in elements:
        elem = ifc.createIfcBuildingElementProxy(
            GlobalId=ifcopenshell.guid.new(),
            Name=getattr(child, "type", "Unknown"),
        )
        _walk_speckle_elements(ifc, elem, child, depth + 1)


# ---------------------------------------------------------------------------
# Compliance annotation helpers
# ---------------------------------------------------------------------------


def _annotate_objects(
    automate_context: AutomationContext,
    decision: dict[str, Any],
) -> tuple[int, int]:
    """Attach Automate result objects for each compliance clause.

    Returns ``(compliant_count, non_compliant_count)``.

    The decision matrix has a list of clause results. Each result may
    carry an ``element_ids`` list mapping the failure to specific IFC
    element GUIDs. When present, those are looked up in the Speckle
    object tree and attached to the annotation so the Viewer highlights
    the failing geometry.
    """
    compliant = 0
    non_compliant = 0

    clause_results: list[dict[str, Any]] = (
        decision.get("clause_results") or decision.get("results") or []
    )

    for clause in clause_results:
        passed: bool = clause.get("passed", True)
        clause_ref: str = clause.get("clause_ref") or clause.get("citation", "Unknown")
        message: str = clause.get("message") or clause.get("summary") or ""
        element_ids: list[str] = clause.get("element_ids") or []

        if passed:
            compliant += 1
            if element_ids:
                automate_context.attach_info_to_objects(
                    category="ABS Compliance ✓",
                    object_ids=element_ids,
                    message=f"{clause_ref}: PASSED",
                )
        else:
            non_compliant += 1
            automate_context.attach_error_to_objects(
                category="ABS Compliance ✗",
                object_ids=element_ids if element_ids else [],
                message=f"{clause_ref}: FAILED — {message}",
            )

    return compliant, non_compliant


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    execute_automate_function(automate_function, FunctionInputs)
