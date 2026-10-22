"""Loopback-only app. No remote document endpoints or credentials."""
import argparse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
from urllib.parse import urlsplit,parse_qs
import urllib.request
import webbrowser

from app.lifecycle import IdleLifecycle
from app.service import Service
from backend.cli.manager import locked

WEB=Path(__file__).with_name('web')
PORT=18765


class LocalServer(ThreadingHTTPServer):
    allow_reuse_address=False
    def server_bind(self):
        if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        super().server_bind()


def handler(service,token,life):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass

        def handle(self):
            try:
                with life.request():super().handle()
            except RuntimeError:self.close_connection=True

        def allowed(self):
            return self.headers.get('Host')==f'127.0.0.1:{self.server.server_port}' and self.headers.get('Sec-Fetch-Site')!='cross-site'

        def reply(self,value,code=200,mime='application/json; charset=utf-8'):
            raw=json.dumps(value,ensure_ascii=False).encode() if isinstance(value,(dict,list)) else value
            self.send_response(code)
            for name,content in [('Content-Type',mime),('Content-Length',str(len(raw))),('Cache-Control','no-store'),
                ('X-Content-Type-Options','nosniff'),('Referrer-Policy','no-referrer'),
                ('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")]:self.send_header(name,content)
            self.end_headers();self.wfile.write(raw)

        def do_GET(self):
            # A top-level link may legitimately originate outside this site.
            # Only the static entry page is allowed; data APIs remain same-site.
            entry_navigation=(urlsplit(self.path).path=='/' and
                self.headers.get('Host')==f'127.0.0.1:{self.server.server_port}' and
                self.headers.get('Sec-Fetch-Mode')=='navigate' and
                self.headers.get('Sec-Fetch-Dest')=='document')
            if not self.allowed() and not entry_navigation:return self.reply({'error':'仅允许本机访问'},403)
            url=urlsplit(self.path);path=url.path;query=parse_qs(url.query)
            try:
                if path=='/api/health':return self.reply({'app':'autumn-local-only','port':self.server.server_port,'idle_seconds':life.timeout,'instance':life.instance})
                if path=='/api/state':return self.reply(service.state())
                if path=='/api/settings':return self.reply(service.settings())
                if path=='/api/audits':return self.reply(service.audits())
                if path.startswith('/api/audit/'):return self.reply(service.audit(path.rsplit('/',1)[-1]))
                if path=='/api/filtered':return self.reply(service.filtered(int(query.get('offset',['0'])[0])))
                if path.startswith('/api/filtered/'):return self.reply(service.filtered_detail(path.rsplit('/',1)[-1]))
                if path.startswith('/api/mail/'):
                    key=path.rsplit('/',1)[-1]
                    if not __import__('re').fullmatch(r'[0-9a-f]{64}',key):raise ValueError('无效邮件')
                    return self.reply(service.preview(key))
                if path=='/api/session':
                    if not secrets.compare_digest(query.get('token',[''])[0].encode(),token.encode()):return self.reply({'error':'页面连接失效，请刷新'},403)
                    self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Cache-Control','no-store');self.end_headers()
                    try:
                        while not life.stop_event.is_set():
                            self.wfile.write(('data: '+service.version()+'\n\n').encode());self.wfile.flush()
                            with service.changed:service.changed.wait(timeout=5)
                        if life.action:
                            message=json.dumps({'action':life.action,'instance':life.instance})
                            self.wfile.write(('event: service-control\ndata: '+message+'\n\n').encode());self.wfile.flush()
                    except (OSError,ValueError):pass
                    self.close_connection=True;return
                assets={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/style.css':('style.css','text/css; charset=utf-8')}
                if path in assets:
                    name,mime=assets[path]
                    return self.reply((WEB/name).read_bytes().replace(b'__TOKEN__',token.encode()),mime=mime)
                self.reply({'error':'页面不存在'},404)
            except ValueError as error:self.reply({'error':str(error)},400)
            except (OSError,RuntimeError):self.reply({'error':'读取失败，请稍后刷新'},503)

        def do_POST(self):
            expected=f'http://127.0.0.1:{self.server.server_port}'
            if not self.allowed() or self.headers.get('Origin')!=expected or self.headers.get('X-Local-Token')!=token:
                return self.reply({'error':'页面验证失败，请刷新'},403)
            length=self.headers.get('Content-Length','0')
            if self.headers.get('Transfer-Encoding') or not length.isdigit() or int(length)>65536:return self.reply({'error':'请求过大'},400)
            try:
                raw=self.rfile.read(int(length));value=json.loads(raw or b'{}')
                if self.path=='/api/control':
                    if not isinstance(value,dict) or set(value)!={'action'} or value['action'] not in ('exit','restart'):raise ValueError('无效服务操作')
                    try:
                        with locked():
                            life.begin_control(value['action'],busy=service.is_busy())
                    except RuntimeError:
                        raise ValueError('正在扫描或保存，请完成后再退出或重启') from None
                    try:self.reply({'action':life.action,'instance':life.instance})
                    finally:stop_server(self.server,service,life)
                    return
                with life.mutation():
                    if self.path in ('/api/step','/api/email','/api/notes'):return self.reply(service.edit(self.path[5:],value))
                    if self.path=='/api/manual':return self.reply(service.manual(value))
                    if self.path=='/api/scan':return self.reply({'started':service.scan()})
                    if self.path=='/api/settings':return self.reply(service.save_settings(value))
                    if self.path=='/api/mailbox':return self.reply(service.mailbox(value))
                    if self.path=='/api/schedule':return self.reply(service.save_schedule(value))
                    self.reply({'error':'操作不存在'},404)
            except ValueError as error:self.reply({'error':str(error)},409)
            except RuntimeError:self.reply({'error':'本机版正在处理，请稍后重试'},409)
            except OSError:self.reply({'error':'保存失败，请稍后重试'},503)
    return Handler


def stop_server(server,service,life):
    life.stop_event.set()
    with service.changed:service.changed.notify_all()
    threading.Thread(target=server.shutdown,daemon=True).start()


def restart_service(port):
    subprocess.Popen([sys.executable,'-X','utf8','-m','app.server','--no-open','--port',str(port)],
                     cwd=Path(__file__).resolve().parents[1],stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--no-open',action='store_true');parser.add_argument('--port',type=int,default=PORT)
    args=parser.parse_args();url=f'http://127.0.0.1:{args.port}'
    service=Service();life=IdleLifecycle()
    try:server=LocalServer(('127.0.0.1',args.port),handler(service,secrets.token_urlsafe(32),life))
    except OSError:
        try:
            with urllib.request.urlopen(url+'/api/health',timeout=2) as response:
                if json.load(response).get('app')!='autumn-local-only':return
        except (OSError,ValueError):return
        if not args.no_open:webbrowser.open(url)
        return
    threading.Thread(target=life.watch,args=(server,service.is_busy),daemon=True).start()
    if not args.no_open:webbrowser.open(url)
    try:server.serve_forever()
    finally:
        life.stop_event.set()
        with service.changed:service.changed.notify_all()
        server.server_close()
    if life.action=='restart':restart_service(args.port)


if __name__=='__main__':main()
