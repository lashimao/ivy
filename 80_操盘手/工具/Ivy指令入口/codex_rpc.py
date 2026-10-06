"""Local documented Codex app-server transport; no model call on connection."""
import os
import json
import queue
import subprocess
import threading
import resource
from pathlib import Path

CLI = os.environ.get('IVY_CODEX_CLI', '/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex')

class RPC:
    def __init__(self, log):
        soft,hard=resource.getrlimit(resource.RLIMIT_NOFILE)
        desired=8192 if hard==resource.RLIM_INFINITY else min(8192,hard)
        if soft<desired:resource.setrlimit(resource.RLIMIT_NOFILE,(desired,hard))
        self.log = open(log, 'a')
        self.process = subprocess.Popen([CLI, 'app-server', '--stdio'], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=self.log, text=True, bufsize=1)
        self.events = queue.Queue()
        self.pending = {}
        self.serial = 0
        self.lock = threading.Lock()
        threading.Thread(target=self.read, daemon=True).start()
        self.call('initialize', {'clientInfo': {'name': 'ivy_router', 'version': '1.0'},
                                'capabilities': {'experimentalApi': True}})
        self.write({'method': 'initialized'})

    def write(self, value):
        with self.lock:
            self.process.stdin.write(json.dumps(value, ensure_ascii=False) + '\n')
            self.process.stdin.flush()

    def call(self, method, params, timeout=60):
        self.serial += 1
        ident = self.serial
        q = self.pending[ident] = queue.Queue()
        self.write({'id': ident, 'method': method, 'params': params})
        try:
            result = q.get(timeout=timeout)
        finally:
            self.pending.pop(ident, None)
        if 'error' in result:
            raise RuntimeError(str(result['error']))
        return result['result']

    def read(self):
        try:
            for line in self.process.stdout:
                data = json.loads(line)
                if 'id' in data and ('result' in data or 'error' in data):
                    q = self.pending.get(data['id'])
                    if q: q.put(data)
                elif 'id' in data:
                    # Never silently approve a server-side permission request.
                    self.write({'id': data['id'], 'error': {'code': -32601,
                        'message': 'Unattended Ivy does not approve permission requests'}})
                else:
                    # Ivy consumes terminal events only. Tool output, token deltas,
                    # and status notifications can be huge while HTTP collection
                    # blocks the service loop; never retain that unused stream.
                    method = data.get('method')
                    if method == 'turn/completed' or (method == 'item/completed'
                            and data.get('params', {}).get('item', {}).get('type') == 'agentMessage'):
                        self.events.put(data)
        finally:
            for q in list(self.pending.values()):
                q.put({'error': 'app-server closed'})
            self.events.put({'method': 'transport/closed'})

    def close(self):
        self.process.terminate()
        try: self.process.wait(timeout=5)
        except subprocess.TimeoutExpired: self.process.kill()
        self.log.close()
