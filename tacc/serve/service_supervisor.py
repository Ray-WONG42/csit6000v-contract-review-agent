"""Adapted from the course CineContext cleanup patch; supports service-only jobs.

Keep the server and optional check in separate sessions and reap both groups.
Only this job's child groups are signalled; never select processes by name.
"""
import os
import signal
import subprocess
import sys
import time


def signal_group(pid, signum):
    try:
        os.killpg(pid, signum)
        return True
    except ProcessLookupError:
        return False


def group_alive(pid):
    rows = subprocess.check_output(['ps', '-axo', 'pgid=,stat=']).splitlines()
    group = str(pid).encode('ascii')
    return any(pgid == group and not state.startswith(b'Z')
               for pgid, state in (row.split() for row in rows if row.strip()))


def stop_group(process, grace):
    if process is None:
        return
    pid = process.pid
    if group_alive(pid) and signal_group(pid, signal.SIGTERM):
        print('CLAUSE_CLEANUP group={} signal=TERM'.format(pid), flush=True)
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            process.poll()
            if not group_alive(pid):
                break
            time.sleep(0.1)
        else:
            if signal_group(pid, signal.SIGKILL):
                print('CLAUSE_CLEANUP group={} signal=KILL'.format(pid), flush=True)
    process.wait(timeout=3)
    deadline = time.monotonic() + 3
    while group_alive(pid):
        if time.monotonic() >= deadline:
            raise RuntimeError('process group {} still running'.format(pid))
        time.sleep(0.1)


def main(launcher, check=None):
    stop_signal = None

    def request_stop(signum, _frame):
        nonlocal stop_signal
        stop_signal = signum

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, request_stop)
    service = client = None
    result = 1
    try:
        service = subprocess.Popen(['bash', launcher], start_new_session=True)
        if check is not None:
            client = subprocess.Popen([sys.executable, '-u', check], start_new_session=True)
        while stop_signal is None:
            if client is not None:
                result = client.poll()
                if result is not None:
                    break
            if service.poll() is not None:
                print('CLAUSE_SERVICE_EXIT {}'.format(service.returncode), flush=True)
                result = (service.returncode or 1) if client is not None else service.returncode
                break
            time.sleep(0.1)
        if stop_signal is not None:
            result = 128 + stop_signal
    finally:
        try:
            stop_group(client, grace=2)
        finally:
            stop_group(service, grace=10)
    return result if result >= 0 else 128 - result


if __name__ == '__main__':
    raise SystemExit(main(*sys.argv[1:]))
