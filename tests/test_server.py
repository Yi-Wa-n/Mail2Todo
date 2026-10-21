import http.client
import json
import threading
import unittest
from app.lifecycle import IdleLifecycle
from app.server import LocalServer,handler


class FakeService:
    def state(self):return {'emails':[],'steps':[]}
    def edit(self,kind,value):
        if value.get('stale'):raise ValueError('记录已变化')
        return {'saved':kind}
    def manual(self,value):return {'manual':value['op']}
    def save_schedule(self,value):return {'schedule':value}


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.server=LocalServer(('127.0.0.1',0),handler(FakeService(),'test-token',IdleLifecycle()))
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

    def test_browser_stream_and_scan_protect_idle_exit(self):
        clock=[0];life=IdleLifecycle(timeout=120,clock=lambda:clock[0])
        with life.request():
            clock[0]=200;self.assertFalse(life.should_stop())
        clock[0]=400;self.assertFalse(life.should_stop(busy=True))
        clock[0]=519;self.assertFalse(life.should_stop())
        clock[0]=520;self.assertTrue(life.should_stop())


if __name__=='__main__':unittest.main()
