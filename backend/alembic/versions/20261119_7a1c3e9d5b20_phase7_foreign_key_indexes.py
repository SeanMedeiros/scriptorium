"""phase7: indexes on foreign-key columns used by joins, cascades and retention

Found by the Phase 7 foreign-key index audit (docs/performance.md): PostgreSQL does not index
referencing columns automatically, so lookups such as "which pages use this image", the retention
run's reference checks, deletes that cascade through segmentations/recognitions, and the
line-history queries of the transcription editor scanned whole tables. Columns referencing users
are left unindexed on purpose (users are deactivated, never deleted).

Revision ID: 7a1c3e9d5b20
Revises: f233e02e5f64
Create Date: 2026-11-19
"""
from typing import Sequence, Union

from alembic import op

revision: str = "7a1c3e9d5b20"
down_revision: Union[str, Sequence[str], None] = "f233e02e5f64"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEXES = [
    ("annotations", "line_id"),
    ("comparisons", "segmentation_id"),
    ("documents", "source_asset_id"),
    ("evaluation_results", "model_version_id"),
    ("ground_truth_lines", "line_id"),
    ("ground_truth_pages", "segmentation_id"),
    ("line_versions", "line_id"),
    ("line_versions", "origin_line_recognition_id"),
    ("line_versions", "previous_version_id"),
    ("line_versions", "restored_from_id"),
    ("pages", "image_asset_id"),
    ("recognition_jobs", "input_segmentation_id"),
    ("recognition_jobs", "output_recognition_id"),
    ("recognition_jobs", "output_segmentation_id"),
    ("recognition_jobs", "recognition_model_version_id"),
    ("recognition_jobs", "retry_of_id"),
    ("recognition_jobs", "segmentation_model_version_id"),
    ("recognitions", "input_asset_id"),
    ("recognitions", "job_id"),
    ("recognitions", "model_version_id"),
    ("segmentations", "input_asset_id"),
    ("segmentations", "job_id"),
    ("segmentations", "model_version_id"),
    ("segmentations", "parent_segmentation_id"),
    ("transcriptions", "base_recognition_id"),
    ("transcriptions", "comparison_id"),
    ("transcriptions", "segmentation_id"),
    ("user_sessions", "replaced_by_id"),
]


def _name(table: str, col: str) -> str:
    return f"ix_{table}_{col}"[:63]


def upgrade() -> None:
    for table, col in INDEXES:
        op.create_index(_name(table, col), table, [col])


def downgrade() -> None:
    for table, col in reversed(INDEXES):
        op.drop_index(_name(table, col), table_name=table)
