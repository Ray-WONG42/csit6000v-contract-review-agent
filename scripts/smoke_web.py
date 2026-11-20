"""Start an isolated API/worker and verify packaged assets + actual tool output."""
import http.cookiejar
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8790
    base = 'http://127.0.0.1:' + str(port)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    csrf = ''
    def request(path, body=None):
        value = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(base + path, data=value,
            headers={'Content-Type': 'application/json', 'X-CSRF-Token': csrf})
        with opener.open(req, timeout=5) as response:
            return response.read()
    with tempfile.TemporaryDirectory(prefix='clause-web-smoke-') as directory:
        env = {**os.environ, 'APP_DATA': directory, 'APP_DEMO': '1', 'MODEL_MODE': 'demo'}
        processes = []
        with (Path(directory) / 'server.log').open('w', encoding='utf-8') as log:
            try:
                # Refuse to exercise an unrelated existing server.
                import socket
                with socket.socket() as sock:
                    sock.bind(('127.0.0.1', port))
                for command in (['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(port)], ['-m', 'app.worker']):
                    processes.append(subprocess.Popen([sys.executable, *command], cwd=ROOT, env=env, stdout=log, stderr=log))
                deadline = time.monotonic() + 60
                while True:
                    try:
                        assert json.loads(request('/api/health'))['status'] == 'ok'
                        break
                    except OSError:
                        if time.monotonic() > deadline:
                            raise RuntimeError('API startup timed out')
                        time.sleep(.25)
                html = request('/').decode()
                for asset in re.findall(r'(?:src|href)="(/assets/[^\"]+)"', html):
                    assert request(asset)
                user = json.loads(request('/api/auth/login', {'email': 'legal@example.com', 'password': 'LegalDemo2026!'}))
                csrf = user['csrf']
                started = json.loads(request('/api/assistant/runs', {'request_key': 'clean-web-smoke', 'message': '/生成文档 Contract review demo succeeded'}))
                while time.monotonic() < deadline:
                    result = json.loads(request('/api/assistant/runs/' + started['id']))
                    if result['state'] in ('completed', 'failed'):
                        break
                    time.sleep(.3)
                assert result['state'] == 'completed', result
                assert any(e['stage'] == 'tool' for e in result['events'])
                file_id = result['result']['artifacts'][0]['file_id']
                content = request('/api/files/' + file_id)
                with zipfile.ZipFile(io.BytesIO(content)) as document:
                    assert b'Contract review demo succeeded' in document.read('word/document.xml')
                print('PASS: packaged frontend assets, login, worker, create_document tool and DOCX download; no Node required.')
            finally:
                for process in processes:
                    if process.poll() is None:
                        if os.name == 'nt':
                            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        else:
                            process.terminate()
                for process in processes:
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()


if __name__ == '__main__':
    main()
