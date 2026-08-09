"""Training/validation result reconciliation tests (MVP 3 / SP 3.23).

Verifies that every trial's validation run is reconciled with the MVP 2
ledger (SP 2.63), net-value series and attribution (SP 2.57), and that any
trial which does not close is marked failed — carrying no metric — so it
cannot participate in SP 3.21 ranking.
"""

import unittest
from datetime import date

from harbor.core.attribution import AttributionReport, DailyAttribution
from harbor.core.backtest_domain import Currency, NetValue
from harbor.core.ledger_reconciliation import (
    DailyLedgerReconciliation,
    LedgerReconciliationReport,
)
from harbor.core.trial_reconciliation import (
    ReconciledTrials,
    ReconciliationCheck,
    TrialReconciliation,
    TrialReconciliationError,
    TrialReconciliationSummary,
    build_reconciliation_summary,
    mark_trial_failed,
    net_values_reconcile,
    reconcile_trial,
    reconcile_trials,
    reconciliation_fingerprint,
    reconciliation_json,
)
from harbor.core.validation_domain import Parameter, ParameterTrial

_TRAIN_START = date(2019, 1, 1)
_TRAIN_END = date(2020, 12, 31)
_VALIDATION_START = date(2021, 1, 1)
_VALIDATION_END = date(2022, 12, 31)


def _trial(**overrides: object) -> ParameterTrial:
    """Return a valid parameter trial with overridable fields."""
    fields: dict[str, object] = {
        "trial_id": "trial-1",
        "parameters": (Parameter(name="cash_weight", value=0.05),),
        "dataset_fingerprint": "fp-1",
        "train_start": _TRAIN_START,
        "train_end": _TRAIN_END,
        "validation_start": _VALIDATION_START,
        "validation_end": _VALIDATION_END,
        "seed": 42,
        "code_version": "1.0.0",
        "metric": 0.12,
    }
    fields.update(overrides)
    return ParameterTrial(**fields)  # type: ignore[arg-type]


def _reconciliation(**overrides: object) -> TrialReconciliation:
    """Return a valid trial reconciliation with overridable fields."""
    fields: dict[str, object] = {
        "trial_id": "trial-1",
        "ledger_reconciled": True,
        "net_value_reconciled": True,
        "attribution_reconciled": True,
    }
    fields.update(overrides)
    return TrialReconciliation(**fields)  # type: ignore[arg-type]


def _net_values(reconciled: bool) -> tuple[NetValue, ...]:
    """Return a net-value series that reconciles or not."""
    if not reconciled:
        return ()
    return (
        NetValue(
            as_of_date=date(2021, 1, 1),
            currency=Currency.HKD,
            cash=40.0,
            securities_value=60.0,
            fees_paid=0.5,
        ),
        NetValue(
            as_of_date=date(2021, 1, 2),
            currency=Currency.HKD,
            cash=41.0,
            securities_value=60.0,
            fees_paid=1.0,
        ),
    )


def _ledger_report(reconciled: bool) -> LedgerReconciliationReport:
    """Return a real MVP 2 ledger report that reconciles or not."""
    days: tuple[DailyLedgerReconciliation, ...] = ()
    if not reconciled:
        days = (
            DailyLedgerReconciliation(
                as_of=date(2021, 1, 1),
                total_value=100.0,
                cash=40.0,
                securities_value=60.0,
                assets_balance=1.0,
                assets_balanced=False,
                net_value_change=1.0,
                cash_change=1.0,
                expected_cash_change=1.0,
                cash_gap=0.0,
                cash_closes=True,
                fees_delta=0.0,
                fees_expected=0.0,
                fees_close=True,
                dividends=0.0,
                corporate_actions=0.0,
                fx_pnl_delta=0.0,
            ),
        )
    return LedgerReconciliationReport(
        base_currency=Currency.HKD,
        initial_capital=1_000_000.0,
        tolerance=1e-6,
        days=days,
    )


def _attribution_report(reconciled: bool) -> AttributionReport:
    """Return a real MVP 2 attribution report that reconciles or not."""
    days: tuple[DailyAttribution, ...] = ()
    if not reconciled:
        days = (
            DailyAttribution(
                as_of=date(2021, 1, 1),
                previous_value=100.0,
                net_value=101.0,
                net_value_change=1.0,
                price_return=1.0,
                dividends=0.0,
                corporate_actions=0.0,
                trading_costs=0.0,
                fx_impact=0.0,
                gap=0.5,
            ),
        )
    return AttributionReport(
        base_currency=Currency.HKD,
        initial_capital=1_000_000.0,
        tolerance=1e-6,
        days=days,
    )


