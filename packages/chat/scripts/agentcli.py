"""pchat agent: allocate, find, and run agents with stable authenticated names."""
import argparse
import json
import os
import signal
import subprocess
import sys
import uuid
from pathlib import Path

import chatlib
import identities as ids
import receipt_experiment
import runstore


def notify(record, text, kind='status'):
    channel = record.get('channel')
    if not channel:
        return
    request = record.get('request')
    tag = '[' + kind + (' ' + request if request else '') + ']'
    message = tag + ' ' + text
    refusal = runstore.silent_refusal(record['name'], os.environ)
    if refusal:  # inside a silent run: neither posted nor queued
        print(f'pchat agent: notice withheld: {refusal}', file=sys.stderr)
        return
    cfg = chatlib.load_config()
    try:
        chatlib.post(channel, message, record['name'], cfg, cont=tag, origin='notify')
    except chatlib.Refused as err:  # the server refused it: retrying can't help; the bridge dead-letters it
        print(f'pchat agent: notice refused: {err}', file=sys.stderr)
    except chatlib.Queued:  # durably queued, or its rest handed off: the bridge posts it
        pass
    except (OSError, chatlib.ChatError) as err:  # nothing could be queued: say so, as before
        print(f'pchat agent: notice not posted: {err}', file=sys.stderr)


def run_agent(a):
    args = a.command[1:] if a.command[:1] == ['--'] else a.command
    if not args:
        raise chatlib.ChatError('agent run requires a command after --')
    record = ids.register(project=a.project, role=a.role, key='background:' + str(uuid.uuid4()),
                          parent=a.parent, channel=a.channel, request=a.request, task=a.task, brand=a.brand,
                          lifecycle='starting')
    name = record['name']
    folder = chatlib.STATE_DIR / 'agents' / name
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    ids.update(name, log_dir=str(folder))
    env = dict(os.environ, CHAT_AGENT_ID=name, CHAT_AS=name)
    if a.channel:
        env['CHAT_CHANNEL'] = a.channel
    else:
        env.pop('CHAT_CHANNEL', None)
    if a.request:
        env['CHAT_REQUEST_ID'] = a.request
    else:
        env.pop('CHAT_REQUEST_ID', None)
    child = None
    old_signals = {}
    def stop(signum, frame):
        if child and child.poll() is None:
            os.killpg(child.pid, signum)
        raise KeyboardInterrupt
    for sig in (signal.SIGTERM, signal.SIGINT):
        old_signals[sig] = signal.signal(sig, stop)
    rc, reason = 1, 'failed to launch'
    try:
        # Save structured output even on failure. Keep stdout exclusively the
        # child's output, so canonical review JSON is unchanged.
        payload = sys.stdin.buffer.read()
        child = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, env=env, start_new_session=True)
        ids.update(name, pid=child.pid, pid_start=ids.process_start(child.pid), lifecycle='running')
        notify(record, f"start {a.task or 'review'}" + (f' — launched by {a.parent}' if a.parent else ''),
               receipt_experiment.start_kind(record))
        try:
            out, err = child.communicate(payload, timeout=a.timeout)
            rc, reason = child.returncode, 'completed' if child.returncode == 0 else f'failed (exit {child.returncode})'
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            out, err = child.communicate()
            rc, reason = 124, 'timed out'
        (folder / 'stdout.log').write_bytes(out)
        (folder / 'stderr.log').write_bytes(err)
        if a.result_file and Path(a.result_file).is_file():
            (folder / 'result.json').write_bytes(Path(a.result_file).read_bytes())
        sys.stdout.buffer.write(out)
        sys.stderr.buffer.write(err)
    except KeyboardInterrupt:
        if child and child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
        if child:
            out, err = child.communicate()
            (folder / 'stdout.log').write_bytes(out)
            (folder / 'stderr.log').write_bytes(err)
        rc, reason = 130, 'interrupted'
    except OSError as e:
        (folder / 'stderr.log').write_text(str(e))
        print(f'pchat agent: {name}: command failed', file=sys.stderr)
    finally:
        for sig, handler in old_signals.items():
            signal.signal(sig, handler)
        ids.update(name, status='retired', lifecycle=reason, exit_code=rc, finished_at=ids.now())
        # The coordinator publishes the actual verdict. This line only reports
        # whether its independent reviewer process returned successfully.
        notify(record, f"{a.task or 'review'} {reason} — output: {folder}", 'status' if rc == 0 else 'blocked')
    return rc


