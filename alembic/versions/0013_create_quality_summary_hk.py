"""Create Hong Kong market quality summary view.

Revision ID: 0013_create_quality_summary_hk
Revises: 0012_create_quality_issues
Create Date: 2026-08-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013_create_quality_summary_hk"
down_revision: str | Sequence[str] | None = "0012_create_quality_issues"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the Hong Kong market quality summary view."""
    op.execute(
        """
        CREATE VIEW v_quality_summary_hk AS
        SELECT
            check_name,
            severity,
            COUNT(*) AS issue_count,
            COUNT(*) FILTER (WHERE resolved) AS resolved_count,
            COUNT(*) FILTER (WHERE NOT resolved) AS unresolved_count
        FROM quality_issues
        WHERE market = 'HK'
        GROUP BY check_name, severity
        """
    )


def downgrade() -> None:
    """Drop the Hong Kong market quality summary view."""
    op.execute("DROP VIEW v_quality_summary_hk")