class ReconciliationCheckTests(unittest.TestCase):
    """Validates the :class:`ReconciliationCheck` enum."""

    def test_values(self) -> None:
        self.assertEqual(ReconciliationCheck.LEDGER, "ledger")
        self.assertEqual(ReconciliationCheck.NET_VALUE, "net_value")
        self.assertEqual(ReconciliationCheck.ATTRIBUTION, "attribution")


class TrialReconciliationTests(unittest.TestCase):
    """Validates the :class:`TrialReconciliation` invariants."""

    def test_valid_reconciled(self) -> None:
        entry = _reconciliation()
        self.assertTrue(entry.reconciled)
        self.assertEqual(entry.failures, ())

    def test_empty_trial_id_rejected(self) -> None:
        with self.assertRaises(TrialReconciliationError):
            _reconciliation(trial_id="")

    def test_failures_list_non_closing_checks(self) -> None:
        entry = _reconciliation(attribution_reconciled=False)
        self.assertFalse(entry.reconciled)
        self.assertEqual(entry.failures, (ReconciliationCheck.ATTRIBUTION,))

    def test_failures_all_when_nothing_closes(self) -> None:
        entry = _reconciliation(
            ledger_reconciled=False,
            net_value_reconciled=False,
            attribution_reconciled=False,
        )
        self.assertEqual(
            entry.failures,
            (
                ReconciliationCheck.LEDGER,
                ReconciliationCheck.NET_VALUE,
                ReconciliationCheck.ATTRIBUTION,
            ),
        )

    def test_readable(self) -> None:
        self.assertIn("trial trial-1", _reconciliation().readable())
        self.assertIn("reconciled", _reconciliation().readable())


class NetValuesReconcileTests(unittest.TestCase):
    """Verifies :func:`net_values_reconcile` (净值对账)."""

    def test_empty_series_does_not_reconcile(self) -> None:
        self.assertFalse(net_values_reconcile(()))

    def test_ascending_series_reconciles(self) -> None:
        self.assertTrue(net_values_reconcile(_net_values(True)))

    def test_reversed_dates_do_not_reconcile(self) -> None:
        series = (
            NetValue(
                as_of_date=date(2021, 1, 2),
                currency=Currency.HKD,
                cash=41.0,
                securities_value=60.0,
            ),
            NetValue(
                as_of_date=date(2021, 1, 1),
                currency=Currency.HKD,
                cash=40.0,
                securities_value=60.0,
            ),
        )
        self.assertFalse(net_values_reconcile(series))

    def test_duplicate_date_does_not_reconcile(self) -> None:
        series = (
            NetValue(
                as_of_date=date(2021, 1, 1),
                currency=Currency.HKD,
                cash=40.0,
                securities_value=60.0,
            ),
            NetValue(
                as_of_date=date(2021, 1, 1),
                currency=Currency.HKD,
                cash=41.0,
                securities_value=60.0,
            ),
        )
        self.assertFalse(net_values_reconcile(series))


class ReconcileTrialTests(unittest.TestCase):
    """Verifies :func:`reconcile_trial` uses the MVP 2 reports."""

    def test_all_reports_reconcile(self) -> None:
        entry = reconcile_trial(
            "trial-1",
            ledger=_ledger_report(True),
            net_values=_net_values(True),
            attribution=_attribution_report(True),
        )
        self.assertTrue(entry.reconciled)
        self.assertEqual(entry.failures, ())

    def test_missing_ledger_report_is_not_reconciled(self) -> None:
        entry = reconcile_trial(
            "trial-1",
            ledger=None,
            net_values=_net_values(True),
            attribution=_attribution_report(True),
        )
        self.assertFalse(entry.reconciled)
        self.assertEqual(entry.failures, (ReconciliationCheck.LEDGER,))

    def test_missing_attribution_report_is_not_reconciled(self) -> None:
        entry = reconcile_trial(
            "trial-1",
            ledger=_ledger_report(True),
            net_values=_net_values(True),
            attribution=None,
        )
        self.assertFalse(entry.reconciled)
        self.assertEqual(entry.failures, (ReconciliationCheck.ATTRIBUTION,))

    def test_empty_net_values_is_not_reconciled(self) -> None:
        entry = reconcile_trial(
            "trial-1",
            ledger=_ledger_report(True),
            net_values=(),
            attribution=_attribution_report(True),
        )
        self.assertFalse(entry.reconciled)
        self.assertEqual(entry.failures, (ReconciliationCheck.NET_VALUE,))

    def test_failed_ledger_report_is_not_reconciled(self) -> None:
        entry = reconcile_trial(
            "trial-1",
            ledger=_ledger_report(False),
            net_values=_net_values(True),
            attribution=_attribution_report(True),
        )
        self.assertFalse(entry.reconciled)

    def test_failed_attribution_report_is_not_reconciled(self) -> None:
        entry = reconcile_trial(
            "trial-1",
            ledger=_ledger_report(True),
            net_values=_net_values(True),
            attribution=_attribution_report(False),
        )
        self.assertFalse(entry.reconciled)

    def test_empty_trial_id_rejected(self) -> None:
        with self.assertRaises(TrialReconciliationError):
            reconcile_trial("")


