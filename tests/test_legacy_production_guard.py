"""Guard against mistaking the SQLite/UAT server for a production app."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class LegacyProductionFailClosedTests(unittest.TestCase):
    def test_production_mode_rejects_sqlite_init_and_connect(self):
        old_path = server.DB_PATH
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'must-not-be-created' / 'legacy.db'
            server.DB_PATH = target
            try:
                for mode in ('production', 'PRODUCTION', 'prod'):
                    with self.subTest(mode=mode):
                        with patch.dict(os.environ, {'TEAMSAAS_ENVIRONMENT': mode}):
                            with self.assertRaises(RuntimeError):
                                server.initialize()
                            with self.assertRaises(RuntimeError):
                                server.connect()
                        self.assertFalse(target.exists())
            finally:
                server.DB_PATH = old_path

    def test_uat_sqlite_remains_available(self):
        old_path = server.DB_PATH
        with tempfile.TemporaryDirectory() as directory:
            server.DB_PATH = Path(directory) / 'uat.db'
            try:
                with patch.dict(os.environ, {'TEAMSAAS_ENVIRONMENT': 'uat'}):
                    server.initialize()
                    with server.connect() as db:
                        self.assertEqual(db.execute('SELECT 1').fetchone()[0], 1)
            finally:
                server.DB_PATH = old_path


if __name__ == '__main__':
    unittest.main()
