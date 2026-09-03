"""Phase-1 compliance schema: attribute tags + submission/parcel ledger.

Adds the structural primitives every later phase depends on:

* ``source_fragment.attribute_tags`` — JSONB list of attribute IDs
  from the Phase-1 taxonomy (e.g. ``["front_setback_m"]``). Populated
  by the semantic-enrichment pass; the evaluator uses it to scope
  retrieval per attribute.
* ``parcel`` — cadastral parcels, unique on
  ``(jurisdiction, parcel_identifier)``.
* ``external_dataset_feature.parcel_id`` — nullable FK linking
  Halifax parcel features to ``parcel`` rows. Backfilled by the
  one-time ``scripts/backfill_parcels.py`` script; new dataset rows
  populate it inline.
* ``submission`` — one row per development proposal. Carries a
  status enum, a source-type enum, and pointers to the parcel and
  any source artifact.
* ``submission_attribute`` — submission's attribute bag, unique on
  ``(submission_id, attribute_key)``.
* ``approval_decision`` — append-only evaluator-output ledger,
  pinned to ``evaluator_version``.

Indexes added:

* GIN on ``source_fragment.attribute_tags`` (postgres only) so the
  evaluator's per-attribute filter is O(matching-rows) instead of
  O(all-rows).
* B-tree on ``submission.status`` and ``submission_attribute.attribute_key``
  for the obvious filter paths.
* B-tree on ``parcel(jurisdiction, parcel_identifier)`` via the
  unique constraint; an extra index on ``zone_code`` for the
  zone-lookup convenience field (both the column and this index were
  dropped again by ``0026_drop_parcel_zone_code`` — the field turned
  out to be write-only).

SQLite safety: postgres-specific bits (GIN index, JSONB-array
``server_default``) are dialect-gated. Without that the test suite
(sqlite) can't apply the migration.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014_compliance_schema"
down_revision = "0013_terms_acceptance"
branch_labels = None
depends_on = None


def _json_type():
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    json_type = _json_type()

    # ----- source_fragment.attribute_tags ---------------------------------
    # Default '[]' on both dialects so existing rows acquire a usable
    # empty list. We pass the JSON literal as a server_default; postgres
    # casts it through JSONB on insert, sqlite stores the literal text.
    op.add_column(
        "source_fragment",
        sa.Column(
            "attribute_tags",
            json_type,
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    if is_postgres:
        # GIN over the JSONB array makes the evaluator's per-attribute
        # filter (`attribute_tags ?| ARRAY['front_setback_m']`) index-
        # backed. Without it the evaluator regresses to a sequential
        # scan over every fragment in every linked bylaw.
        op.execute(
            sa.text(
                "CREATE INDEX IF NOT EXISTS "
                "ix_source_fragment_attribute_tags_gin "
                "ON source_fragment USING GIN (attribute_tags)"
            )
        )

    # ----- parcel ---------------------------------------------------------
    op.create_table(
        "parcel",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("jurisdiction", sa.String(length=128), nullable=False),
        sa.Column("parcel_identifier", sa.String(length=128), nullable=False),
        sa.Column("geometry_geojson", json_type, nullable=True),
        sa.Column("centroid_geojson", json_type, nullable=True),
        sa.Column("area_m2", sa.Numeric(14, 2), nullable=True),
        sa.Column("zone_code", sa.String(length=64), nullable=True),
        sa.Column(
            "metadata_json",
            json_type,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()") if is_postgres else sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()") if is_postgres else sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "jurisdiction", "parcel_identifier", name="uq_parcel_jurisdiction_identifier"
        ),
    )
    op.create_index("ix_parcel_jurisdiction", "parcel", ["jurisdiction"])
    op.create_index("ix_parcel_parcel_identifier", "parcel", ["parcel_identifier"])
    op.create_index("ix_parcel_zone_code", "parcel", ["zone_code"])

    # ----- external_dataset_feature.parcel_id -----------------------------
    # On postgres we can ALTER ... ADD COLUMN ... REFERENCES in two
    # steps. On sqlite, ``batch_alter_table`` would normally recreate
    # the table to attach the FK — but the existing FK on this table
    # is unnamed, which trips batch mode's reflection ("constraint
    # must have a name"). Tests don't depend on FK enforcement; they
    # just need the column to round-trip. Skip the FK on sqlite and
    # add only the column.
    op.add_column(
        "external_dataset_feature",
        sa.Column("parcel_id", sa.Integer(), nullable=True),
    )
    if is_postgres:
        op.create_foreign_key(
            "fk_external_dataset_feature_parcel_id",
            "external_dataset_feature",
            "parcel",
            ["parcel_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        "ix_external_dataset_feature_parcel_id",
        "external_dataset_feature",
        ["parcel_id"],
    )

    # ----- submission -----------------------------------------------------
    # Enum types are created inline by the first column reference. We
    # don't pre-create with ``.create(checkfirst=True)`` and then use
    # ``create_type=False`` on the column — SQLAlchemy 2.0.49's
    # ``_on_table_create`` ignores ``create_type=False`` on inline
    # enums that aren't bound to ``MetaData``, which produces a
    # ``DuplicateObject`` error against real Postgres (caught by
    # ABS-46's ``make e2e`` run). Letting the first inline reference
    # do the CREATE TYPE matches the pattern migration 0001 used for
    # the layer-1 enums and works on both sqlite and Postgres.
    op.create_table(
        "submission",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "parcel_id",
            sa.Integer(),
            sa.ForeignKey("parcel.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("submitter_id", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "evaluating",
                "evaluated",
                "archived",
                name="submission_status",
            ),
            nullable=False,
            server_default="draft",
        ),
        sa.Column(
            "source_type",
            sa.Enum(
                "manual",
                "ifc",
                "rvt_aps",
                "pdf",
                "speckle",
                name="submission_source_type",
            ),
            nullable=False,
            server_default="manual",
        ),
        sa.Column("source_artifact_path", sa.Text(), nullable=True),
        sa.Column(
            "metadata_json",
            json_type,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()") if is_postgres else sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()") if is_postgres else sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_submission_parcel_id", "submission", ["parcel_id"])
    op.create_index("ix_submission_status", "submission", ["status"])

    # ----- submission_attribute -------------------------------------------
    op.create_table(
        "submission_attribute",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "submission_id",
            sa.Integer(),
            sa.ForeignKey("submission.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attribute_key", sa.String(length=128), nullable=False),
        sa.Column(
            "value_json",
            json_type,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("1.0")),
        sa.Column(
            "source",
            sa.Enum(
                "manual",
                "extracted",
                "derived",
                "override",
                name="submission_attribute_source",
            ),
            nullable=False,
            server_default="manual",
        ),
        sa.Column("evidence_json", json_type, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()") if is_postgres else sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()") if is_postgres else sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "submission_id", "attribute_key", name="uq_submission_attribute_key"
        ),
    )
    op.create_index(
        "ix_submission_attribute_submission_id",
        "submission_attribute",
        ["submission_id"],
    )
    op.create_index(
        "ix_submission_attribute_attribute_key",
        "submission_attribute",
        ["attribute_key"],
    )

    # ----- approval_decision ----------------------------------------------
    op.create_table(
        "approval_decision",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "submission_id",
            sa.Integer(),
            sa.ForeignKey("submission.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("evaluator_version", sa.String(length=64), nullable=False),
        sa.Column(
            "decision_summary_json",
            json_type,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()") if is_postgres else sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_approval_decision_submission_id",
        "approval_decision",
        ["submission_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    op.drop_index("ix_approval_decision_submission_id", table_name="approval_decision")
    op.drop_table("approval_decision")

    op.drop_index(
        "ix_submission_attribute_attribute_key", table_name="submission_attribute"
    )
    op.drop_index(
        "ix_submission_attribute_submission_id", table_name="submission_attribute"
    )
    op.drop_table("submission_attribute")

    op.drop_index("ix_submission_status", table_name="submission")
    op.drop_index("ix_submission_parcel_id", table_name="submission")
    op.drop_table("submission")

    op.drop_index(
        "ix_external_dataset_feature_parcel_id",
        table_name="external_dataset_feature",
    )
    if is_postgres:
        op.drop_constraint(
            "fk_external_dataset_feature_parcel_id",
            "external_dataset_feature",
            type_="foreignkey",
        )
    op.drop_column("external_dataset_feature", "parcel_id")

    op.drop_index("ix_parcel_zone_code", table_name="parcel")
    op.drop_index("ix_parcel_parcel_identifier", table_name="parcel")
    op.drop_index("ix_parcel_jurisdiction", table_name="parcel")
    op.drop_table("parcel")

    if is_postgres:
        op.execute(
            sa.text("DROP INDEX IF EXISTS ix_source_fragment_attribute_tags_gin")
        )
    op.drop_column("source_fragment", "attribute_tags")

    if is_postgres:
        sa.Enum(name="submission_attribute_source").drop(bind, checkfirst=True)
        sa.Enum(name="submission_source_type").drop(bind, checkfirst=True)
        sa.Enum(name="submission_status").drop(bind, checkfirst=True)