class MarkTrialFailedTests(unittest.TestCase):
    """Verifies :func:`mark_trial_failed` drops a trial out of ranking."""

    def test_marks_metric_trial_failed(self) -> None:
        trial = _trial(trial_id="trial-1", metric=0.12)
        failed = mark_trial_failed(trial, reason="reconciliation failed: ledger")
        self.assertIsNone(failed.metric)
        self.assertEqual(failed.failed_reason, "reconciliation failed: ledger")
        self.assertEqual(failed.trial_id, trial.trial_id)
        self.assertEqual(failed.dataset_fingerprint, trial.dataset_fingerprint)
        self.assertEqual(failed.seed, trial.seed)
        self.assertEqual(failed.parameters, trial.parameters)

    def test_already_failed_unchanged(self) -> None:
        trial = _trial(trial_id="trial-1", metric=None, failed_reason="boom")
        failed = mark_trial_failed(trial, reason="reconciliation failed: ledger")
        self.assertIs(failed, trial)

    def test_empty_reason_rejected(self) -> None:
        with self.assertRaises(TrialReconciliationError):
            mark_trial_failed(_trial(), reason="")


class ReconcileTrialsTests(unittest.TestCase):
    """Verifies :func:`reconcile_trials` marks non-closing trials failed."""

    def test_all_reconciled_keeps_trials(self) -> None:
        trials = [_trial(trial_id="trial-1"), _trial(trial_id="trial-2")]
        reconciliations = {
            "trial-1": _reconciliation(trial_id="trial-1"),
            "trial-2": _reconciliation(trial_id="trial-2"),
        }
        result = reconcile_trials(trials, reconciliations=reconciliations)
        self.assertEqual(len(result.trials), 2)
        self.assertEqual(result.failed_trials, ())
        self.assertTrue(result.summary.all_reconciled)
        self.assertEqual(result.trials[0].metric, 0.12)
        self.assertEqual(result.trials[1].metric, 0.12)

    def test_non_closing_trial_replaced_and_failed(self) -> None:
        trials = [_trial(trial_id="trial-1"), _trial(trial_id="trial-2")]
        reconciliations = {
            "trial-1": _reconciliation(trial_id="trial-1", attribution_reconciled=False),
            "trial-2": _reconciliation(trial_id="trial-2"),
        }
        result = reconcile_trials(trials, reconciliations=reconciliations)
        self.assertEqual(len(result.trials), 2)
        self.assertEqual(len(result.failed_trials), 1)
        self.assertEqual(result.failed_trials[0].trial_id, "trial-1")
        self.assertIsNone(result.failed_trials[0].metric)
        self.assertIn("attribution", result.failed_trials[0].failed_reason)
        self.assertEqual(result.summary.failed_trial_ids, ("trial-1",))
        self.assertFalse(result.summary.all_reconciled)
        # The closing trial keeps its metric.
        self.assertEqual(result.trials[1].metric, 0.12)

    def test_missing_reconciliation_marks_failed(self) -> None:
        trials = [_trial(trial_id="trial-1")]
        result = reconcile_trials(trials, reconciliations={})
        self.assertEqual(len(result.failed_trials), 1)
        self.assertIn("no reconciliation recorded", result.failed_trials[0].failed_reason)
        self.assertEqual(result.summary.failed_trial_ids, ("trial-1",))

    def test_summary_entries_cover_all_trials(self) -> None:
        trials = [_trial(trial_id="trial-1"), _trial(trial_id="trial-2")]
        reconciliations = {"trial-2": _reconciliation(trial_id="trial-2")}
        result = reconcile_trials(trials, reconciliations=reconciliations)
        self.assertEqual(len(result.summary.reconciliations), 2)
        self.assertEqual(
            [r.trial_id for r in result.summary.reconciliations],
            ["trial-1", "trial-2"],
        )

    def test_readable(self) -> None:
        trials = [_trial(trial_id="trial-1")]
        result = reconcile_trials(trials, reconciliations={"trial-1": _reconciliation()})
        self.assertIn("reconciled trials 1", result.readable())
        self.assertIn("failed 0", result.readable())


