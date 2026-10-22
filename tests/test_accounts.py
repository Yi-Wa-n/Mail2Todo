"""Independent mailbox ledgers; temporary files and mocked IMAP only."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import http.client
import threading
from unittest.mock import patch,Mock
from backend.storage import vault,accounts,repository
from backend.mail import core
from backend.cli import manager
from app.service import Service
from app.server import LocalServer,handler
from app.lifecycle import IdleLifecycle

class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.home=Path(self.temp.name)/'runtime';self.home.mkdir()
        self.patches=[patch.object(vault,'HOME',self.home),patch.object(manager,'HOME',self.home),
                      patch.object(accounts,'connect_mail',return_value=Mock())]
        for p in self.patches:p.start()
        vault.save_credentials({'email':'old@163.com','mail_code':'synthetic-old'})
        db=core.open_db(self.home/'ledger.sqlite3');repository.initialize(db)
        db.execute("INSERT INTO local_steps VALUES ('sample-step','sample-mail','{}',1,4)")
        db.execute("INSERT INTO local_overrides VALUES ('sample-mail','人工填写备注',3)")
        db.execute("INSERT INTO manual_events VALUES ('sample-event','{}',2,0)")
        db.commit()
        core.set_meta(db,'preserved_note','人工备注');core.set_meta(db,'preserved_done',True);db.close()
        self.original=(self.home/'ledger.sqlite3').read_bytes()

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def switch(self,email='new@126.com',code='synthetic-new',key='legacy'):
        with manager.locked():return accounts.update({'op':'switch','email':email,'code':code,'account':key})

    def test_switch_and_return_preserves_old_data_and_independent_new_ledger(self):
        info=self.switch();key=info['active'];self.assertNotEqual('legacy',key)
        self.assertEqual(self.original,(self.home/'ledger.sqlite3').read_bytes())
        db=repository.connect()
        self.assertEqual([],repository.state(db)['emails'])
        self.assertIsNone(core.get_meta(db,'preserved_note'))
        core.set_meta(db,'new_account_note','新邮箱备注');db.close()
        self.switch('old@163.com','',key)
        self.assertEqual('legacy',vault.account_id())
        db=repository.connect();self.assertEqual('人工备注',core.get_meta(db,'preserved_note'));self.assertTrue(core.get_meta(db,'preserved_done'));db.close()
        db=repository.connect()
        self.assertEqual((1,4),tuple(db.execute("SELECT done,version FROM local_steps WHERE id='sample-step'").fetchone()))
        self.assertEqual(('人工填写备注',3),tuple(db.execute("SELECT notes,version FROM local_overrides WHERE mail_id='sample-mail'").fetchone()))
        self.assertEqual(1,db.execute('SELECT count(*) FROM manual_events').fetchone()[0]);db.close()
        self.switch('new@126.com','','legacy')
        self.assertEqual(key,vault.account_id())
        db=repository.connect();self.assertEqual('新邮箱备注',core.get_meta(db,'new_account_note'));db.close()
        self.assertEqual(1,len(list((self.home/'accounts').iterdir())))

    def test_failed_login_and_stale_request_do_not_change_account_or_credentials(self):
        before=(self.home/'credentials.dpapi').read_bytes()
        with patch.object(accounts,'connect_mail',side_effect=RuntimeError('private-output')):
            with self.assertRaisesRegex(ValueError,'邮箱验证失败'):self.switch()
        self.assertEqual('legacy',vault.account_id());self.assertEqual(before,(self.home/'credentials.dpapi').read_bytes())
        with self.assertRaisesRegex(ValueError,'邮箱已切换'):self.switch(key='stale')

    def test_update_code_keeps_database_and_account(self):
        with manager.locked():accounts.update({'op':'credentials','code':'synthetic-updated','account':'legacy'})
        self.assertEqual('synthetic-updated',vault.load_credentials()['mail_code'])
        self.assertEqual('legacy',vault.account_id());self.assertEqual(self.original,(self.home/'ledger.sqlite3').read_bytes())

    def test_scanning_entry_selects_active_ledger_and_audit_directory(self):
        self.switch()
        from backend.jobs import manual_scan,local_gate
        with patch.object(manual_scan,'HOME',self.home),patch.object(local_gate,'RUNTIME',local_gate.RUNTIME),patch.object(manual_scan,'run_once',return_value={'ok':True}) as run:
            (self.home/'local-gate.json').write_text('{}')
            manual_scan.main()
            self.assertEqual(vault.account_home()/'local-gate',local_gate.RUNTIME)
            self.assertEqual(1,run.call_count)
        db=core.open_db(self.home/'ledger.sqlite3')
        self.assertEqual('人工备注',core.get_meta(db,'preserved_note'));db.close()

    def test_shared_lock_blocks_switch_and_stale_edit_is_rejected(self):
        with manager.locked():
            with self.assertRaises(RuntimeError):
                with manager.locked():pass
        service=Service()
        self.switch()
        with self.assertRaisesRegex(ValueError,'邮箱已切换'):
            service.edit('email',{'account':'legacy'})
        with self.assertRaisesRegex(ValueError,'邮箱已切换'):
            service.edit('mail-schedule',{'account':'legacy'})
        service.job['running']=True
        with self.assertRaises(ValueError):service.mailbox({'op':'credentials','code':'test','account':vault.account_id()})

    def test_authenticated_http_switch_uses_selected_account_without_exposing_code(self):
        import app.service as service_module
        with patch.object(service_module,'HOME',self.home),patch('backend.jobs.scheduler.status',return_value={'enabled':False}):
            service=Service()
            server=LocalServer(('127.0.0.1',0),handler(service,'test-token',IdleLifecycle()))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                port=server.server_port
                connection=http.client.HTTPConnection('127.0.0.1',port,timeout=3)
                connection.request('POST','/api/mailbox',json.dumps({'op':'switch','email':'new@126.com','code':'synthetic-private-code','account':'legacy'}),
                                   {'Origin':f'http://127.0.0.1:{port}','X-Local-Token':'test-token'})
                response=connection.getresponse();body=response.read();self.assertEqual(200,response.status)
                self.assertNotIn(b'synthetic-private-code',body);connection.close()
                connection=http.client.HTTPConnection('127.0.0.1',port,timeout=3)
                connection.request('GET','/api/state');response=connection.getresponse();state=json.loads(response.read());connection.close()
                self.assertEqual(vault.account_id(),state['account']);self.assertEqual([],state['emails'])
                self.assertTrue(state['version'].startswith(state['account']+':'))
            finally:server.shutdown();server.server_close();thread.join()
