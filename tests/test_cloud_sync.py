import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from backend.storage import cloud_sync
from backend.mail import core
from backend.storage import repository


class CloudSyncTests(unittest.TestCase):
    def test_redaction_keeps_schedule_and_notes(self):
        value={'notes':'电话13812345678 邮箱a@example.invalid https://example.invalid/private?k=1','start':'2026-10-22T12:00:00+08:00','done':True}
        clean=cloud_sync.clean(value)
        self.assertNotIn('13812345678',clean['notes'])
        self.assertNotIn('example.invalid',clean['notes'])
        self.assertEqual(value['start'],clean['start'])
        self.assertTrue(clean['done'])

    def test_export_does_not_decrypt_or_export_mail_or_config_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder)
            db=core.open_db(home/'ledger.sqlite3');repository.initialize(db);db.close()
            (home/'local-gate.json').write_text(json.dumps({'codebuddy_model':'test','secret':'do-not-export','codebuddy_cli':'private-path'}))
            with patch('backend.jobs.scheduler.status',return_value={'enabled':True,'interval_hours':5,'script':'private-path'}),patch('backend.storage.vault.crypt',side_effect=AssertionError('must not decrypt')):
                value=cloud_sync.snapshot(home,home,'test')
            encoded=json.dumps(value)
            self.assertNotIn('do-not-export',encoded)
            self.assertNotIn('private-path',encoded)
            self.assertEqual([],value['state']['emails'])
            self.assertEqual(5,value['state']['schedule']['interval_hours'])

    def test_failed_readback_is_not_success(self):
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder);(home/'cloud-sync.dpapi').write_bytes(b'encrypted')
            config={'url':'https://sample.chatgpt.site','service_token':'test','upload_key':'test'}
            with patch.object(cloud_sync,'HOME',home),patch.object(cloud_sync,'crypt',return_value=json.dumps(config).encode()),patch.object(cloud_sync,'snapshot',return_value={'state':{'emails':[],'steps':[],'manual_events':[]}}),patch.object(cloud_sync,'request',return_value={'hash':'wrong','revision':0}):
                result=cloud_sync.sync_once()
            self.assertFalse(result['ok'])
            self.assertEqual('RuntimeError',result['error_type'])

    def test_no_configuration_never_connects(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(cloud_sync,'HOME',Path(folder)),patch.object(cloud_sync,'request',side_effect=AssertionError()):
            self.assertEqual({'skipped':'not_configured'},cloud_sync.sync_once())


if __name__=='__main__':unittest.main()
