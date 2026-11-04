"""Opt-in Linux fault injection against disposable user units, never production."""
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import pytest

pytestmark = pytest.mark.skipif(os.environ.get('RUN_SYSTEMD_FAULT_TESTS') != '1',
                                reason='opt-in disposable systemd cgroup fault tests')


@pytest.mark.parametrize('outcome', ['success','error','timeout','cancel'])
def test_cgroup_reaps_escaped_term_ignoring_descendants(tmp_path, outcome):
    unit = 'ai-usage-openclaw-test-' + uuid.uuid4().hex + '.service'
    pids = tmp_path / 'pids'
    script = tmp_path / 'fault.py'
    script.write_text("""
import os,signal,sys,time
from pathlib import Path
pid=os.fork()
if pid==0:
    os.setsid()
    signal.signal(signal.SIGTERM,signal.SIG_IGN)
    child=os.fork()
    if child:
        with open(sys.argv[1],'w') as f:
            f.write(str(os.getpid())+' '+str(child));f.flush()
    while True: time.sleep(.05)
while not Path(sys.argv[1]).exists(): time.sleep(.01)
if sys.argv[2]=='success': sys.exit(0)
if sys.argv[2]=='error': sys.exit(1)
while True: time.sleep(.05)
""")
    args = ['systemd-run','--user','--no-block','--unit='+unit,'--property=Type=oneshot',
            '--property=TimeoutStartSec=2','--property=TimeoutStopSec=1',
            '--property=KillMode=control-group','--property=Delegate=no',
            sys.executable,str(script),str(pids),outcome]
    try:
        started = subprocess.run(args, capture_output=True, text=True, timeout=5)
        assert started.returncode == 0, started.stderr
        until=time.monotonic()+7
        while not pids.exists() and time.monotonic()<until: time.sleep(.02)
        assert pids.exists()
        if outcome=='cancel':
            subprocess.run(['systemctl','--user','stop',unit],capture_output=True,timeout=5)
        while time.monotonic()<until:
            result=subprocess.run(['systemctl','--user','show',unit,'-p','ActiveState','-p','ControlGroup'],
                                  capture_output=True,text=True,timeout=3).stdout
            if 'ActiveState=inactive' in result or 'ActiveState=failed' in result: break
            time.sleep(.05)
        assert 'ControlGroup=\n' in result
        assert 'ActiveState=inactive' in result or 'ActiveState=failed' in result
        for pid in map(int,pids.read_text().split()):
            until=time.monotonic()+2
            while Path('/proc',str(pid)).exists() and time.monotonic()<until: time.sleep(.02)
            assert not Path('/proc',str(pid)).exists(), 'descendant (including zombie) survived'
    finally:
        subprocess.run(['systemctl','--user','stop',unit],capture_output=True,timeout=5)
        subprocess.run(['systemctl','--user','reset-failed',unit],capture_output=True,timeout=5)
