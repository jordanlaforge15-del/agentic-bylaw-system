# Speckle Automate Integration

ABS ships a [Speckle Automate](https://automate.speckle.dev/) function that lets designers run an ABS compliance check directly from Speckle — at the moment the model is published, before it reaches a code-review or permit stage.

## How it works

1. **Model publish** triggers the Automate function (or the designer runs it manually).
2. The function downloads the model version's IFC blob (or converts the Speckle object tree to IFC on the fly).
3. The IFC is POSTed to the ABS `POST /v1/integrations/submissions` endpoint along with the configured parcel identifier.
4. ABS extracts building attributes (height, floor area, setbacks, etc.) from the IFC.
5. If `run_evaluator` is enabled (default), the ABS bylaw evaluator is called immediately.
6. The compliance matrix is posted back to Speckle as Automate function output. Non-compliant clauses are attached as error annotations on the failing model elements so they highlight in the Speckle Viewer.

## Prerequisites

* A Speckle workspace (Cloud or self-hosted ≥ Speckle Server v2.14).
* An ABS account. The account owner registers the workspace with ABS and receives an API key (see [API key setup](#api-key-setup) below).
* The parcel must already exist in ABS (parcel PID or internal ID). For the Halifax pilot use the PID from the HRM parcel data import.

## Function inputs

| Input | Type | Required | Description |
|---|---|---|---|
| `abs_api_url` | string | ✓ | Base URL of the ABS advisor API (e.g. `https://api.yourdomain.com`). |
| `abs_api_key` | string | ✓ | API key issued by ABS for this workspace. Keep secret. |
| `parcel_id` | integer | one of | Numeric ABS parcel ID for the site. |
| `parcel_address` | string | one of | Civic address or parcel PID. Used when `parcel_id` is unavailable. |
| `run_evaluator` | boolean | — | Default `true`. When `false`, only ingests the model (no compliance check). |

## API key setup

API keys are issued per ABS account.  They authenticate the Speckle Automate function as the account owner — submissions uploaded via API key appear in the owner's ABS dashboard.

### Development / pilot

1. Create an `advisor_user` row (normal sign-up flow).
2. Use the management script to issue a key:

   ```bash
   python scripts/issue_api_key.py --email your@email.com --name "Speckle Automate"
   ```

   The script prints the raw key **once**. Copy it immediately — it cannot be retrieved later.

3. Paste the key into the Speckle Automate function's `abs_api_key` input.

### Revoking a key

```bash
python scripts/issue_api_key.py --revoke --key-id <id>
```

Revoked keys are rejected with HTTP 401 on the next request.

## Integration API endpoints

These endpoints are distinct from the browser-facing `/v1/submissions` routes.  They accept `X-ABS-API-Key` instead of a Clerk JWT.

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/integrations/submissions` | Upload IFC + parcel → ingest → return submission with extracted attributes. |
| `POST` | `/v1/integrations/submissions/{id}/evaluate` | Run the bylaw evaluator on the submission. |
| `GET` | `/v1/integrations/submissions/{id}/matrix` | Fetch the latest compliance matrix. |

### Upload request

```
POST /v1/integrations/submissions
X-ABS-API-Key: <your_key>
Content-Type: multipart/form-data

file=<ifc_file>
parcel_address=<PID or address>   # or parcel_id=<int>
```

### Upload response (200 OK)

```json
{
  "id": 42,
  "status": "draft",
  "source_type": "ifc",
  "attributes": [
    { "attribute_key": "building_height_m", "value": 12.5, "unit": "m", ... },
    ...
  ],
  "warnings": []
}
```

### Evaluate response (200 OK)

```json
{
  "submission_id": 42,
  "decision": { "overall_status": "compliant", "clause_results": [...] }
}
```

## Function location

Source: `integrations/speckle-automate/`

```
integrations/speckle-automate/
  main.py        # Automate function entry point
  inputs.py      # FunctionInputs (Pydantic model)
  abs_client.py  # Thin HTTP client for the ABS integration API
  pyproject.toml
  Dockerfile
  tests/
    test_abs_client.py
    test_main.py
```

## Running the function locally

```bash
cd integrations/speckle-automate
pip install -e ".[dev]"
pytest tests/
```

## Integration tests against a real Speckle workspace

The Automate SDK provides a test harness via `SpeckleAutomateFixture`.  To run integration tests against a real workspace:

1. Set environment variables:
   ```bash
   export SPECKLE_TOKEN=<personal-access-token>
   export SPECKLE_SERVER_URL=https://app.speckle.systems
   export ABS_API_URL=http://localhost:8001
   export ABS_API_KEY=<dev-key>
   ```

2. Run the integration suite:
   ```bash
   pytest tests/ -m integration
   ```

   Integration tests are marked `@pytest.mark.integration` and skipped by default.

## Deployment

The function is deployed as a Docker container to Speckle Automate via the Speckle CLI:

```bash
speckle-automate upload --function abs-compliance-check \
  --docker-image-tag ghcr.io/your-org/abs-speckle-automate:latest
```

Tagging and publishing the image is handled by the same CI pipeline that builds the main ABS images.  See `docs/DEPLOYMENT.md` for the full release flow.
