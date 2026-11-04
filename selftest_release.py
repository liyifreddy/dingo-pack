#!/usr/bin/env python3
"""Release regression: real bounded CSV failures, signals, metadata and artifacts.
Uses isolated state on a local scratch disk; no live roster or agent config is read.
"""
import argparse
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import tempfile
import zipfile

HERE = Path(__file__).resolve().parent
TOOL = HERE / 'bin' / 'dingo-pack'

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--large-csv', action='store_true', help='670 MiB input matching the historical input scale')
    ap.add_argument('--artifacts', type=Path)
    ap.add_argument('--portable', action='store_true', help='smoke only; do not assume RLIMIT_DATA enforcement')
    args = ap.parse_args()
    loader = importlib.machinery.SourceFileLoader('dingo_release_test', str(TOOL))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    tool = importlib.util.module_from_spec(spec)
    loader.exec_module(tool)
    checks = 0
    def check(ok, label):
        nonlocal checks
        assert ok, label
        checks += 1
        print('PASS', label, flush=True)
    for raw in (-9, -11, 137, 139):
        msg = tool.memory_diagnostic(raw, 96*1024**2, 256*1024**2, 'data', False, 0)
        check('possible memory limit hit' in msg and 'does not prove OOM' in msg and 'exit signal=' in msg,
              'signal diagnostics preserve uncertainty: %s' % raw)
    check('cause unknown' in tool.memory_diagnostic(1, 1, 1, 'data', False, 0), 'ordinary exit is not diagnosed as OOM')
    check('confirmed' in tool.memory_diagnostic(-15, 1, 1, 'data', True, 2), 'tree stop is confirmed')
    with tempfile.TemporaryDirectory(prefix='dingo-release-') as tmp:
        root = Path(tmp)
        env = {k:v for k,v in os.environ.items() if not k.startswith('DINGO_')}
        env.update(DINGO_HOME=str(root/'state'), DINGO_CONFIG=str(root/'config.json'),
                   DINGO_CPU_SLOTS='2', DINGO_SAMPLE_S='0.05', DINGO_SCOPE_TAG='release-%d' % os.getpid())
        def run(*cmd):
            return subprocess.run([sys.executable, str(TOOL), 'run', '--heavy', '--threads', '1',
                                   '--mem', '32M', '--limit', '96M', '--tree-limit', '256M', '--timeout', '30s',
                                   '--', *cmd], env=env, text=True, capture_output=True, timeout=120)
        r = run(sys.executable, '-c', 'print("hello")')
        check(r.returncode == 0 and r.stdout.strip() == 'hello' and 'memory diagnostic' not in r.stderr,
              'success output and exit are preserved')
        r = run(sys.executable, '-c', 'import os,signal; os.kill(os.getpid(),signal.SIGSEGV)')
        check(r.returncode == 139 and 'SIGSEGV' in r.stderr and 'possible memory limit hit' in r.stderr,
              'real SIGSEGV returns 139 with cautious diagnosis')
        r = run(sys.executable, '-c', 'raise SystemExit(7)')
        check(r.returncode == 7 and 'cause unknown' in r.stderr, 'application error preserves exit 7')
        if args.portable:
            print('%d/%d portable checks passed' % (checks, checks))
            return
        csv = root/'large.csv'
        row = b'1,2,3,4,5,6,7,8\n'
        block = row * (1024**2 // len(row))
        size_mib = 670 if args.large_csv else 16
        with csv.open('wb') as f:
            for _ in range(size_mib): f.write(block)
        print('CSV bytes:', csv.stat().st_size, flush=True)
        r = run(sys.executable, '-c', 'import csv,sys; rows=list(csv.reader(open(sys.argv[1]))); print(len(rows))', str(csv))
        check(r.returncode != 0 and 'MemoryError' in r.stderr and 'configured per-process data limit=' in r.stderr
              and 'exit signal=none' in r.stderr and 'cause unknown' in r.stderr,
              'CSV materialization hits bounded memory and prints configuration without false certainty')
        r = run(sys.executable, '-c', 'import csv,sys; print(sum(1 for row in csv.reader(open(sys.argv[1]))))', str(csv))
        check(r.returncode == 0 and int(r.stdout.strip()) == csv.stat().st_size//len(row),
              'same CSV streams successfully under the same limit')
    if args.artifacts:
        wheels = list(args.artifacts.glob('*.whl'))
        sdists = list(args.artifacts.glob('*.tar.gz'))
        check(len(wheels) == len(sdists) == 1, 'one wheel and one sdist')
        with tarfile.open(sdists[0]) as f:
            names = {n.split('/',1)[-1] for n in f.getnames()}
            check({'ADAPTERS.md','CHANGELOG.md','RELEASING.md','LICENSE','README.md','README.zh-CN.md',
                   'selftest_release.py','pyproject.toml'}.issubset(names), 'sdist includes release documentation and tests')
        with zipfile.ZipFile(wheels[0]) as f:
            check('dingo_pack.py' in f.namelist(), 'wheel contains CLI module')
    print('%d/%d release checks passed' % (checks, checks))

if __name__ == '__main__':
    main()
