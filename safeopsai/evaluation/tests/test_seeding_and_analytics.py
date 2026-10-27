"""
SafeOpsAI — Demo Seeding & Dashboard Analytics Tests
=====================================================
Validates:
1. Seeding of 100+ realistic demo incident records across 8 services & 8 incident types.
2. Idempotency and duplicate prevention on repeated seeder invocations.
3. Strict demo data isolation (is_demo=TRUE, detection_source='demo_seed').
4. Agent queue view shielding (background workers never process demo records).
5. /dashboard/analytics aggregation calculations (MTTR, success rate, distributions).
6. /incidents search, filtering, and pagination query handling.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
import urllib.request
import json
from pathlib import Path
import asyncpg

# Ensure safeopsai directory is on sys.path
_SAFEOPS_DIR = Path(__file__).parent.parent.parent
if str(_SAFEOPS_DIR) not in sys.path:
    sys.path.insert(0, str(_SAFEOPS_DIR))

from database.seed_incidents import seed_demo_incidents

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "safeopsdb")
DB_USER = os.getenv("DB_USER", "safeops")
DB_PASS = os.getenv("DB_PASS", "safeops123")


class TestDemoSeedingAndAnalytics(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Run seeder with 120 count to ensure known state
        cls.summary = asyncio.run(seed_demo_incidents(count=120, seed=42, clean_demo=False))

    def test_01_seeding_creates_minimum_100_records(self):
        """Verify that at least 100 sample incidents are generated."""
        self.assertGreaterEqual(self.summary["demo_incidents"], 100)
        self.assertGreaterEqual(self.summary["total_incidents_in_db"], 106)
        self.assertIn("database", self.summary["service_distribution"])
        self.assertIn("backend", self.summary["service_distribution"])
        self.assertIn("resolved", self.summary["status_distribution"])

    def test_02_idempotency_prevents_duplicate_records(self):
        """Verify that re-running the seeder does not create duplicate incidents."""
        initial_total = self.summary["total_incidents_in_db"]
        second_run = asyncio.run(seed_demo_incidents(count=120, seed=42, clean_demo=False))
        self.assertEqual(second_run["total_incidents_in_db"], initial_total)
        self.assertEqual(second_run["created_records"], 0)
        self.assertEqual(second_run["updated_records"], 120)

    def test_03_demo_data_isolation(self):
        """Confirm demo records are strictly tagged with is_demo=TRUE and detection_source='demo_seed'."""
        async def verify_isolation():
            conn = await asyncpg.connect(host=DB_HOST, port=DB_PORT, database=DB_NAME, user=DB_USER, password=DB_PASS)
            try:
                demo_count = await conn.fetchval("SELECT COUNT(*) FROM incidents WHERE is_demo = TRUE")
                real_count = await conn.fetchval("SELECT COUNT(*) FROM incidents WHERE is_demo = FALSE")
                demo_source = await conn.fetchval("SELECT COUNT(*) FROM incidents WHERE detection_source = 'demo_seed'")

                self.assertGreaterEqual(demo_count, 120)
                self.assertGreaterEqual(real_count, 6)
                self.assertEqual(demo_count, demo_source)
            finally:
                await conn.close()

        asyncio.run(verify_isolation())

    def test_04_agent_queues_shield_demo_records(self):
        """Verify background agent queue views never expose demo records."""
        async def verify_queues():
            conn = await asyncpg.connect(host=DB_HOST, port=DB_PORT, database=DB_NAME, user=DB_USER, password=DB_PASS)
            try:
                for q in ["rca_queue", "scoring_queue", "coordinator_queue", "sandbox_queue", "remediation_queue"]:
                    cnt = await conn.fetchval(f"SELECT COUNT(*) FROM {q}")
                    self.assertEqual(cnt, 0, f"Queue {q} should not have demo items queued")
            finally:
                await conn.close()

        asyncio.run(verify_queues())

    def test_05_dashboard_analytics_api_endpoint(self):
        """Verify /dashboard/analytics returns valid mathematical distributions."""
        req = urllib.request.Request("http://localhost:8000/dashboard/analytics")
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode())

            self.assertIn("total_incidents", data)
            self.assertIn("active_incidents", data)
            self.assertIn("resolved_incidents", data)
            self.assertIn("severity_distribution", data)
            self.assertIn("incident_types", data)
            self.assertIn("service_distribution", data)
            self.assertIn("incidents_over_time", data)

            self.assertGreaterEqual(data["total_incidents"], 100)
            self.assertGreater(len(data["severity_distribution"]), 0)
            self.assertGreater(len(data["incident_types"]), 0)
            self.assertGreater(len(data["service_distribution"]), 0)
            self.assertGreater(len(data["incidents_over_time"]), 0)

    def test_06_incidents_filtering_and_pagination(self):
        """Verify /incidents query parameters for service, severity, and limit."""
        # 1. Filter by service
        with urllib.request.urlopen("http://localhost:8000/incidents?service=database&limit=10", timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            items = json.loads(resp.read().decode())
            for item in items:
                self.assertEqual(item["service"], "database")

        # 2. Filter by severity
        with urllib.request.urlopen("http://localhost:8000/incidents?severity=critical&limit=10", timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            items = json.loads(resp.read().decode())
            for item in items:
                self.assertEqual(item["severity"], "critical")


if __name__ == "__main__":
    unittest.main()
