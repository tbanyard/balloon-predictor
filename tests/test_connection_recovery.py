"""Real loopback SSH disconnects, cached-password recovery and deferred sign-in."""
import asyncio
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import threading
import time
from urllib.request import Request,urlopen
import asyncssh
from desktop.connection import Chooser
from desktop.forward import Forward,CONTROL


def test_real_ssh_recovery_retains_origin_and_defers_prompts(tmp_path):
    class HTTP(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            body=json.dumps({'status':'ok','time_reference':'UTC'}).encode()
            self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    http=ThreadingHTTPServer(('127.0.0.1',0),HTTP);threading.Thread(target=http.serve_forever,daemon=True).start()
    ready=threading.Event();context={};connections=[]
    class SSH(asyncssh.SSHServer):
        def connection_made(self,connection):connections.append(connection)
        def begin_auth(self,username):return True
        def password_auth_supported(self):return True
        def validate_password(self,username,password):return username=='fixture' and password=='fixture-password'
        def connection_requested(self,host,port,*args):return host=='127.0.0.1' and port==http.server_port
    async def server():
        key=asyncssh.generate_private_key('ssh-ed25519');context['loop']=asyncio.get_running_loop()
        context['server']=await asyncssh.listen('127.0.0.1',0,server_factory=SSH,server_host_keys=[key]);context['pin']=key.get_fingerprint('sha256');context['port']=context['server'].get_port();ready.set();await asyncio.Future()
    def run():
        loop=asyncio.new_event_loop();context['loop']=loop;asyncio.set_event_loop(loop)
        task=loop.create_task(server());context['task']=task
        try:loop.run_until_complete(task)
        except asyncio.CancelledError:pass
        finally:
            for task in asyncio.all_tasks(loop):task.cancel()
            loop.run_until_complete(asyncio.gather(*asyncio.all_tasks(loop),return_exceptions=True));loop.close()
    thread=threading.Thread(target=run,daemon=True);thread.start();assert ready.wait(10)
    chooser=Chooser(tmp_path);chooser.pins[f"127.0.0.1:{context['port']}"]=context['pin'];questions=[]
    def answer(*args):questions.append(args);return ['fixture-password']
    chooser.ask=answer
    forward=Forward(chooser,'127.0.0.1',context['port'],'fixture',http.server_port)
    def request(action,data=None):
        options={} if data is None else {'data':json.dumps(data).encode(),'headers':{'Origin':origin,'Content-Type':'application/json'}}
        with urlopen(Request(origin+CONTROL+action,**options),timeout=8) as response:return json.load(response)
    def wait_for(predicate):
        limit=time.monotonic()+12
        while time.monotonic()<limit:
            if predicate():return
            time.sleep(.05)
        raise AssertionError(f'Condition not reached; forward state={forward.status}')
    try:
        origin=forward.start();assert len(questions)==1
        with urlopen(origin+'/api/health',timeout=8) as response:assert json.load(response)['status']=='ok'
        context['loop'].call_soon_threadsafe(connections[-1].abort)
        wait_for(lambda:len(connections)>=2 and forward.status=='connected')
        assert request('state')['status']=='connected' and origin==forward.origin and len(questions)==1
        # Password cannot be replayed: no prompt appears during background work.
        forward.password=None
        context['loop'].call_soon_threadsafe(connections[-1].abort)
        wait_for(lambda:forward.status=='signin_required')
        assert len(questions)==1
        request('recover',{})
        wait_for(lambda:forward.status=='connected')
        assert len(questions)==2 and origin==forward.origin
        request('flights',{'workspace':{'example':'saved tracks'},'recent':[]})
        assert request('flights')['workspace']['example']=='saved tracks'
        assert not any('fixture-password' in p.read_text() for p in tmp_path.glob('*.json'))
    finally:
        forward.stop();http.shutdown();http.server_close()
        context['loop'].call_soon_threadsafe(context['task'].cancel);thread.join(5)
