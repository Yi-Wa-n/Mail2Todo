"""Slow OS status queries must not delay local reads or saves."""
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
import threading
import unittest
from unittest.mock import patch
from app.service import Service
from backend.jobs import scheduler


class ServiceLatencyTests(unittest.TestCase):
    def test_state_and_mutations_return_while_scheduler_query_is_blocked(self):
        service = Service.__new__(Service)
        service.guard = threading.RLock()
        service.changed = threading.Condition()
        service.job = {'running':False, 'message':''}
        service.job_version = 0
        service.use = lambda fn: fn(None)
        entered, release = threading.Event(), threading.Event()
        def slow(*args, **kwargs):
            entered.set()
            release.wait(5)
            return subprocess.CompletedProcess([],0,json.dumps({'enabled':True}), '')
        with patch.object(scheduler, '_cached', None), patch.object(scheduler, '_checked', 0), patch.object(scheduler.subprocess, 'run', side_effect=slow), patch('app.service.account_id', return_value='test'), patch('app.service.locked', side_effect=lambda: nullcontext()), patch('app.service.repo.state', return_value={'revision':1}), patch('app.service.repo.revision', return_value=1), patch('app.service.repo.edit_manual') as manual, patch('app.service.repo.mutate') as mutate:
            try:
                with ThreadPoolExecutor() as pool:
                    self.assertTrue(pool.submit(service.state).result(timeout=1)['schedule']['pending'])
                    self.assertTrue(entered.wait(1))
                    for op in ['delete', 'complete']:
                        self.assertIn('version', pool.submit(service.manual, {'account':'test', 'op':op}).result(timeout=1))
                    self.assertIn('version', pool.submit(service.edit, 'step', {'account':'test'}).result(timeout=1))
                    self.assertEqual('test:1:0', pool.submit(service.version).result(timeout=1))
                    self.assertEqual(2, manual.call_count); mutate.assert_called_once()
            finally:
                release.set()
                self.assertTrue(scheduler._refresh_guard.acquire(timeout=2))
                scheduler._refresh_guard.release()
            self.assertEqual(1, service.job_version)
            self.assertTrue(service.state()['schedule']['enabled'])
