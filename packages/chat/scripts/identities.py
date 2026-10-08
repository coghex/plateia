"""Stable chat identities; private credentials remain in chat/config.json.

Numbers belong to logical sessions, not tasks or surfaces. Registry writes and
account provisioning are serialized; retired names and counters are retained.
"""
from __future__ import annotations

import contextlib
import datetime
import fcntl
import json
import os
import re
import secrets
import subprocess
import tempfile
import time
from pathlib import Path

import chatlib

ROLES = ('manager', 'solver', 'reviewer', 'guide', 'assistant', 'owner')
COLORS = {'manager': '135', 'solver': '208', 'reviewer': '196', 'guide': '75',
          'assistant': '82', 'owner': '51'}
NUMBERED = {'solver', 'reviewer'}


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def registry_path():
    return chatlib.STATE_DIR / 'identities.json'


def read_registry():
    try:
        return json.loads(registry_path().read_text())
    except FileNotFoundError:
        return {'version': 1, 'counters': {}, 'agents': {}}


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@contextlib.contextmanager
def locked():
    chatlib.STATE_DIR.mkdir(parents=True, exist_ok=True)
    with (chatlib.STATE_DIR / 'identities.lock').open('a') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def prefix(cfg, project):
    try:
        p = cfg['projects'][project]
    except KeyError as e:
        raise chatlib.ChatError(f'unknown chat project {project!r}') from e
    return p['prefix'].lstrip('#').rstrip('-')


def project_for_repo(cfg, repo):
    # Exact identities only: do not attribute another owner's fork by basename.
    return next((p for p, c in cfg.get('projects', {}).items() if c.get('repo') == repo), None)


def project_for_path(cfg, path):
    resolved = Path(path).resolve()
    for p, c in cfg.get('projects', {}).items():
        root = Path(c.get('path', Path.home() / 'work' / p)).resolve()
        if resolved == root or root in resolved.parents:
            return p
    # Owner/repo worktrees use their configured repository identity.
    for p, c in cfg.get('projects', {}).items():
        repo = c.get('repo')
        if repo:
            root = (Path.home() / 'worktrees' / repo).resolve()
            if resolved == root or root in resolved.parents:
                return p
    return None


def allocate(cfg, registry, *, project, role, key, name=None, **details):
    """Pure allocation, called under the registry lock. Never reuses a number."""
    if role not in ROLES:
        raise chatlib.ChatError(f'unknown role {role!r}')
    for record in registry['agents'].values():
        if key in record.get('keys', []):
            if record['role'] != role or record.get('project') != project:
                raise chatlib.ChatError(f"{record['name']} keeps its original role and project")
            record.update({k: v for k, v in details.items() if v is not None})
            record.update(status='active', last_seen=now())
            return record
    short = prefix(cfg, project) if project else None
    if role in NUMBERED:
        counter = f'{project}:{role}'
        number = registry['counters'].get(counter, 0) + 1
        name = f'{short}-{role}-{number}'
        while name in registry['agents'] or name in cfg.get('accounts', {}):
            number += 1
            name = f'{short}-{role}-{number}'
        registry['counters'][counter] = number
    elif role in ('manager', 'guide'):
        name = f'{short}-{role}'
    elif role == 'owner':
        name = cfg['owner']
    elif not name:
        raise chatlib.ChatError('assistants require an explicit global name')
    if not name or not re.fullmatch(r'[a-z][a-z0-9-]{0,62}', name):
        raise chatlib.ChatError('invalid identity name')
    if name in registry['agents']:
        record = registry['agents'][name]
        if record['role'] != role or record.get('project') != project:
            raise chatlib.ChatError(f'{name} already belongs to another role or project')
        if role in NUMBERED:
            raise chatlib.ChatError(f'{name} already belongs to another session')
        # Singleton roles retain their name across provider restarts.
        record['keys'].append(key)
        record.update({k: v for k, v in details.items() if v is not None})
        record.update(status='active', last_seen=now())
        return record
    record = dict(name=name, project=project, role=role, keys=[key], status='active',
                  created_at=now(), last_seen=now(), **details)
    registry['agents'][name] = record
    return record


