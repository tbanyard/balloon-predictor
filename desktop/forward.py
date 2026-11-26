"""Retain a loopback HTTP origin while its SSH transport reconnects.

Only replayable password prompts are kept in RAM until the app closes. Codes,
answers to other challenges, and passwords on disk are never retained here.
"""
import asyncio
from concurrent.futures import Future
import json
import hashlib
import logging
import re
import threading
from urllib.parse import urlsplit

LOG=logging.getLogger('balloon-predictor.connection')
CONTROL='/__bp_connection/'


class SigninRequired(ValueError):
    pass


class Forward:
    def __init__(self,chooser,host,ssh_port,username,web_port):
        self.chooser,self.host,self.ssh_port,self.username,self.web_port=chooser,host,ssh_port,username,web_port
        self.history=chooser.root / ("flights-"+hashlib.sha256(f"{host}:{ssh_port}:{username}:{web_port}".encode()).hexdigest()[:24]+".json")
        self.history_lock=threading.RLock()
        self.ready=Future()
        self.signin=None
        self.loop=self.task=self.connection=None
        self.status='reconnecting'
        self.interactive=True
        self.password=None
        self.thread=threading.Thread(target=self.run,daemon=True)

    def start(self):
        self.thread.start()
        return self.ready.result(timeout=310)

    def stop(self):
        self.password=None
        if self.loop and self.task and not self.loop.is_closed():
            try:self.loop.call_soon_threadsafe(self.task.cancel)
            except RuntimeError:pass
        self.thread.join(2)

    def run(self):
        try:asyncio.run(self.tunnel())
        except asyncio.CancelledError:pass
        except Exception as error:
            LOG.error('SSH failed: %s',type(error).__name__)
            if not self.ready.done():self.ready.set_exception(error if isinstance(error,ValueError) else ValueError('SSH could not connect or sign in. Check the VPN, username and credentials.'))
        finally:self.password=None

    async def credentials(self,text,prompts):
        reusable=len(prompts)==1 and not prompts[0][1] and re.search(r'password',prompts[0][0],re.I) and not re.search(r'code|token|otp|verification',prompts[0][0],re.I)
        if reusable and self.password and not self.used_password:
            self.used_password=True
            return [self.password]
        if self.used_password:
            self.password=None
        if not self.interactive:
            self.status='signin_required'
            raise SigninRequired('Sign in again to reconnect. Your flight is still open.')
        answers=await asyncio.to_thread(self.chooser.ask,'credentials',text,prompts)
        if reusable and answers:
            self.password=answers[0]
            self.used_password=True
        return answers

    async def identity(self):
        from balloon_predictor.login import ssh_command
        from desktop.connection import verify_server
        info=await asyncio.to_thread(verify_server,self.origin)
        if not info.get('requires_ssh_identity'):
            return None
        if not self.connection:
            raise ValueError('Reconnect to the server first.')
        reply=await self.connection.run(ssh_command(info.get('identity_socket')),check=True,timeout=12)
        token=json.loads(reply.stdout).get('token')
        if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}',token):
            raise ValueError('The server could not open your personal workspace. Check its sign-in service.')
        return token

    async def tunnel(self):
        import asyncssh
        from desktop.connection import verify_server
        self.loop,self.task=asyncio.get_running_loop(),asyncio.current_task()
        self.wake=asyncio.Event()
        forward,chooser=self,self.chooser
        endpoint=f'{self.host}:{self.ssh_port}'
        class Client(asyncssh.SSHClient):
            def validate_host_public_key(self,host,addr,port,key):
                fingerprint=key.get_fingerprint('sha256')
                if not forward.interactive and endpoint not in chooser.pins:
                    forward.status='signin_required'
                    raise SigninRequired('Verify the SSH host before reconnecting.')
                try:return chooser.trust(endpoint,fingerprint)
                except ValueError as error:
                    forward.status='signin_required'
                    if not forward.ready.done():forward.ready.set_exception(error)
                    return False
            async def password_auth_requested(self):
                return (await forward.credentials(f'SSH password for {endpoint}',[('Password',False)]))[0]
            def kbdint_auth_requested(self):return ''
            async def kbdint_challenge_received(self,name,instructions,lang,prompts):
                return await forward.credentials(instructions or name or f'SSH sign-in for {endpoint}',prompts)
        # This listener outlives each SSH transport. Cookies, DOM and client
        # storage keep exactly the same origin through every recovery.
        server=await asyncio.start_server(self.handle,'127.0.0.1',0)
        self.origin=f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}'
        delay=1
        try:
            while True:
                self.used_password=False
                try:
                    self.status='reconnecting'
                    async with asyncssh.connect(self.host,port=self.ssh_port,username=self.username,config=None,
                        known_hosts=([],[],[]),client_factory=Client,client_keys=None,agent_path=None,
                        preferred_auth=('keyboard-interactive','password'),connect_timeout=300 if self.interactive else 20,login_timeout=300,
                        keepalive_interval=20,keepalive_count_max=3) as connection:
                        self.connection=connection
                        await asyncio.to_thread(verify_server,self.origin)
                        self.status='connected'
                        if not self.ready.done():
                            token=await self.identity()
                            self.signin='/signin#'+token if token else None
                            self.ready.set_result(self.origin)
                        self.interactive=False
                        delay=1
                        await connection.wait_closed()
                except asyncio.CancelledError:raise
                except Exception as error:
                    LOG.info('SSH transport interrupted: %s',type(error).__name__)
                    if not self.ready.done():
                        self.ready.set_exception(error if isinstance(error,ValueError) else ValueError('SSH could not connect or sign in. Check the VPN, username, port and credentials.'))
                        return
                    if self.status=='signin_required' or isinstance(error,(SigninRequired,asyncssh.PermissionDenied,asyncssh.HostKeyNotVerifiable,ValueError)):
                        self.status='signin_required'
                    else:self.status='reconnecting'
                finally:self.connection=None
                self.interactive=False
                self.wake.clear()
                try:
                    # Authentication which cannot be replayed waits silently
                    # until a flight action explicitly requests recovery.
                    if self.status=='signin_required':await self.wake.wait()
                    else:await asyncio.wait_for(self.wake.wait(),delay)
                except asyncio.TimeoutError:pass
                delay=min(30,delay*2)
        finally:
            self.connection=None
            server.close()
            await server.wait_closed()

    async def reply(self,writer,status,data,*,offline=False):
        body=json.dumps(data,allow_nan=False).encode()
        headers=f'HTTP/1.1 {status} Response\r\nContent-Type: application/json\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nConnection: close\r\nContent-Length: {len(body)}\r\n'
        if offline:headers+='X-Balloon-Reconnect: yes\r\n'
        writer.write(headers.encode()+b'\r\n'+body)
        await writer.drain()

    async def handle(self,reader,writer):
        remote=None
        try:
            head=await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),15)
            if len(head)>32768:return
            lines=head.decode('iso-8859-1').split('\r\n')
            method,path,version=lines[0].split(' ')
            headers=dict((k.lower(),v.strip()) for k,v in (line.split(':',1) for line in lines[1:] if ':' in line))
            if headers.get('host')!=urlsplit(self.origin).netloc or not path.startswith('/') or path.startswith('//'):
                return await self.reply(writer,403,{'detail':'Use this app connection.'})
            if path.startswith(CONTROL):
                if method=='GET' and path==CONTROL+'state':
                    return await self.reply(writer,200,{'native_ssh':True,'status':self.status,'challenge':self.chooser.state.get('challenge') if self.interactive else None})
                if method=='GET' and path==CONTROL+'flights':
                    from desktop.connection import read_json
                    with self.history_lock:
                        saved=await asyncio.to_thread(read_json,self.history)
                    return await self.reply(writer,200,saved)
                if method!='POST' or headers.get('origin')!=self.origin or headers.get('content-type','').split(';')[0]!='application/json':
                    return await self.reply(writer,403,{'detail':'Use the flight connection controls.'})
                length=int(headers.get('content-length','0'))
                maximum=32*1024**2 if path==CONTROL+'flights' else 16384
                if not 0<=length<=maximum:raise ValueError('Invalid recovery request.')
                data=json.loads(await reader.readexactly(length)) if length else {}
                action=path[len(CONTROL):]
                if action=='flights':
                    from desktop.connection import write_json
                    if not isinstance(data,dict) or not isinstance(data.get('recent'),list):
                        raise ValueError('Invalid saved flight history.')
                    with self.history_lock:
                        await asyncio.to_thread(write_json,self.history,data)
                    return await self.reply(writer,200,{'saved':True})
                if action=='recover':
                    self.interactive=True
                    self.wake.set()
                    return await self.reply(writer,200,{'status':self.status})
                if action=='answer':self.chooser.answer(data)
                elif action=='cancel':self.interactive=False;self.chooser.cancel()
                elif action=='identity':return await self.reply(writer,200,{'token':await self.identity()})
                else:return await self.reply(writer,404,{})
                return await self.reply(writer,200,{})
            connection=self.connection
            if connection is None or connection.is_closed():
                return await self.reply(writer,503,{'detail':'Reconnecting to the server. Your flight is still open.'},offline=True)
            upstream,remote=await connection.open_connection('127.0.0.1',self.web_port)
            # One response per connection makes control requests independent of
            # previously pooled HTTP connections. Bodies and downloads stream.
            clean=[lines[0]]+[line for line in lines[1:] if line and not line.lower().startswith(('connection:','proxy-connection:','keep-alive:'))]
            remote.write(('\r\n'.join(clean)+'\r\nConnection: close\r\n\r\n').encode('iso-8859-1'))
            async def upload():
                while chunk:=await reader.read(65536):
                    remote.write(chunk);await remote.drain()
            task=asyncio.create_task(upload())
            try:
                while chunk:=await upstream.read(65536):
                    writer.write(chunk);await writer.drain()
            finally:
                task.cancel()
                await asyncio.gather(task,return_exceptions=True)
        except (ValueError,KeyError,TypeError) as error:
            try:await self.reply(writer,400,{'detail':str(error)})
            except (ConnectionError,OSError):pass
        except Exception:
            # The request may have reached the server. Do not claim it is safe
            # to replay a mutation after a mid-response transport failure.
            pass
        finally:
            if remote:remote.close()
            writer.close()
            try:await writer.wait_closed()
            except (ConnectionError,OSError):pass
