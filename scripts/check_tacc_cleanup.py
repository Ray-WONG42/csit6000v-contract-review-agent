"""Linux-only subprocess integration checks; no GPU or TACC submission needed."""
import importlib.util
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
SUPERVISOR = ROOT / 'tacc/serve/service_supervisor.py'
spec = importlib.util.spec_from_file_location('supervisor', SUPERVISOR)
supervisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supervisor)


def scenario(name, server, client=None, cancel=False, expected=0):
    with tempfile.TemporaryDirectory(prefix='clause-cleanup-test-') as tmp:
        directory = Path(tmp)
        launcher = directory / 'serve.sh'
        marker = directory / 'group'
        launcher.write_text('echo $$ > "' + str(marker) + '"\n' + server)
        command = [sys.executable, str(SUPERVISOR), str(launcher)]
        if client is not None:
            check = directory / 'check.py'
            check.write_text(client)
            command.append(str(check))
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        group = None
        try:
            deadline = time.monotonic() + 5
            while not marker.exists():
                if time.monotonic() > deadline:
                    raise AssertionError('server did not start')
                time.sleep(.05)
            group = int(marker.read_text())
            if cancel:
                time.sleep(.3)
                process.send_signal(signal.SIGTERM)
            output, _ = process.communicate(timeout=20)
            assert process.returncode == expected, output
            assert not supervisor.group_alive(group), output
            print('PASS ' + name + '\n' + output.strip())
        finally:
            if group is not None:
                supervisor.signal_group(group, signal.SIGKILL)
            if process.poll() is None:
                process.kill()
            process.wait()


if __name__ == '__main__':
    scenario('client exit kills stubborn service descendants',
             "trap '' TERM\nsleep 120 &\nwait\n",
             'import time\ntime.sleep(.5)\nraise SystemExit(7)\n', expected=7)
    scenario('cancelling standalone service cleans children',
             'sleep 120 &\nwait\n', cancel=True, expected=143)
    scenario('server failure stops waiting client', 'sleep .3\nexit 9\n',
             'import time\ntime.sleep(120)\n', expected=9)