def register(*, project, role, key, name=None, **details):
    with locked():
        cfg = chatlib.load_config()
        registry = read_registry()
        existing = next((r for r in registry['agents'].values() if key in r.get('keys', [])), None)
        if not existing and role in ('manager', 'guide'):
            existing = registry['agents'].get(f'{prefix(cfg, project)}-{role}')
        if (existing and existing.get('status') == 'active' and existing.get('pid')
                and details.get('pid') and existing['pid'] != details['pid']
                and process_start(existing['pid']) == existing.get('pid_start')):
            raise chatlib.ChatError(f"{existing['name']} already has a live session")
        record = allocate(cfg, registry, project=project, role=role, key=key, name=name, **details)
        prior = cfg.get('identities', {}).get(record['name'])
        if prior and prior != {'role': role, 'project': project}:
            raise chatlib.ChatError('an existing account cannot change roles')
        # Reserve the number before network I/O. Interrupted provisioning resumes
        # with this same key; a different session never takes this number.
        atomic_json(registry_path(), registry)
        account = record['name']
        if account not in cfg['accounts']:
            cfg['accounts'][account] = secrets.token_urlsafe(32)
            atomic_json(chatlib.CONFIG_PATH, cfg)
        meta = cfg.setdefault('identities', {}).get(account)
        if not meta:
            # Existing role accounts are already registered. Newly created ones
            # must authenticate successfully before their role is made effective.
            try:
                c = chatlib.login(account, cfg)
            except chatlib.ChatError:
                chatlib.register(account, cfg['accounts'][account], cfg)
                c = chatlib.login(account, cfg)
            c.close('identity registered')
        cfg.setdefault('identities', {})[account] = {'role': role, 'project': project}
        if role == 'assistant' and account not in cfg.setdefault('assistants', []):
            cfg['assistants'].append(account)
        atomic_json(chatlib.CONFIG_PATH, cfg)
        return dict(record)


def update(name, **details):
    with locked():
        registry = read_registry()
        if name not in registry['agents']:
            raise chatlib.ChatError(f'unknown identity {name!r}')
        registry['agents'][name].update(details, last_seen=now())
        atomic_json(registry_path(), registry)


def cmux_json(*args):
    r = subprocess.run(['cmux', *args], capture_output=True, text=True, timeout=15)
    if r.returncode:
        raise chatlib.ChatError('cmux inventory unavailable')
    try:
        return json.loads(r.stdout)
    except ValueError as e:
        raise chatlib.ChatError('invalid cmux inventory') from e


def process_start(pid):
    r = subprocess.run(['ps', '-o', 'lstart=', '-p', str(pid)], capture_output=True, text=True)
    return r.stdout.strip()


