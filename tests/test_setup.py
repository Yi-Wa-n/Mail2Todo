"""Fresh install checks use temporary directories; no mailbox or CLI execution."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from tools.setup_local import initialize
from backend.storage import vault
from backend.cli import manager
from backend.mail import core
from backend.storage import repository

class FreshInstall(unittest.TestCase):
    def test_initialize_empty_database_and_refuse_overwrite(self):
        config = {'model_provider':'codebuddy_cn','codebuddy_cli':sys.executable,
                  'codebuddy_model':'deepseek-v4.1-flash','codebuddy_fallback_model':'auto',
                  'reasoning_effort':'low','notifications_enabled':True}
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)/'runtime'
            with patch.object(vault,'HOME',home), patch.object(manager,'HOME',home):
                initialize(config, 'test@163.com', 'synthetic-test-code')
                db = core.open_db(home/'ledger.sqlite3')
                try:
                    state = repository.state(db)
                    self.assertEqual([],state['emails'])
                    self.assertEqual([],state['steps'])
                    self.assertEqual([],state['manual_events'])
                finally: db.close()
                self.assertFalse(json.loads((home/'local-gate.json').read_text())['notifications_enabled'])
                before = {p.name:p.read_bytes() for p in home.iterdir() if p.is_file()}
                with self.assertRaises(RuntimeError):
                    initialize(config, 'other@163.com', 'another-test-code')
                self.assertEqual(before,{p.name:p.read_bytes() for p in home.iterdir() if p.is_file()})

    def test_unsupported_mailbox_rejected_before_creating_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)/'runtime'
            with patch.object(vault,'HOME',home):
                with self.assertRaises(ValueError): initialize({},'test@example.invalid','test')
                self.assertFalse(home.exists())
