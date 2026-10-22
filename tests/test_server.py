import http.client
import json
import threading
import unittest
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from app.lifecycle import IdleLifecycle
from app.server import LocalServer,handler


class FakeService:
    def __init__(self):
        self.changed=threading.Condition()
    def is_busy(self):return False
    def version(self):return "test:1"
    def state(self):return {'emails':[],'steps':[]}
    def edit(self,kind,value):
        if value.get('stale'):raise ValueError('记录已变化')
        return {'saved':kind}
    def manual(self,value):return {'manual':value['op']}
    def save_schedule(self,value):return {'schedule':value}


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.life=IdleLifecycle()
        self.service=FakeService()
        self.server=LocalServer(('127.0.0.1',0),handler(self.service,'test-token',self.life))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.port=self.server.server_port

    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join()

    def request(self,path,method='GET',body=None,headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.port,timeout=3)
        connection.request(method,path,body=json.dumps(body) if body is not None else None,headers=headers or {})
        response=connection.getresponse();result=(response.status,response.read(),dict(response.getheaders()));connection.close();return result

    def test_html_has_token_and_offline_assets(self):
        status,body,headers=self.request('/')
        self.assertEqual(200,status);self.assertIn(b'test-token',body);self.assertIn("default-src 'self'",headers['Content-Security-Policy'])
        for path in ['/app.js','/style.css']:self.assertEqual(200,self.request(path)[0])

    def test_invalid_host_and_cross_origin_are_rejected(self):
        self.assertEqual(403,self.request('/api/state',headers={'Host':'evil.invalid'})[0])
        self.assertEqual(403,self.request('/api/state',headers={'Sec-Fetch-Site':'cross-site'})[0])
        self.assertEqual(403,self.request('/api/email','POST',{})[0])
        navigation={'Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':'navigate','Sec-Fetch-Dest':'document'}
        self.assertEqual(200,self.request('/',headers=navigation)[0])
        self.assertEqual(403,self.request('/api/state',headers=navigation)[0])

    def test_authenticated_mutation_and_conflict(self):
        headers={'Origin':f'http://127.0.0.1:{self.port}','X-Local-Token':'test-token'}
        self.assertEqual(200,self.request('/api/email','POST',{},headers)[0])
        self.assertEqual(409,self.request('/api/email','POST',{'stale':True},headers)[0])
        self.assertEqual(200,self.request('/api/manual','POST',{'op':'create'},headers)[0])
        self.assertEqual(200,self.request('/api/schedule','POST',{'interval_hours':1},headers)[0])
        self.assertEqual(200,self.request('/api/mail-schedule','POST',{'op':'save'},headers)[0])
        self.assertEqual(403,self.request('/api/mail-schedule','POST',{'op':'save'})[0])

    def headers(self):
        return {'Origin':f'http://127.0.0.1:{self.port}','X-Local-Token':'test-token'}

    def test_control_requires_authentication_and_valid_action(self):
        with patch('app.server.stop_server') as stop:
            self.assertEqual(403,self.request('/api/control','POST',{'action':'exit'})[0])
            self.assertEqual(409,self.request('/api/control','POST',{'action':'delete'},self.headers())[0])
            self.assertEqual(409,self.request('/api/control','POST',{'action':'exit','extra':1},self.headers())[0])
            stop.assert_not_called()
        self.assertIsNone(self.life.action)

    def test_control_rejects_scans_and_shared_background_lock(self):
        with patch('app.server.stop_server') as stop, patch('app.server.locked',return_value=nullcontext()):
            with patch.object(self.service,'is_busy',return_value=True):
                status,body,_=self.request('/api/control','POST',{'action':'restart'},self.headers())
                self.assertEqual(409,status);self.assertIn('扫描',json.loads(body)['error'])
            with patch('app.server.locked',side_effect=RuntimeError('locked')):
                self.assertEqual(409,self.request('/api/control','POST',{'action':'exit'},self.headers())[0])
            stop.assert_not_called()
        self.assertIsNone(self.life.action)

    def test_control_rejects_concurrent_save_without_interrupting_it(self):
        entered,release=threading.Event(),threading.Event()
        def save(*args):
            entered.set();release.wait(3);return {'saved':True}
        with patch.object(self.service,'edit',side_effect=save), patch('app.server.locked',return_value=nullcontext()), patch('app.server.stop_server') as stop:
            with ThreadPoolExecutor() as pool:
                pending=pool.submit(self.request,'/api/notes','POST',{},self.headers())
                try:
                    self.assertTrue(entered.wait(1))
                    self.assertEqual(409,self.request('/api/control','POST',{'action':'exit'},self.headers())[0])
                    stop.assert_not_called()
                finally:release.set()
                self.assertEqual(200,pending.result(timeout=2)[0])
        self.assertIsNone(self.life.action)

    def test_control_accepts_idle_exit_and_returns_instance(self):
        with patch('app.server.locked',return_value=nullcontext()), patch('app.server.stop_server') as stop:
            status,body,_=self.request('/api/control','POST',{'action':'exit'},self.headers())
            self.assertEqual(200,status);self.assertEqual(self.life.instance,json.loads(body)['instance'])
            self.assertEqual('exit',self.life.action)
            stop.assert_called_once_with(self.server,self.service,self.life)

    def test_lifecycle_blocks_new_writes_after_control_and_recovers_from_busy(self):
        life=IdleLifecycle()
        with life.mutation():
            with self.assertRaises(ValueError):life.begin_control('restart')
        self.assertIsNone(life.action)
        with self.assertRaises(ValueError):life.begin_control('restart',busy=True)
        life.begin_control('restart')
        with self.assertRaises(ValueError):
            with life.mutation():self.fail('must not start a new save')

    def test_control_notifies_open_page_and_stops_without_idle_delay(self):
        connection=http.client.HTTPConnection('127.0.0.1',self.port,timeout=3)
        connection.request('GET','/api/session?token=test-token')
        stream=connection.getresponse()
        self.assertEqual(b'data: test:1\n',stream.readline());self.assertEqual(b'\n',stream.readline())
        try:
            with patch('app.server.locked',return_value=nullcontext()):
                self.assertEqual(200,self.request('/api/control','POST',{'action':'restart'},self.headers())[0])
            self.assertEqual(b'event: service-control\n',stream.readline())
            event=json.loads(stream.readline().decode().removeprefix('data: '))
            self.assertEqual('restart',event['action']);self.assertEqual(self.life.instance,event['instance'])
            self.thread.join(timeout=2)
            self.assertFalse(self.thread.is_alive())
        finally:connection.close()

    def test_restart_launch_is_hidden_and_preserves_runtime_and_port(self):
        import sys
        from app.server import restart_service
        with patch('app.server.subprocess.Popen') as launch:
            restart_service(19999)
        args,options=launch.call_args
        self.assertEqual(sys.executable,args[0][0])
        self.assertEqual(['--no-open','--port','19999'],args[0][-3:])
        self.assertFalse(options.get('shell',False))
        self.assertIn('creationflags',options)

    def test_browser_stream_and_scan_protect_idle_exit(self):
        clock=[0];life=IdleLifecycle(timeout=120,clock=lambda:clock[0])
        with life.request():
            clock[0]=200;self.assertFalse(life.should_stop())
        clock[0]=400;self.assertFalse(life.should_stop(busy=True))
        clock[0]=519;self.assertFalse(life.should_stop())
        clock[0]=520;self.assertTrue(life.should_stop())


if __name__=='__main__':unittest.main()