def process_metadata(pid):
    """Read only open-file names: never inspect prompts or authentication data."""
    try:
        result = subprocess.run(['lsof', '-a', '-p', str(pid), '-Ffn'],
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    sessions, cwd, field = set(), None, None
    uuid = r'([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})'
    for line in result.stdout.splitlines():
        if line.startswith('f'):
            field = line[1:]
        elif line.startswith('n'):
            path = line[1:]
            if field == 'cwd':
                cwd = path
            match = re.search(r'/thread-writer-locks/' + uuid + r'\.lock$', path)
            match = match or re.search(r'/rollout-.*-' + uuid + r'\.jsonl$', path)
            if match:
                sessions.add(match[1])
    return (next(iter(sessions)) if len(sessions) == 1 else None), cwd


def walk_processes(records):
    for r in records:
        yield r
        yield from walk_processes(r.get('children', []))


def infer_role(project, workspace, surface, cfg, *, header=False, manager_surface=None):
    """A live session's role from where it is. Never from its tab title: titles
    describe the last task (a triage tab can still read "Review project
    alignment"), and a role, once assigned, is permanent."""
    if surface.lower() == (manager_surface or '').lower():
        return 'manager'
    if header:
        return 'guide'
    if re.search(r'\breview\b|reviewer|approv|\bcritic\b', workspace, re.I):
        return 'reviewer'
    return 'solver'


def manager_surface(project):
    try:
        path = Path.home() / '.local/state/project-manager' / project / 'manager.json'
        return json.loads(path.read_text()).get('surface_id')
    except (OSError, ValueError):
        return None


def started_epoch(lstart):
    try:
        return time.mktime(time.strptime(' '.join(lstart.split()), '%a %b %d %H:%M:%S %Y'))
    except ValueError:
        return None


def hook_record(sessions, pid, start):
    """The cmux hook record this live process wrote, if any.

    Process ids start over at boot, so a record counts only if a hook updated
    it after this process started; an older record with the same pid belongs
    to a process that is gone."""
    since = started_epoch(start)
    if since is None:
        return None
    found = [s for s in sessions if s.get('pid') == pid and (s.get('updated_at_unix') or 0) >= since - 2]
    return next((s for s in found if s.get('active_for_surface')), found[0] if found else None)


NODEV = 0xFFFFFFFF


def no_terminal(p):
    """cmux positively reports that this process has no controlling terminal.

    cmux credits a process to a tab by the cmux environment it inherited, so a
    detached daemon started from a tab (the Codex app-server, 2026-10-05)
    shows up in that tab beside the real client. A terminal client always has
    a terminal. Missing metadata is not evidence: such a process stays a
    candidate, as before."""
    if 'tty_device' not in p and 'tpgid' not in p:
        return False
    tpgid = p.get('tpgid')
    return p.get('tty_device') in (None, -1, NODEV) and not (isinstance(tpgid, int) and tpgid > 0)


def foreground(p, surface):
    pgid = p.get('pgid')
    return pgid is not None and (pgid == p.get('tpgid') or pgid in (surface.get('foreground_pgids') or []))


def inventory(cfg, unplaced=None):
    """Only return live top-level agents. Never retain launch args or MCP tokens.

    cmux places processes in tabs by tty. After cmux relaunches, a tab can keep
    a tty that now belongs to another tab's shell, so an agent can be credited
    to the wrong tab, or to two, while its own tab shows nothing (2026-10-03:
    resumed managers were registered again as solvers in idle tabs). An
    agent's hook record names the tab it really runs in, so a record this
    process wrote decides its tab; tty placement is only the fallback.

    A live agent whose tab cannot be decided (no fresh hook record, and tabs
    in different groups still claim it) is not returned; its (pid, start) is
    added to `unplaced` so the caller neither registers nor retires it.
    """
    top = cmux_json('--json', '--id-format', 'both', 'top', '--all', '--processes')
    groups = cmux_json('--json', 'workspace-group', 'list').get('groups', [])
    sessions = cmux_json('sessions', 'list', '--json', '--limit', '500').get('sessions', [])
    group_of = {ref: g for g in groups for ref in g.get('member_workspace_refs', [])}
    places, seen = {}, {}
    for w in top.get('windows', []):
        for ws in w.get('workspaces', []):
            group = group_of.get(ws['ref'], {})
            for pi, pane in enumerate(ws.get('panes', [])):
                for ti, surface in enumerate(pane.get('surfaces', [])):
                    place = (ws, group, pi, pane, ti, surface)
                    places[surface['id'].lower()] = place
                    procs = list(walk_processes(surface.get('processes', [])))
                    live = [p for p in procs if (p.get('name') == 'codex' or re.fullmatch(r'\d+\.\d+\.\d+', p.get('name', '')))
                            and not no_terminal(p)]
                    if not live:
                        continue
                    # A nested headless reviewer does not own its parent's surface.
                    all_by_pid = {p['pid']: p for p in procs}
                    def depth(p):
                        seen_pids = set()
                        while p.get('ppid') in all_by_pid and p['ppid'] not in seen_pids:
                            seen_pids.add(p['ppid'])
                            p = all_by_pid[p['ppid']]
                        return len(seen_pids)
                    # Fewest ancestors first; the tab's foreground client breaks ties.
                    agent = min(live, key=lambda p: (depth(p), not foreground(p, surface)))
                    seen.setdefault(agent['pid'], []).append((place, agent))
    rows = []
    for pid, sightings in seen.items():
        start = process_start(pid)
        s = hook_record(sessions, pid, start) or {}
        agent = sightings[0][1]
        # The hook record's tab, even when cmux credits this process to another
        # tab or to none. Without a record, a tab whose cmux environment the
        # process inherited outranks tty-only sightings; then the first tab
        # that shows it, unless the remaining tabs are in different groups.
        place = places.get((s.get('surface_id') or '').lower())
        if not place:
            env = [x for x in sightings if (x[1].get('cmux_surface_id') or '').lower() == x[0][5]['id'].lower()]
            candidates = env or sightings
            if len({x[0][1].get('name') for x in candidates}) > 1:
                if unplaced is not None:
                    unplaced.add((pid, start))
                continue
            place = candidates[0][0]
        ws, group, pi, pane, ti, surface = place
        project = group.get('name')
        if project not in cfg.get('projects', {}):
            continue
        # Working project wins for a worker moved into another group;
        # singleton header roles remain anchored to their project.
        header = ws['ref'] == group.get('anchor_workspace_ref')
        session = s.get('session_id')
        cwd = s.get('launch_working_directory') or s.get('cwd')
        if not session or not cwd:
            open_session, open_cwd = process_metadata(pid)
            session, cwd = session or open_session, cwd or open_cwd
        actual = project if header else (project_for_path(cfg, cwd or '/') or project)
        role = infer_role(actual, ws.get('title', ''), surface['id'], cfg,
                          header=header, manager_surface=manager_surface(project))
        brand = 'codex' if agent.get('name') == 'codex' else 'claude'
        # Standing issue-approval tab: the second Codex session in
        # the left pane; extra Claude tabs do not change its role.
        if not header and ws.get('title', '').endswith(' issue') and pi == 0 and brand == 'codex':
            previous_codex = any(any(p.get('name') == 'codex' for p in walk_processes(x.get('processes', [])))
                                 for x in pane.get('surfaces', [])[:ti])
            if previous_codex:
                role = 'reviewer'
        key = f'{brand}:{session}' if session else f'process:{pid}:{start}'
        rows.append(dict(project=actual, role=role, key=key, session_id=session,
                         surface_id=surface['id'], workspace_id=ws['id'],
                         location=f"{ws.get('title', '')} / pane {pi + 1} / tab {ti + 1}",
                         brand=brand, pid=pid, pid_start=start,
                         lifecycle=s.get('agent_lifecycle', 'running')))
    return rows


def weak(record):
    """Known only by a process or launch, never by a provider session."""
    return all(k.startswith(('process:', 'launch:')) for k in record.get('keys', []))


def sync(cfg=None):
    cfg = cfg or chatlib.load_config()
    unplaced = set()
    rows = inventory(cfg, unplaced)
    registry = read_registry()
    if not rows and any(r.get('surface_id') and r.get('status') == 'active' for r in registry['agents'].values()):
        raise chatlib.ChatError('empty cmux process inventory; existing identities preserved')
    records = []
    for row in rows:
        # Preserve an established role even if a title, task, or location changes.
        # The session key decides first: a weak duplicate of the same process
        # must never win over the identity that owns the session.
        agents = registry['agents'].values()
        old = (next((r for r in agents if row['key'] in r.get('keys', [])), None)
               or next((r for r in agents if r.get('pid') == row['pid']
                        and r.get('pid_start') == row['pid_start']), None))
        if old:
            row['role'], row['project'] = old['role'], old.get('project')
            if row['key'] not in old.get('keys', []):
                with locked():
                    fresh = read_registry()
                    fresh['agents'][old['name']]['keys'].append(row['key'])
                    atomic_json(registry_path(), fresh)
        records.append(register(**row))
    # A process registered under a weak key before its session was known is
    # the same agent as the identity that now holds its process: retire the
    # duplicate, or it lingers as long as the process lives.
    holder = {(row['pid'], row['pid_start']): rec['name'] for row, rec in zip(rows, records)}
    for r in read_registry()['agents'].values():
        held = holder.get((r.get('pid'), r.get('pid_start')))
        if r.get('status') == 'active' and held and held != r['name'] and weak(r):
            update(r['name'], status='retired', lifecycle=f'duplicate of {held}')
    # Only retire interactive identities on a successful complete inventory.
    # A live agent whose tab is ambiguous keeps its existing identity as is.
    live = {(r['pid'], r['pid_start']) for r in rows} | unplaced
    for r in read_registry()['agents'].values():
        if (r.get('status') == 'active' and r.get('pid')
                and ((r.get('surface_id') and (r['pid'], r.get('pid_start')) not in live)
                     or (not r.get('surface_id') and process_start(r['pid']) != r.get('pid_start')))):
            update(r['name'], status='retired', lifecycle='exited')
    return records


def current(cfg, *, discover=True):
    name = os.environ.get('CHAT_AGENT_ID')
    if name:
        record = read_registry()['agents'].get(name)
        if not record or name not in cfg.get('identities', {}):
            raise chatlib.ChatError('invalid CHAT_AGENT_ID')
        return record
    sid = os.environ.get('CMUX_SURFACE_ID', '').lower()
    if not sid:
        return None
    if discover:
        sync(cfg)
    rows = [r for r in read_registry()['agents'].values() if (r.get('surface_id') or '').lower() == sid
            and r.get('status') == 'active']
    return rows[0] if len(rows) == 1 else None


def resolve(cfg, requested=None, *, discover=True):
    if not cfg.get('agent_identities_enabled'):
        return requested or os.environ.get('CHAT_AS')
    record = current(cfg, discover=discover)
    cfg = chatlib.load_config()  # discovery may provision a new account
    if record:
        name = record['name']
        # Older prompts still say --as claude/codex or --as <project>-worker; they mean "me".
        def legacy(n):
            return n in ('codex', 'claude') or n.endswith('-worker')
        inherited = os.environ.get('CHAT_AS')
        if requested and requested != name and not legacy(requested):
            raise chatlib.ChatError(f'you are {name}; cannot post as {requested}')
        if not requested and inherited and inherited != name and not legacy(inherited):
            raise chatlib.ChatError(f'you are {name}; inherited CHAT_AS belongs to another agent')
        return name
    name = requested or os.environ.get('CHAT_AS')
    if name in {'codex', 'claude'}:
        # An unbound cmux worker must not acquire global assistant authority.
        if os.environ.get('CMUX_SURFACE_ID'):
            raise chatlib.ChatError('no unique chat identity for this surface; run pchat agent sync')
        name = cfg.get('identity_aliases', {}).get(name, name)
    if name and name.endswith('-worker'):
        raise chatlib.ChatError('shared worker identity retired; bind a session with pchat agent register')
    return name


def remember(name, channel, request=None):
    if name in read_registry()['agents']:
        update(name, channel=channel, request=request)


def review_context(cfg, repo):
    if not cfg.get('agent_identities_enabled'):
        return {'enabled': False}
    project = project_for_repo(cfg, repo)
    if not project:
        return {'enabled': False}
    parent = current(cfg)
    p = cfg['projects'][project]
    channel = os.environ.get('CHAT_CHANNEL')
    request = os.environ.get('CHAT_REQUEST_ID')
    if parent and parent.get('project') == project:
        channel = channel or parent.get('channel')
        request = request or parent.get('request')
    if not channel:
        channel = p['prefix'] + 'reviews'  # background jobs get a project request channel
    valid = channel == p['channel'] or channel.startswith(p['prefix'])
    if not valid:
        raise chatlib.ChatError('review channel does not belong to the reviewed project')
    return dict(enabled=True, project=project, parent=parent['name'] if parent else None,
                channel=channel, request=request)


def prepare_launch(args, brand, role=None, project=None):
    """Called before modelclass exec; the provider keeps the launcher PID."""
    cfg = chatlib.load_config()
    if not cfg.get('agent_identities_enabled'):
        return None
    if os.environ.get('CHAT_AGENT_ID'):
        return current(cfg, discover=False)
    sid = os.environ.get('CMUX_SURFACE_ID')
    project = project or os.environ.get('CHAT_PROJECT') or project_for_path(cfg, Path.cwd())
    if not project or (not sid and not role):
        return None
    text = ' '.join(args)
    role = role or os.environ.get('CHAT_ROLE')
    if not role:
        if re.search(r'[/\$]project-manager\b', text):
            role = 'manager'
        elif re.search(r'[/\$]guide\b', text):
            role = 'guide'
        elif re.search(r'[/\$](?:[a-z-]+:)?(?:pr-review|pr-rereview|issue-review|approve-issue|project-review|art-critic)\b', text):
            role = 'reviewer'
        else:
            # New empty header surfaces have no provider process yet.
            groups = cmux_json('--json', 'workspace-group', 'list').get('groups', []) if sid else []
            group = next((g for g in groups if g['name'] == project), {})
            top = cmux_json('--json', '--id-format', 'both', 'top', '--all') if sid else {}
            role = 'solver'
            for w in top.get('windows', []):
                for ws in w.get('workspaces', []):
                    for pane in ws.get('panes', []):
                        for s in pane.get('surfaces', []):
                            if s['id'].lower() == sid.lower() and ws['ref'] == group.get('anchor_workspace_ref'):
                                role = 'manager' if pane == ws['panes'][0] else 'guide'
    session = None
    for i, arg in enumerate(args[:-1]):
        if arg in ('--resume', 'resume') and not args[i + 1].startswith('-'):
            session = args[i + 1]
    if session:
        key = f'{brand}:{session}'
        old = next((r for r in read_registry()['agents'].values() if key in r.get('keys', [])), None)
        if old:
            role, project = old['role'], old.get('project')
    else:
        key = 'launch:' + secrets.token_hex(16)
    # A childrun launch exports its run id (launch-worker --run-id): recorded on the process that
    # modelclass execs into the provider, it is the run's positive launch evidence (childrun claim).
    run_id = os.environ.get('CHAT_RUN_ID', '')
    run_id = run_id if re.fullmatch(r'run-\d{8}T\d{6}Z-[0-9a-f]{12}', run_id) else None
    record = register(project=project, role=role, key=key, surface_id=sid, session_id=session,
                      brand=brand, pid=os.getpid(), pid_start=process_start(os.getpid()), lifecycle='starting',
                      run_id=run_id)
    os.environ.update(CHAT_AGENT_ID=record['name'], CHAT_AS=record['name'])
    return record
