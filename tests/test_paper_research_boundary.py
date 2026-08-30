"""Research boundary review tests (MVP 4 / SP 4.83).

Covers the pre-release boundary review: no broker orders / credentials, no
return promise, and no upgrade without a passed difference verification.
"""

import unittest
from datetime import date

from harbor.core.paper_research_boundary import (
    ResearchBoundaryError,
    ResearchBoundaryReview,
    review_research_boundary,
)

_AS_OF = date(2026, 1, 15)


class ResearchBoundaryTests(unittest.TestCase):
    """The pre-release boundary review (SP 4.83)."""

    def test_passing_review(self) -> None:
        review = review_research_boundary(
            paper_run_id="paper-1",
            reviewed_at=_AS_OF,
            reviewer="ops-owner",
            no_broker_orders=True,
            no_return_promise=True,
            admission_passed=True,
        )
        self.assertIsInstance(review, ResearchBoundaryReview)
        self.assertTrue(review.passed)

    def test_broker_orders_block(self) -> None:
        review = review_research_boundary(
            paper_run_id="paper-1",
            reviewed_at=_AS_OF,
            reviewer="ops-owner",
            no_broker_orders=False,
            no_return_promise=True,
            admission_passed=True,
        )
        self.assertFalse(review.passed)

    def test_return_promise_blocks(self) -> None:
        review = review_research_boundary(
            paper_run_id="paper-1",
            reviewed_at=_AS_OF,
            reviewer="ops-owner",
            no_broker_orders=True,
            no_return_promise=False,
            admission_passed=True,
        )
        self.assertFalse(review.passed)

    def test_unpassed_admission_blocks(self) -> None:
        review = review_research_boundary(
            paper_run_id="paper-1",
            reviewed_at=_AS_OF,
            reviewer="ops-owner",
            no_broker_orders=True,
            no_return_promise=True,
            admission_passed=False,
        )
        self.assertFalse(review.passed)

    def test_readable(self) -> None:
        review = review_research_boundary(
            paper_run_id="paper-1",
            reviewed_at=_AS_OF,
            reviewer="ops-owner",
            no_broker_orders=True,
            no_return_promise=True,
            admission_passed=True,
            notes="paper loop only",
        )
        text = review.readable()
        self.assertIn("PASSED", text)
        self.assertIn("no broker orders/credentials: yes", text)
        self.assertIn("notes: paper loop only", text)

    def test_notes_default(self) -> None:
        review = review_research_boundary(
            paper_run_id="paper-1",
            reviewed_at=_AS_OF,
            reviewer="ops-owner",
            no_broker_orders=True,
            no_return_promise=True,
            admission_passed=True,
        )
        self.assertEqual(review.notes, "")

    def test_required_fields(self) -> None:
        with self.assertRaises(ResearchBoundaryError):
            review_research_boundary(
                paper_run_id="",
                reviewed_at=_AS_OF,
                reviewer="ops-owner",
                no_broker_orders=True,
                no_return_promise=True,
                admission_passed=True,
            )
        with self.assertRaises(ResearchBoundaryError):
            review_research_boundary(
                paper_run_id="paper-1",
                reviewed_at=_AS_OF,
                reviewer="",
                no_broker_orders=True,
                no_return_promise=True,
                admission_passed=True,
            )