class SummaryTests(unittest.TestCase):
    """Verifies the :class:`TrialReconciliationSummary` and fingerprinting."""

    def _summary(self) -> TrialReconciliationSummary:
        return build_reconciliation_summary(
            [
                _reconciliation(trial_id="trial-2"),
                _reconciliation(trial_id="trial-1", attribution_reconciled=False),
            ]
        )

    def test_key_sorted_and_counts(self) -> None:
        summary = self._summary()
        self.assertEqual(
            [r.trial_id for r in summary.reconciliations],
            ["trial-1", "trial-2"],
        )
        self.assertEqual(summary.reconciled_count, 1)
        self.assertEqual(summary.failed_count, 1)
        self.assertEqual(summary.failed_trial_ids, ("trial-1",))
        self.assertFalse(summary.all_reconciled)

    def test_all_reconciled(self) -> None:
        summary = build_reconciliation_summary([_reconciliation(trial_id="trial-1")])
        self.assertTrue(summary.all_reconciled)
        self.assertEqual(summary.failed_trial_ids, ())

    def test_empty_fingerprint_rejected(self) -> None:
        with self.assertRaises(TrialReconciliationError):
            TrialReconciliationSummary(reconciliations=(), fingerprint="")

    def test_unsorted_reconciliations_rejected(self) -> None:
        with self.assertRaises(TrialReconciliationError):
            TrialReconciliationSummary(
                reconciliations=(
                    _reconciliation(trial_id="trial-2"),
                    _reconciliation(trial_id="trial-1"),
                ),
                fingerprint="fp",
            )

    def test_duplicate_trial_rejected(self) -> None:
        with self.assertRaises(TrialReconciliationError):
            TrialReconciliationSummary(
                reconciliations=(
                    _reconciliation(trial_id="trial-1"),
                    _reconciliation(trial_id="trial-1"),
                ),
                fingerprint="fp",
            )

    def test_fingerprint_stable_and_rederivable(self) -> None:
        summary = self._summary()
        self.assertEqual(summary.fingerprint, reconciliation_fingerprint(summary))
        self.assertEqual(len(summary.fingerprint), 64)
        self.assertEqual(
            reconciliation_fingerprint(self._summary()),
            reconciliation_fingerprint(self._summary()),
        )

    def test_fingerprint_changes_with_failures(self) -> None:
        other = build_reconciliation_summary(
            [_reconciliation(trial_id="trial-1"), _reconciliation(trial_id="trial-2")]
        )
        self.assertNotEqual(
            reconciliation_fingerprint(self._summary()),
            reconciliation_fingerprint(other),
        )

    def test_json_key_sorted_and_stable(self) -> None:
        self.assertEqual(reconciliation_json(self._summary()), reconciliation_json(self._summary()))
        self.assertIn('"trial_id":"trial-1"', reconciliation_json(self._summary()))

    def test_readable(self) -> None:
        self.assertIn("failed 1", self._summary().readable())


class ReconciledTrialsTests(unittest.TestCase):
    """Validates the :class:`ReconciledTrials` gate output."""

    def test_failed_trials_must_match_summary(self) -> None:
        summary = build_reconciliation_summary(
            [_reconciliation(trial_id="trial-1", attribution_reconciled=False)]
        )
        with self.assertRaises(TrialReconciliationError):
            ReconciledTrials(
                trials=(),
                failed_trials=(),
                summary=summary,
            )

    def test_valid(self) -> None:
        trial = _trial(trial_id="trial-1")
        result = reconcile_trials(
            [trial],
            reconciliations={
                "trial-1": _reconciliation(trial_id="trial-1", attribution_reconciled=False)
            },
        )
        self.assertEqual(len(result.failed_trials), 1)
        self.assertEqual(result.failed_trials[0].trial_id, "trial-1")


if __name__ == "__main__":
    unittest.main()
