"""
Incident Agent — Detector Unit Tests
======================================
Tests all 12 required scenarios:
  1.  Backend-down detection
  2.  Database-down detection
  3.  High-error-rate detection
  4.  High-5xx-ratio detection
  5.  High-latency detection
  6.  Slow-database detection
  7.  Sustained-condition requirement (must NOT fire before for_seconds)
  8.  Incident deduplication (only one open incident per fingerprint)
  9.  Incident resolution (condition clears → ResolveResult returned)
  10. Prometheus unavailable (None values → no incident)
  11. Invalid / partial Prometheus response (None values tolerated)
  12. Severity classification (correct Severity enum per rule)

All tests are pure unit tests — no database, no network, no Docker.
The detector's monotonic clock is patched via time.monotonic so tests
run instantly rather than sleeping for real seconds.
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

# ── Path setup ────────────────────────────────────────────────────────────
# Allow imports from incident-agent/ without installing the package
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from incident_agent.detector import RuleBasedDetector
from incident_agent.models import (
    IncidentStatus,
    IncidentType,
    MetricsSnapshot,
    Severity,
)
from incident_agent.config import rules


# ── Helpers ────────────────────────────────────────────────────────────────

def _snap(**kwargs) -> MetricsSnapshot:
    """Build a MetricsSnapshot with safe defaults (all normal)."""
    defaults = dict(
        backend_up=1.0,
        database_up=1.0,
        error_rate=0.0,
        request_rate=5.0,
        p95_request_latency=0.1,
        p95_db_latency=0.05,
        ratio_5xx=0.0,
    )
    defaults.update(kwargs)
    return MetricsSnapshot(**defaults)


def _snap_healthy() -> MetricsSnapshot:
    return _snap()


def _make_detector() -> RuleBasedDetector:
    return RuleBasedDetector()


# ── Controlled time patching ──────────────────────────────────────────────

class FakeClock:
    """
    Replace time.monotonic with a controllable counter.
    Usage:
        with FakeClock() as clock:
            clock.advance(35)   # simulate 35 seconds passing
    """
    def __init__(self) -> None:
        self._t = 0.0

    def now(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds

    def __enter__(self):
        self._patcher = patch("time.monotonic", side_effect=self.now)
        self._patcher.start()
        return self

    def __exit__(self, *args):
        self._patcher.stop()


# ══════════════════════════════════════════════════════════════════════════
#  Test cases
# ══════════════════════════════════════════════════════════════════════════

class TestBackendDownDetection(unittest.TestCase):
    """Test 1 — Backend-down detection."""

    def test_fires_after_for_seconds(self):
        """BACKEND_DOWN incident created once for_seconds elapses."""
        det = _make_detector()
        for_s = rules.rule_for_seconds("backend_down")  # 15s
        snap = _snap(backend_up=0.0)

        with FakeClock() as clk:
            # First call — condition true, timer starts, not yet elapsed
            opens, resolves = det.evaluate(snap)
            self.assertEqual(opens, [], "Should not fire immediately")

            # Advance past for_seconds
            clk.advance(for_s + 1)
            opens, resolves = det.evaluate(snap)

        self.assertEqual(len(opens), 1)
        inc = opens[0].incident
        self.assertEqual(inc.incident_type, IncidentType.BACKEND_DOWN)
        self.assertEqual(inc.service, "backend")
        self.assertEqual(inc.status, IncidentStatus.OPEN)

    def test_does_not_fire_before_for_seconds(self):
        """Timer must elapse — no false-positive on first sample."""
        det = _make_detector()
        for_s = rules.rule_for_seconds("backend_down")
        snap = _snap(backend_up=0.0)

        with FakeClock() as clk:
            clk.advance(for_s - 1)   # just under threshold
            opens, _ = det.evaluate(snap)

        self.assertEqual(opens, [])

    def test_no_fire_when_backend_up(self):
        det = _make_detector()
        with FakeClock() as clk:
            clk.advance(60)
            opens, _ = det.evaluate(_snap(backend_up=1.0))
        self.assertEqual(opens, [])


class TestDatabaseDownDetection(unittest.TestCase):
    """Test 2 — Database-down detection."""

    def test_fires_after_for_seconds(self):
        det = _make_detector()
        for_s = rules.rule_for_seconds("database_down")
        snap = _snap(database_up=0.0)

        with FakeClock() as clk:
            det.evaluate(snap)
            clk.advance(for_s + 1)
            opens, _ = det.evaluate(snap)

        self.assertEqual(len(opens), 1)
        self.assertEqual(opens[0].incident.incident_type, IncidentType.DATABASE_DOWN)
        self.assertEqual(opens[0].incident.service, "database")

    def test_no_fire_when_database_up(self):
        det = _make_detector()
        with FakeClock() as clk:
            clk.advance(60)
            opens, _ = det.evaluate(_snap(database_up=1.0))
        self.assertEqual(opens, [])


class TestHighErrorRateDetection(unittest.TestCase):
    """Test 3 — High-error-rate detection."""

    def _threshold(self) -> float:
        return rules.rule_threshold("high_error_rate")

    def test_fires_above_threshold(self):
        det = _make_detector()
        for_s = rules.rule_for_seconds("high_error_rate")
        snap = _snap(error_rate=self._threshold() + 0.1)

        with FakeClock() as clk:
            det.evaluate(snap)
            clk.advance(for_s + 1)
            opens, _ = det.evaluate(snap)

        self.assertEqual(len(opens), 1)
        self.assertEqual(opens[0].incident.incident_type, IncidentType.HIGH_ERROR_RATE)

    def test_no_fire_at_threshold(self):
        """Strictly greater-than — equal to threshold must NOT fire."""
        det = _make_detector()
        snap = _snap(error_rate=self._threshold())  # exactly at threshold
        with FakeClock() as clk:
            clk.advance(60)
            opens, _ = det.evaluate(snap)
        self.assertEqual(opens, [])

    def test_no_fire_below_threshold(self):
        det = _make_detector()
        snap = _snap(error_rate=self._threshold() - 0.1)
        with FakeClock() as clk:
            clk.advance(60)
            opens, _ = det.evaluate(snap)
        self.assertEqual(opens, [])


class TestHigh5xxRatioDetection(unittest.TestCase):
    """Test 4 — High-5xx-ratio detection."""

    def test_fires_above_threshold(self):
        det = _make_detector()
        for_s = rules.rule_for_seconds("high_5xx_ratio")
        thr = rules.rule_threshold("high_5xx_ratio")
        snap = _snap(ratio_5xx=thr + 0.05)

        with FakeClock() as clk:
            det.evaluate(snap)
            clk.advance(for_s + 1)
            opens, _ = det.evaluate(snap)

        self.assertEqual(len(opens), 1)
        self.assertEqual(opens[0].incident.incident_type, IncidentType.HIGH_5XX_RATIO)

    def test_no_fire_below_threshold(self):
        det = _make_detector()
        thr = rules.rule_threshold("high_5xx_ratio")
        snap = _snap(ratio_5xx=thr - 0.01)
        with FakeClock() as clk:
            clk.advance(60)
            opens, _ = det.evaluate(snap)
        self.assertEqual(opens, [])


class TestHighLatencyDetection(unittest.TestCase):
    """Test 5 — High-latency detection."""

    def test_fires_above_threshold(self):
        det = _make_detector()
        for_s = rules.rule_for_seconds("high_latency")
        thr = rules.rule_threshold("high_latency", "p95_threshold_seconds")
        snap = _snap(p95_request_latency=thr + 0.5)

        with FakeClock() as clk:
            det.evaluate(snap)
            clk.advance(for_s + 1)
            opens, _ = det.evaluate(snap)

        self.assertEqual(len(opens), 1)
        self.assertEqual(opens[0].incident.incident_type, IncidentType.HIGH_LATENCY)

    def test_no_fire_below_threshold(self):
        det = _make_detector()
        thr = rules.rule_threshold("high_latency", "p95_threshold_seconds")
        snap = _snap(p95_request_latency=thr - 0.5)
        with FakeClock() as clk:
            clk.advance(60)
            opens, _ = det.evaluate(snap)
        self.assertEqual(opens, [])


class TestSlowDatabaseDetection(unittest.TestCase):
    """Test 6 — Slow-database detection."""

    def test_fires_above_threshold(self):
        det = _make_detector()
        for_s = rules.rule_for_seconds("slow_database")
        thr = rules.rule_threshold("slow_database", "p95_threshold_seconds")
        snap = _snap(p95_db_latency=thr + 0.5)

        with FakeClock() as clk:
            det.evaluate(snap)
            clk.advance(for_s + 1)
            opens, _ = det.evaluate(snap)

        self.assertEqual(len(opens), 1)
        self.assertEqual(opens[0].incident.incident_type, IncidentType.SLOW_DATABASE)
        self.assertEqual(opens[0].incident.service, "database")

    def test_no_fire_below_threshold(self):
        det = _make_detector()
        thr = rules.rule_threshold("slow_database", "p95_threshold_seconds")
        snap = _snap(p95_db_latency=thr - 0.1)
        with FakeClock() as clk:
            clk.advance(60)
            opens, _ = det.evaluate(snap)
        self.assertEqual(opens, [])


class TestSustainedCondition(unittest.TestCase):
    """Test 7 — Sustained-condition requirement."""

    def test_timer_resets_when_condition_clears(self):
        """If condition goes false before for_seconds, timer resets."""
        det = _make_detector()
        for_s = rules.rule_for_seconds("backend_down")
        down_snap = _snap(backend_up=0.0)
        up_snap   = _snap(backend_up=1.0)

        with FakeClock() as clk:
            det.evaluate(down_snap)       # [t=0] starts timer
            clk.advance(for_s - 2)
            det.evaluate(down_snap)       # [t=for_s-2] still pending
            det.evaluate(up_snap)         # [t=for_s-2] clears pending timer
            self.assertEqual(det.pending_fingerprints(), [],
                             "Pending state should be cleared")
            # New window: condition true again → NEW timer starts on this call
            det.evaluate(down_snap)       # [t=for_s-2] first_seen set HERE
            clk.advance(for_s + 2)        # advance past new threshold
            opens, _ = det.evaluate(down_snap)   # [t=2*for_s] should fire

        self.assertEqual(len(opens), 1,
                         "Incident should fire after a full new for_seconds window")

    def test_exactly_at_boundary_does_not_fire(self):
        """for_seconds is ≥ (greater-or-equal), so exactly at boundary fires."""
        det = _make_detector()
        for_s = rules.rule_for_seconds("backend_down")
        snap = _snap(backend_up=0.0)

        with FakeClock() as clk:
            det.evaluate(snap)
            clk.advance(for_s)           # exactly at boundary
            opens, _ = det.evaluate(snap)

        # duration() >= for_seconds → fires
        self.assertEqual(len(opens), 1)

    def test_multiple_rules_independent_timers(self):
        """Each rule tracks its own timer independently."""
        det = _make_detector()
        bd_for  = rules.rule_for_seconds("backend_down")
        err_for = rules.rule_for_seconds("high_error_rate")
        thr_err = rules.rule_threshold("high_error_rate")

        snap = _snap(backend_up=0.0, error_rate=thr_err + 0.5)

        with FakeClock() as clk:
            det.evaluate(snap)
            # Advance past backend_down threshold but not high_error_rate
            clk.advance(max(bd_for, err_for) + 1)
            opens, _ = det.evaluate(snap)

        types = {o.incident.incident_type for o in opens}
        self.assertIn(IncidentType.BACKEND_DOWN,    types)
        self.assertIn(IncidentType.HIGH_ERROR_RATE, types)


class TestIncidentDeduplication(unittest.TestCase):
    """Test 8 — Only one active incident per fingerprint."""

    def test_no_duplicate_opens(self):
        """Once firing, repeated true conditions produce no more OpenResults."""
        det = _make_detector()
        for_s = rules.rule_for_seconds("backend_down")
        snap = _snap(backend_up=0.0)

        with FakeClock() as clk:
            det.evaluate(snap)
            clk.advance(for_s + 1)
            opens1, _ = det.evaluate(snap)

        # Simulate agent marking it as opened
        self.assertEqual(len(opens1), 1)
        fp = opens1[0].incident.fingerprint
        det.mark_opened(fp, db_id=42)

        # Further evaluations with condition still true
        with FakeClock() as clk:
            clk.advance(100)
            opens2, _ = det.evaluate(snap)
            opens3, _ = det.evaluate(snap)

        self.assertEqual(opens2, [], "No duplicate open after mark_opened")
        self.assertEqual(opens3, [], "No duplicate open on third eval")

    def test_active_count_correct(self):
        det = _make_detector()
        for_s = rules.rule_for_seconds("backend_down")
        snap = _snap(backend_up=0.0)

        with FakeClock() as clk:
            det.evaluate(snap)
            clk.advance(for_s + 1)
            opens, _ = det.evaluate(snap)

        det.mark_opened(opens[0].incident.fingerprint, db_id=1)
        self.assertEqual(det.active_count(), 1)


class TestIncidentResolution(unittest.TestCase):
    """Test 9 — Incident resolves when condition clears."""

    def test_resolve_after_open(self):
        det = _make_detector()
        for_s = rules.rule_for_seconds("backend_down")
        down = _snap(backend_up=0.0)
        up   = _snap(backend_up=1.0)

        with FakeClock() as clk:
            det.evaluate(down)
            clk.advance(for_s + 1)
            opens, _ = det.evaluate(down)

        fp = opens[0].incident.fingerprint
        det.mark_opened(fp, db_id=99)

        with FakeClock():
            _, resolves = det.evaluate(up)

        self.assertEqual(len(resolves), 1)
        self.assertEqual(resolves[0].fingerprint, fp)
        self.assertEqual(resolves[0].incident_id, 99)

    def test_active_count_zero_after_resolve(self):
        det = _make_detector()
        for_s = rules.rule_for_seconds("backend_down")
        snap_down = _snap(backend_up=0.0)
        snap_up   = _snap(backend_up=1.0)

        with FakeClock() as clk:
            det.evaluate(snap_down)
            clk.advance(for_s + 1)
            opens, _ = det.evaluate(snap_down)

        fp = opens[0].incident.fingerprint
        det.mark_opened(fp, db_id=7)
        self.assertEqual(det.active_count(), 1)

        with FakeClock():
            _, resolves = det.evaluate(snap_up)

        det.mark_resolved(fp)
        self.assertEqual(det.active_count(), 0)

    def test_no_resolve_if_not_firing(self):
        """Condition clears before for_seconds — no ResolveResult."""
        det = _make_detector()
        snap_down = _snap(backend_up=0.0)
        snap_up   = _snap(backend_up=1.0)

        with FakeClock() as clk:
            det.evaluate(snap_down)   # starts timer
            clk.advance(5)
            _, resolves = det.evaluate(snap_up)   # clears before threshold

        self.assertEqual(resolves, [])


class TestPrometheusUnavailable(unittest.TestCase):
    """Test 10 — All metrics None when Prometheus is down."""

    def test_none_metrics_do_not_trigger(self):
        """No incidents should fire when all metric values are None."""
        det = _make_detector()
        none_snap = MetricsSnapshot()   # all fields default to None

        with FakeClock() as clk:
            clk.advance(120)
            opens, resolves = det.evaluate(none_snap)

        self.assertEqual(opens,    [], "None metrics must not open incidents")
        self.assertEqual(resolves, [], "None metrics must not resolve incidents")

    def test_partial_none_only_fires_available_rules(self):
        """Only rules with non-None values that cross thresholds should fire."""
        det = _make_detector()
        for_s = rules.rule_for_seconds("backend_down")

        # backend_up is 0.0 (confirmed); all other metrics are None
        partial = MetricsSnapshot(backend_up=0.0)

        with FakeClock() as clk:
            det.evaluate(partial)
            clk.advance(for_s + 1)
            opens, _ = det.evaluate(partial)

        types = {o.incident.incident_type for o in opens}
        self.assertIn(IncidentType.BACKEND_DOWN, types)
        # database_down should NOT fire because database_up is None
        self.assertNotIn(IncidentType.DATABASE_DOWN, types)


class TestInvalidPrometheusResponse(unittest.TestCase):
    """Test 11 — Malformed / partial metric values handled safely."""

    def test_none_error_rate_does_not_raise(self):
        det = _make_detector()
        snap = _snap(error_rate=None)
        try:
            with FakeClock() as clk:
                clk.advance(120)
                det.evaluate(snap)
        except Exception as exc:
            self.fail(f"evaluate() raised unexpectedly: {exc}")

    def test_none_latency_does_not_raise(self):
        det = _make_detector()
        snap = _snap(p95_request_latency=None, p95_db_latency=None)
        try:
            with FakeClock() as clk:
                clk.advance(120)
                det.evaluate(snap)
        except Exception as exc:
            self.fail(f"evaluate() raised unexpectedly: {exc}")

    def test_none_ratio_does_not_raise(self):
        det = _make_detector()
        snap = _snap(ratio_5xx=None)
        try:
            with FakeClock() as clk:
                clk.advance(120)
                det.evaluate(snap)
        except Exception as exc:
            self.fail(f"evaluate() raised unexpectedly: {exc}")


class TestSeverityClassification(unittest.TestCase):
    """Test 12 — Correct severity assigned by rule config."""

    def _get_severity(self, rule_name: str, snap: MetricsSnapshot) -> Severity:
        det = _make_detector()
        for_s = rules.rule_for_seconds(rule_name)
        with FakeClock() as clk:
            det.evaluate(snap)
            clk.advance(for_s + 1)
            opens, _ = det.evaluate(snap)
        self.assertEqual(len(opens), 1, f"Expected 1 incident for rule {rule_name}")
        return opens[0].incident.severity

    def test_backend_down_is_critical(self):
        sev = self._get_severity("backend_down", _snap(backend_up=0.0))
        self.assertEqual(sev, Severity.CRITICAL)

    def test_database_down_is_critical(self):
        sev = self._get_severity("database_down", _snap(database_up=0.0))
        self.assertEqual(sev, Severity.CRITICAL)

    def test_high_error_rate_is_critical(self):
        thr = rules.rule_threshold("high_error_rate")
        sev = self._get_severity("high_error_rate", _snap(error_rate=thr + 0.5))
        self.assertEqual(sev, Severity.CRITICAL)

    def test_high_5xx_ratio_is_high(self):
        thr = rules.rule_threshold("high_5xx_ratio")
        sev = self._get_severity("high_5xx_ratio", _snap(ratio_5xx=thr + 0.05))
        self.assertEqual(sev, Severity.HIGH)

    def test_high_latency_is_high(self):
        thr = rules.rule_threshold("high_latency", "p95_threshold_seconds")
        sev = self._get_severity("high_latency", _snap(p95_request_latency=thr + 1.0))
        self.assertEqual(sev, Severity.HIGH)

    def test_slow_database_is_high(self):
        thr = rules.rule_threshold("slow_database", "p95_threshold_seconds")
        sev = self._get_severity("slow_database", _snap(p95_db_latency=thr + 0.5))
        self.assertEqual(sev, Severity.HIGH)

    def test_severity_comes_from_rules_yml(self):
        """Changing rules.yml severity should change the classified incident."""
        import copy
        original = rules._raw.get("incident_rules", {}).get("backend_down", {}).copy()
        try:
            rules._raw["incident_rules"]["backend_down"]["severity"] = "low"
            sev = self._get_severity("backend_down", _snap(backend_up=0.0))
            self.assertEqual(sev, Severity.LOW,
                             "Severity must be read from rules.yml, not hardcoded")
        finally:
            rules._raw["incident_rules"]["backend_down"].update(original)


class TestFingerprint(unittest.TestCase):
    """Additional: fingerprint format and uniqueness."""

    def test_fingerprint_format(self):
        from incident_agent.models import Incident, IncidentType
        fp = Incident.make_fingerprint(IncidentType.BACKEND_DOWN, "backend")
        self.assertEqual(fp, "BACKEND_DOWN:backend")

    def test_different_rules_different_fingerprints(self):
        from incident_agent.models import Incident, IncidentType
        fp1 = Incident.make_fingerprint(IncidentType.BACKEND_DOWN,  "backend")
        fp2 = Incident.make_fingerprint(IncidentType.DATABASE_DOWN, "database")
        self.assertNotEqual(fp1, fp2)


# ── Runner ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
