import json
from pathlib import Path
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch
from backend.jobs import scheduler,scheduled_gate


class SchedulerTests(unittest.TestCase):
    def parameters(self):return {'interval_hours':1,'logon_delay_minutes':1,'retry_minutes':5,'retry_count':2}

    def test_invalid_parameters_do_not_touch_windows_task(self):
        for override in [{'interval_hours':0},{'interval_hours':True},{'interval_hours':1.1},{'logon_delay_minutes':-1},{'retry_minutes':0},{'retry_count':11},{'retry_count':True}]:
            with patch.object(scheduler.subprocess,'run') as run:
                with self.assertRaises(ValueError):scheduler.update({**self.parameters(),**override})
                run.assert_not_called()

    def test_update_is_verified_and_cached(self):
        value=self.parameters();actual={**value,'installed':True,'enabled':True}
        with patch.object(scheduler,'_cached',None),patch.object(scheduler,'_checked',0),patch.object(scheduler.subprocess,'run',return_value=subprocess.CompletedProcess([],0,json.dumps(actual),'')) as run:
            self.assertEqual(actual,scheduler.update(value));self.assertEqual(actual,scheduler.status());self.assertEqual(1,run.call_count)
            self.assertIn('Update',run.call_args.args[0]);self.assertNotIn('Install',run.call_args.args[0])

    def test_running_scan_and_mismatched_readback_are_not_success(self):
        for result in [subprocess.CompletedProcess([],1,'','SCAN_RUNNING'),subprocess.CompletedProcess([],0,json.dumps({**self.parameters(),'retry_count':3}),'')]:
            with patch.object(scheduler,'_cached',None),patch.object(scheduler.subprocess,'run',return_value=result):
                with self.assertRaises(ValueError):scheduler.update(self.parameters())

    def test_status_is_read_only_and_cached(self):
        result=subprocess.CompletedProcess([],0,json.dumps({'installed':True,'enabled':True,'interval_hours':1}),'')
        with patch.object(scheduler,'_cached',None),patch.object(scheduler,'_checked',0),patch.object(scheduler.subprocess,'run',return_value=result) as run:
            self.assertTrue(scheduler.status()['enabled']);self.assertTrue(scheduler.status()['enabled'])
            self.assertEqual(1,run.call_count)
            self.assertEqual('Status',run.call_args.args[0][-1]);self.assertNotIn('Install',run.call_args.args[0])

    def test_unavailable_status_never_reports_disabled_as_a_fact(self):
        with patch.object(scheduler,'_cached',None),patch.object(scheduler.subprocess,'run',side_effect=OSError):
            value=scheduler.status();self.assertIsNone(value['enabled']);self.assertIn('error',value)

    def test_background_status_never_blocks_and_coalesces_slow_queries(self):
        for cached in [None, {'installed':True, 'enabled':True}]:
            entered, release, notified = threading.Event(), threading.Event(), threading.Event()
            def slow_query(*args, **kwargs):
                entered.set()
                release.wait(3)
                return subprocess.CompletedProcess([], 0, json.dumps({'installed':True, 'enabled':True}), '')
            with patch.object(scheduler, '_cached', cached), patch.object(scheduler, '_checked', 0), patch.object(scheduler.subprocess, 'run', side_effect=slow_query) as run:
                try:
                    with ThreadPoolExecutor() as pool:
                        first = pool.submit(scheduler.status_nowait, notified.set).result(timeout=1)
                        self.assertTrue(first['refreshing'])
                        self.assertTrue(entered.wait(1))
                        for _ in range(4):
                            self.assertTrue(pool.submit(scheduler.status_nowait).result(timeout=1)['refreshing'])
                        self.assertEqual(1, run.call_count)
                        if cached is None:
                            self.assertTrue(first['pending']); self.assertIsNone(first['enabled'])
                        else:
                            self.assertTrue(first['enabled'])
                finally:
                    release.set()
                    self.assertTrue(notified.wait(2))
                    self.assertTrue(scheduler._refresh_guard.acquire(timeout=2))
                    scheduler._refresh_guard.release()
                self.assertTrue(scheduler.status_nowait()['enabled'])
                self.assertEqual(1, run.call_count)

    def test_task_uses_same_scan_and_returns_success(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(scheduled_gate,'HOME',Path(folder)),patch('backend.jobs.manual_scan.main') as scan:
            self.assertEqual(0,scheduled_gate.main());scan.assert_called_once_with()
            record=json.loads((Path(folder)/'local-gate.log').read_text(encoding='utf-8').splitlines()[0])
            self.assertTrue(record['local_only']);self.assertIn('+08:00',record['at'])

    def test_task_failure_returns_nonzero_for_windows_retry(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(scheduled_gate,'HOME',Path(folder)),patch('backend.jobs.manual_scan.main',side_effect=RuntimeError('sensitive value')):
            self.assertEqual(1,scheduled_gate.main())
            text=(Path(folder)/'local-gate.log').read_text(encoding='utf-8')
            self.assertNotIn('sensitive value',text);self.assertIn('RuntimeError',text)