def main(argv=None):
    ap = argparse.ArgumentParser(prog='pchat agent')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('sync')
    who = sub.add_parser('who')
    who.add_argument('--json', action='store_true')
    ls = sub.add_parser('list')
    ls.add_argument('--project')
    ls.add_argument('--all', action='store_true', help='include retired identities')
    ls.add_argument('--json', action='store_true')
    ls.add_argument('--refresh', action='store_true')
    find = sub.add_parser('find')
    find.add_argument('name')
    context = sub.add_parser('review-context')
    context.add_argument('--repo', required=True)
    context.add_argument('--json', action='store_true')
    reg = sub.add_parser('register')
    reg.add_argument('--project')
    reg.add_argument('--role', choices=ids.ROLES, required=True)
    reg.add_argument('--session', required=True, help='stable logical session key')
    reg.add_argument('--name', help='global assistant name')
    reg.add_argument('--surface')
    reg.add_argument('--json', action='store_true')
    run = sub.add_parser('run')
    run.add_argument('--project', required=True)
    run.add_argument('--role', choices=('reviewer',), default='reviewer')
    run.add_argument('--parent')
    run.add_argument('--channel')
    run.add_argument('--re', dest='request')
    run.add_argument('--task')
    run.add_argument('--brand')
    run.add_argument('--result-file', help='retain the reviewer structured-result file')
    run.add_argument('--timeout', type=float, default=1800)
    run.add_argument('command', nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    try:
        if a.cmd == 'run':
            return run_agent(a)
        cfg = chatlib.load_config()
        if a.cmd == 'who':
            r = ids.current(cfg)
            if not r:
                assistant = next((n for n, i in cfg.get('identities', {}).items()
                                  if isinstance(i, dict) and i.get('role') == 'assistant'), 'ACCOUNT')
                raise chatlib.ChatError(f'no bound agent here; outside cmux use --as {assistant} explicitly')
            print(json.dumps(r) if a.json else r['name'])
            return 0
        if a.cmd == 'sync':
            rows = ids.sync(cfg)
            print(f'{len(rows)} live agent identities synchronized')
            return 0
        if a.cmd == 'register':
            r = ids.register(project=a.project, role=a.role, key=a.session, name=a.name, surface_id=a.surface)
            print(json.dumps(r) if a.json else r['name'])
            return 0
        if a.cmd == 'review-context':
            print(json.dumps(ids.review_context(cfg, a.repo)))
            return 0
        if a.cmd == 'list' and a.refresh:
            ids.sync(cfg)
        agents = ids.read_registry()['agents']
        if a.cmd == 'find':
            if a.name not in agents:
                raise chatlib.ChatError(f'unknown identity {a.name!r}')
            print(json.dumps(agents[a.name], indent=2))
            return 0
        rows = sorted((r for r in agents.values() if (not a.project or r.get('project') == a.project)
                       and (a.all or r.get('status') == 'active')), key=lambda r: r['name'])
        if a.json:
            print(json.dumps(rows, indent=2))
        else:
            for r in rows:
                location = r.get('location') or ('background process' if r.get('parent') else 'global identity')
                print(f"{r['name']:22} {r.get('lifecycle', r['status']):18} {location}")
                if r.get('task') or r.get('log_dir'):
                    print('  ' + ' · '.join(str(r[k]) for k in ('task', 'parent', 'log_dir') if r.get(k)))
        return 0
    except (OSError, ValueError, chatlib.ChatError) as e:
        print(f'pchat agent: {e}', file=sys.stderr)
        return 2
