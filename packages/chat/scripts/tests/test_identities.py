"""Identity allocation, attribution, colors, and headless-process lifecycle."""
import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import multiprocessing
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolation  # first: sandbox home and live-state guard
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chatlib
import identities as ids
import agentcli
import role_colors
_isolation.check_bound(chatlib, ids, agentcli, role_colors)


def allocate_worker(key):
    ids.register(project='alpha', role='solver', key=key)


class RegistryCase(unittest.TestCase):
    """A private chat config and identity registry; no tests of its own."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cfg = {'owner': 'pat', 'assistants': ['sam'], 'agent_identities_enabled': True,
                    'identity_aliases': {'codex': 'sam', 'claude': 'sam'},
                    'accounts': {'pat': 'x', 'sam': 'x', 'alp-manager': 'x', 'alp-worker': 'x'},
                    'projects': {'alpha': {'prefix': '#alp-', 'channel': '#alpha',
                                             'manager': 'alp-manager', 'worker': 'alp-worker',
                                             'repo': 'owner/alpha'}}}
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps(self.cfg))
        self.patches = [mock.patch.object(chatlib, 'CONFIG_PATH', self.config),
                        mock.patch.object(chatlib, 'STATE_DIR', self.root / 'state'),
                        mock.patch.object(chatlib, 'login', return_value=mock.Mock()),
                        # process evidence is faked: the isolation guard refuses ps and lsof
                        mock.patch.object(ids, 'process_start', lambda pid: f'fake-start-{pid}'),
                        mock.patch.dict(os.environ, {}, clear=True)]
        for p in self.patches:
            p.start()
        self.addCleanup(self.temp.cleanup)
        for p in self.patches:
            self.addCleanup(p.stop)

    def register(self, key='session-1', role='solver', **details):
        return ids.register(project='alpha', role=role, key=key, **details)


class IdentityTests(RegistryCase):
    def test_same_session_keeps_identity_when_task_or_location_changes(self):
        a = self.register(task='issue 1', surface_id='a')
        b = self.register(task='issue 2', surface_id='b')
        self.assertEqual(a['name'], b['name'])
        self.assertEqual(b['surface_id'], 'b')

    def test_retired_numbers_never_recycled_and_roles_count_separately(self):
        a = self.register()
        ids.update(a['name'], status='retired')
        self.assertEqual(self.register('session-2')['name'], 'alp-solver-2')
        self.assertEqual(self.register('review-1', 'reviewer')['name'], 'alp-reviewer-1')

    def test_role_change_refused(self):
        self.register()
        with self.assertRaisesRegex(chatlib.ChatError, 'original role'):
            self.register(role='reviewer')

    def test_concurrent_number_allocation_is_unique(self):
        ctx = multiprocessing.get_context('fork')
        processes = [ctx.Process(target=allocate_worker, args=(f's-{i}',)) for i in range(8)]
        for p in processes:
            p.start()
        for p in processes:
            p.join(10)
            self.assertEqual(p.exitcode, 0)
        registry = ids.read_registry()
        self.assertEqual(set(registry['agents']), {f'alp-solver-{i}' for i in range(1, 9)})
        self.assertEqual(registry['counters']['alpha:solver'], 8)
        self.assertEqual(len(chatlib.load_config()['accounts']), 12)

    def test_failed_provisioning_reserves_number(self):
        with mock.patch.object(chatlib, 'login', side_effect=OSError('offline')):
            with self.assertRaises(OSError):
                self.register()
        self.assertEqual(self.register('new')['name'], 'alp-solver-2')
        self.assertEqual(self.register()['name'], 'alp-solver-1')

    def test_credentials_are_private_and_absent_from_registry(self):
        self.register()
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(ids.registry_path().stat().st_mode & 0o777, 0o600)
        pwd = chatlib.load_config()['accounts']['alp-solver-1']
        self.assertNotIn(pwd, ids.registry_path().read_text())

    def test_worker_legacy_brand_redirects_to_own_identity(self):
        r = self.register()
        cfg = chatlib.load_config()
        with mock.patch.object(ids, 'current', return_value=r):
            self.assertEqual(ids.resolve(cfg, 'codex'), 'alp-solver-1')
            self.assertEqual(ids.resolve(cfg, 'alp-worker'), 'alp-solver-1')
            with self.assertRaises(chatlib.ChatError):
                ids.resolve(cfg, 'sam')
            with self.assertRaises(chatlib.ChatError):
                ids.resolve(cfg, 'alp-manager')

    def test_unbound_surface_cannot_fall_back_to_assistant(self):
        with mock.patch.dict(os.environ, {'CMUX_SURFACE_ID': 'unknown'}), mock.patch.object(ids, 'current', return_value=None):
            with self.assertRaisesRegex(chatlib.ChatError, 'no unique'):
                ids.resolve(self.cfg, 'codex')
        with mock.patch.object(ids, 'current', return_value=None):
            self.assertEqual(ids.resolve(self.cfg, 'codex'), 'sam')

    def test_background_identity_overrides_inherited_surface(self):
        r = self.register('bg', 'reviewer')
        with mock.patch.dict(os.environ, {'CHAT_AGENT_ID': r['name'], 'CMUX_SURFACE_ID': 'solver'}), mock.patch.object(ids, 'sync') as sync:
            self.assertEqual(ids.current(chatlib.load_config())['name'], 'alp-reviewer-1')
            sync.assert_not_called()

    def test_review_context_requires_exact_repository_and_valid_channel(self):
        self.assertFalse(ids.review_context(self.cfg, 'another/alpha')['enabled'])
        r = self.register(channel='#alp-issue-27', request='alp-request')
        with mock.patch.object(ids, 'current', return_value=r):
            value = ids.review_context(self.cfg, 'owner/alpha')
            self.assertEqual(value['channel'], '#alp-issue-27')
            self.assertEqual(value['parent'], r['name'])
            self.assertEqual(value['request'], 'alp-request')
            with mock.patch.dict(os.environ, {'CHAT_CHANNEL': '#other-project'}):
                with self.assertRaises(chatlib.ChatError):
                    ids.review_context(self.cfg, 'owner/alpha')

    def test_singletons_and_global_assistant(self):
        self.assertEqual(self.register('mgr1', 'manager')['name'], 'alp-manager')
        self.assertEqual(self.register('mgr2', 'manager')['name'], 'alp-manager')
        self.assertEqual(self.register('guide1', 'guide')['name'], 'alp-guide')
        a = ids.register(project=None, role='assistant', key='sam-global', name='sam')
        self.assertEqual(a['name'], 'sam')

    def test_live_singleton_cannot_be_replaced(self):
        self.register('mgr', 'manager', pid=123, pid_start='start')
        with mock.patch.object(ids, 'process_start', return_value='start'):
            with self.assertRaisesRegex(chatlib.ChatError, 'live session'):
                self.register('mgr-other', 'manager', pid=456, pid_start='other')
        with mock.patch.object(ids, 'process_start', return_value=''):
            self.assertEqual(self.register('mgr-restart', 'manager', pid=456, pid_start='other')['name'], 'alp-manager')

    def test_explicit_resume_recovers_identity_and_role(self):
        r = self.register('codex:provider-id', 'reviewer')
        with mock.patch.dict(os.environ, {'CMUX_SURFACE_ID': 'new-surface'}):
            value = ids.prepare_launch(['resume', 'provider-id'], 'codex', role='solver', project='alpha')
        self.assertEqual(value['name'], r['name'])
        self.assertEqual(value['role'], 'reviewer')
        self.assertEqual(value['surface_id'], 'new-surface')

    def test_a_childrun_launch_records_its_run_id_on_the_launched_process(self):
        run = 'run-20261006T120000Z-0123456789ab'
        with mock.patch.dict(os.environ, {'CMUX_SURFACE_ID': 'tab-1', 'CHAT_RUN_ID': run}):
            value = ids.prepare_launch(['$backlog'], 'codex', role='solver', project='alpha')
        self.assertEqual((value['run_id'], value['pid'], value['pid_start']),
                         (run, os.getpid(), f'fake-start-{os.getpid()}'))
        self.assertTrue(value['keys'][0].startswith('launch:'))
        for bad in ('run-1', run + 'x', 'run-20261006T120000Z-0123456789ab; x'):
            with self.subTest(bad=bad), mock.patch.dict(os.environ, {'CMUX_SURFACE_ID': 'tab-2', 'CHAT_RUN_ID': bad,
                                                                     'CHAT_AGENT_ID': ''}):
                os.environ.pop('CHAT_AGENT_ID')
                value = ids.prepare_launch(['$backlog'], 'codex', role='solver', project='alpha')
                self.assertIsNone(value.get('run_id'))

    def test_open_file_discovery_recovers_session_without_reading_contents(self):
        session = '01a0f840-c0fc-7df3-90b1-0f8d713ccabd'
        files = f'fcwd\nn/work/alpha\nf12\nn/private/thread-writer-locks/{session}.lock\n'
        with mock.patch.object(ids.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, files, '')) as run:
            self.assertEqual(ids.process_metadata(123), (session, '/work/alpha'))
            self.assertEqual(run.call_args.args[0], ['lsof', '-a', '-p', '123', '-Ffn'])

    def test_incomplete_inventory_does_not_retire_existing_sessions(self):
        self.register(surface_id='live', pid=123, pid_start='start')
        with mock.patch.object(ids, 'inventory', return_value=[]):
            with self.assertRaisesRegex(chatlib.ChatError, 'preserved'):
                ids.sync()
        self.assertEqual(ids.read_registry()['agents']['alp-solver-1']['status'], 'active')

    def test_requested_colors(self):
        with mock.patch.object(role_colors, 'CONFIG', self.config):
            for nick, role, color in [('pat','owner','51'), ('sam','assistant','82'),
                                      ('bet-manager','manager','135'), ('alp-guide','guide','75'),
                                      ('alp-solver-20','solver','208'), ('alp-reviewer-3','reviewer','196')]:
                self.assertEqual(role_colors.role_for_nick(nick), role)
                self.assertEqual(role_colors.COLORS[role], color)

    def args(self, code, timeout=5):
        return argparse.Namespace(project='alpha', role='reviewer', parent='alp-solver-7',
                                  channel='#alp-review-tests', request='req', task='PR #45', brand='codex',
                                  timeout=timeout, result_file=None, command=[sys.executable, '-c', code])

    def streams(self):
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(sys, 'stdin', io.TextIOWrapper(io.BytesIO(b'prompt'))))
        output, error = io.BytesIO(), io.BytesIO()
        stack.enter_context(mock.patch.object(sys, 'stdout', io.TextIOWrapper(output)))
        stack.enter_context(mock.patch.object(sys, 'stderr', io.TextIOWrapper(error)))
        stack.enter_context(mock.patch.object(agentcli, 'notify'))
        return stack

    def test_background_process_gets_own_name_and_retained_output(self):
        code = 'import os,sys; print(os.environ["CHAT_AS"]); print(sys.stdin.read(), file=sys.stderr)'
        with self.streams():
            rc = agentcli.run_agent(self.args(code))
        self.assertEqual(rc, 0)
        r = ids.read_registry()['agents']['alp-reviewer-1']
        self.assertEqual(r['status'], 'retired')
        self.assertEqual(r['parent'], 'alp-solver-7')
        self.assertEqual((Path(r['log_dir']) / 'stdout.log').read_text(), 'alp-reviewer-1\n')
        self.assertEqual((Path(r['log_dir']) / 'stderr.log').read_text(), 'prompt\n')

    def test_background_timeout_retires_and_kills_process(self):
        with self.streams():
            rc = agentcli.run_agent(self.args('import time; time.sleep(20)', timeout=0.05))
        self.assertEqual(rc, 124)
        r = ids.read_registry()['agents']['alp-reviewer-1']
        self.assertEqual(r['lifecycle'], 'timed out')
        with self.assertRaises(ProcessLookupError):
            os.kill(r['pid'], 0)

    def test_failed_background_command_retained_and_retired(self):
        with self.streams():
            rc = agentcli.run_agent(self.args('import sys; print("failure",file=sys.stderr); sys.exit(3)'))
        self.assertEqual(rc, 3)
        r = ids.read_registry()['agents']['alp-reviewer-1']
        self.assertEqual(r['exit_code'], 3)
        self.assertEqual(r['status'], 'retired')


class RelaunchAttributionTests(RegistryCase):
    """After cmux relaunches, a tab can keep a tty that now belongs to another
    tab's shell, so cmux credits an agent to the wrong tab (2026-10-03)."""
    START = 'Sat Oct  3 11:00:53 2026'

    def setUp(self):
        super().setUp()
        self.start_epoch = ids.started_epoch(self.START)
        for target, value in [('process_start', lambda pid: self.START if pid == 25759 else ''),
                              ('process_metadata', lambda pid: (None, '/work/alpha')),
                              ('manager_surface', lambda project: 'HEADER-LEFT')]:
            p = mock.patch.object(ids, target, side_effect=value)
            p.start()
            self.addCleanup(p.stop)

    def cmux(self, *, record_age=10.0, stale_tab_has_agent=True, header_has_agent=False):
        """The header's left tab runs the manager (pid 25759); cmux credits it
        to an idle tab in the design workspace instead."""
        claude = {'pid': 25759, 'name': '2.1.288', 'ppid': 1}
        def tab(sid, has_agent):
            return {'id': sid, 'processes': [{'pid': 1, 'name': 'login', 'children': [claude]}] if has_agent else []}
        top = {'windows': [{'workspaces': [
            {'ref': 'ws:1', 'id': 'WS-HEADER', 'title': 'Group 2',
             'panes': [{'surfaces': [tab('HEADER-LEFT', header_has_agent)]}]},
            {'ref': 'ws:2', 'id': 'WS-DESIGN', 'title': 'alpha design',
             'panes': [{'surfaces': [tab('DESIGN-IDLE', stale_tab_has_agent)]}]}]}]}
        groups = {'groups': [{'name': 'alpha', 'anchor_workspace_ref': 'ws:1',
                              'member_workspace_refs': ['ws:1', 'ws:2']}]}
        sessions = {'sessions': [{'pid': 25759, 'session_id': 'manager-session', 'surface_id': 'HEADER-LEFT',
                                  'active_for_surface': True, 'cwd': '/work/alpha',
                                  'updated_at_unix': self.start_epoch + record_age,
                                  'agent_lifecycle': 'idle'}]}
        answers = {'top': top, 'workspace-group': groups, 'sessions': sessions}
        def fake(*args):
            return next(v for k, v in answers.items() if k in args)
        return mock.patch.object(ids, 'cmux_json', side_effect=fake)

    def test_hook_record_places_agent_in_its_own_tab(self):
        with self.cmux():
            rows = ids.inventory(chatlib.load_config())
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row['surface_id'], row['workspace_id']), ('HEADER-LEFT', 'WS-HEADER'))
        self.assertEqual((row['role'], row['key']), ('manager', 'claude:manager-session'))
        self.assertTrue(row['location'].startswith('Group 2 /'))

    def test_agent_credited_to_two_tabs_is_one_agent(self):
        with self.cmux(header_has_agent=True):
            rows = ids.inventory(chatlib.load_config())
        self.assertEqual([r['surface_id'] for r in rows], ['HEADER-LEFT'])

    def test_record_from_before_this_process_started_is_ignored(self):
        # Pids start over at boot: a pre-crash record can carry a reused pid.
        with self.cmux(record_age=-3600):
            rows = ids.inventory(chatlib.load_config())
        self.assertEqual(rows[0]['surface_id'], 'DESIGN-IDLE')
        self.assertEqual(rows[0]['key'], f'process:25759:{self.START}')

    def test_resumed_manager_is_not_registered_again(self):
        self.register('claude:manager-session', 'manager', surface_id='PRE-CRASH', pid=111, pid_start='old')
        with self.cmux():
            ids.sync()
        agents = ids.read_registry()['agents']
        self.assertEqual(set(agents), {'alp-manager'})
        m = agents['alp-manager']
        self.assertEqual((m['status'], m['surface_id'], m['pid']), ('active', 'HEADER-LEFT', 25759))

    def test_weak_duplicate_is_retired_once_the_session_is_known(self):
        # The phantom a misattributed sync made, then the real identity.
        phantom = self.register(f'process:25759:{self.START}', surface_id='DESIGN-IDLE',
                                pid=25759, pid_start=self.START)
        self.register('claude:manager-session', 'manager', surface_id='PRE-CRASH', pid=111, pid_start='old')
        with self.cmux():
            ids.sync()
        agents = ids.read_registry()['agents']
        self.assertEqual(agents[phantom['name']]['status'], 'retired')
        self.assertEqual(agents[phantom['name']]['lifecycle'], 'duplicate of alp-manager')
        # The session's owner won, though the phantom was registered first.
        self.assertEqual((agents['alp-manager']['status'], agents['alp-manager']['pid']), ('active', 25759))

    def test_launch_identity_learns_its_session_and_stays(self):
        # modelclass registers a launch key before the provider has a session.
        launched = self.register('launch:abc', 'manager', surface_id='HEADER-LEFT',
                                 pid=25759, pid_start=self.START)
        with self.cmux(stale_tab_has_agent=False, header_has_agent=True):
            ids.sync()
        r = ids.read_registry()['agents'][launched['name']]
        self.assertEqual(r['status'], 'active')
        self.assertIn('claude:manager-session', r['keys'])


class DiscoveryEvidenceTests(RegistryCase):
    """Fixtures follow cmux's own per-process metadata (2026-10-06): the Codex
    app-server daemon inherits a tab's cmux environment, so cmux lists it in
    that tab beside the real client, with no controlling terminal."""
    START = 'Mon Oct  5 23:20:00 2026'
    GUIDE_SESSION = '01a0f864-95ee-7813-a868-5250c77a9298'
    TTY = 268435488

    def setUp(self):
        super().setUp()
        self.cfg['projects']['beta'] = {'prefix': '#bet-', 'channel': '#beta',
                                               'repo': 'owner/beta'}
        self.config.write_text(json.dumps(self.cfg))
        self.sessions = {}
        self.hooks = []
        for target, value in [('process_start', lambda pid: self.START),
                              ('process_metadata', lambda pid: (self.sessions.get(pid), '/elsewhere')),
                              ('manager_surface', lambda project: 'MANAGER-TAB')]:
            p = mock.patch.object(ids, target, side_effect=value)
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def proc(pid, name='codex', *, ppid=1, surface=None, tty=True, pgid=None, tpgid=None, children=()):
        pgid = pgid or pid
        p = {'pid': pid, 'ppid': ppid, 'name': name, 'pgid': pgid, 'children': list(children),
             'tty_device': DiscoveryEvidenceTests.TTY if tty else ids.NODEV,
             'tpgid': (tpgid or pgid) if tty else None}
        if surface:
            p['cmux_surface_id'] = surface
        return p

    def daemon(self, surface):
        return self.proc(26732, surface=surface, tty=False,
                         children=[self.proc(41694, ppid=26732, surface=surface, tty=False)])

    def cmux(self, tabs):
        """tabs: (group, header?, surface id, layout tty, foreground pgids, processes)."""
        workspaces = {}
        for group, header, sid, tty, fg, procs in tabs:
            ref = f'ws:{group}:{"header" if header else "work"}'
            ws = workspaces.setdefault(ref, {'ref': ref, 'id': ref.upper(), 'title': 'Group' if header else f'{group} solve',
                                             'panes': [{'surfaces': []}]})
            ws['panes'][0]['surfaces'].append({'id': sid, 'tty': tty, 'foreground_pgids': fg, 'processes': procs})
        groups = [{'name': g, 'anchor_workspace_ref': f'ws:{g}:header',
                   'member_workspace_refs': [f'ws:{g}:header', f'ws:{g}:work']}
                  for g in ('alpha', 'beta')]
        answers = {'top': {'windows': [{'workspaces': list(workspaces.values())}]},
                   'workspace-group': {'groups': groups}, 'sessions': {'sessions': self.hooks}}
        return mock.patch.object(ids, 'cmux_json', side_effect=lambda *a: next(v for k, v in answers.items() if k in a))

    def guide_tab(self):
        self.sessions[66022] = self.GUIDE_SESSION
        guide = self.proc(66022, ppid=24436, surface='GUIDE-TAB')
        return ('alpha', True, 'GUIDE-TAB', 'ttys032', [66022], [self.daemon('GUIDE-TAB'), guide])

    def inventory(self, tabs, unplaced=None):
        with self.cmux(tabs):
            return ids.inventory(chatlib.load_config(), unplaced)

    def test_detached_daemon_is_not_the_tabs_agent(self):
        rows = self.inventory([self.guide_tab()])
        self.assertEqual([(r['pid'], r['role'], r['key']) for r in rows],
                         [(66022, 'guide', f'codex:{self.GUIDE_SESSION}')])

    def test_sync_keeps_the_live_guide_and_gives_the_daemon_no_identity(self):
        # Before: the daemon was chosen, and the guide's live-session guard
        # aborted the whole sync ("alp-guide already has a live session").
        self.register(f'codex:{self.GUIDE_SESSION}', 'guide', surface_id='GUIDE-TAB',
                      pid=66022, pid_start=self.START)
        with self.cmux([self.guide_tab()]):
            ids.sync()
        agents = ids.read_registry()['agents']
        self.assertEqual(set(agents), {'alp-guide'})
        self.assertEqual((agents['alp-guide']['status'], agents['alp-guide']['pid']), ('active', 66022))

    def test_a_tab_with_only_a_daemon_has_no_agent(self):
        self.assertEqual(self.inventory([('alpha', True, 'GUIDE-TAB', 'ttys032', [], [self.daemon('GUIDE-TAB')])]), [])

    def test_client_without_layout_tty_is_kept_on_its_own_terminal_evidence(self):
        self.sessions[67028] = 'bet-session'
        rows = self.inventory([('beta', False, 'NO-TTY-TAB', None, [67028],
                                [self.proc(67028, ppid=46013, surface='NO-TTY-TAB')])])
        self.assertEqual([(r['pid'], r['surface_id'], r['key']) for r in rows],
                         [(67028, 'NO-TTY-TAB', 'codex:bet-session')])

    def test_terminal_evidence_must_be_positive(self):
        self.assertFalse(ids.no_terminal({'pid': 1, 'name': 'codex'}))  # older cmux: unknown, kept
        self.assertTrue(ids.no_terminal({'tty_device': ids.NODEV, 'tpgid': None}))
        self.assertTrue(ids.no_terminal({'tty_device': -1, 'tpgid': 0}))
        self.assertFalse(ids.no_terminal({'tty_device': self.TTY, 'tpgid': None}))
        self.assertFalse(ids.no_terminal({'tty_device': ids.NODEV, 'tpgid': 41}))
        # a background job on its terminal is still a client, not a daemon
        background = self.proc(70, ppid=69, pgid=70, tpgid=71)
        self.assertFalse(ids.no_terminal(background))
        self.assertEqual([r['pid'] for r in self.inventory([('alpha', False, 'T', 'ttys1', [71], [background])])], [70])

    def test_foreground_breaks_ties_but_never_outranks_a_parent(self):
        bg = self.proc(80, ppid=79, pgid=80, tpgid=81)
        fg = self.proc(81, ppid=78, pgid=81)
        self.assertEqual([r['pid'] for r in self.inventory([('alpha', False, 'T', 'ttys1', [81], [bg, fg])])], [81])
        nested = self.proc(91, ppid=90, pgid=91)
        parent = self.proc(90, ppid=89, pgid=90, tpgid=91, children=[nested])
        self.assertEqual([r['pid'] for r in self.inventory([('alpha', False, 'T', 'ttys1', [91], [parent])])], [90])

    def cross_project(self, *, bet_env=False):
        claude = self.proc(13930, '2.1.289', ppid=24746)
        moved = dict(claude, cmux_surface_id='BET-TAB') if bet_env else claude
        self.sessions[500] = 'alp-session'
        other = self.proc(500, ppid=499, surface='ALP-OTHER')
        return [('alpha', False, 'ALP-TAB', 'ttys035', [], [claude]),
                ('alpha', False, 'ALP-OTHER', 'ttys040', [500], [other]),
                ('beta', False, 'BET-TAB', 'ttys020', [13930], [moved])]

    def test_unresolved_cross_project_sighting_is_held_not_moved_or_retired(self):
        bet = ids.register(project='beta', role='solver', key='claude:bet-session',
                            surface_id='BET-TAB', pid=13930, pid_start=self.START)
        held = set()
        self.assertEqual([r['pid'] for r in self.inventory(self.cross_project(), held)], [500])
        self.assertEqual(held, {(13930, self.START)})
        with self.cmux(self.cross_project()):
            ids.sync()
        agents = ids.read_registry()['agents']
        self.assertEqual({k: (v['status'], v.get('surface_id'), v.get('project')) for k, v in agents.items()},
                         {bet['name']: ('active', 'BET-TAB', 'beta'),
                          'alp-solver-1': ('active', 'ALP-OTHER', 'alpha')})

    def test_a_fresh_hook_record_still_decides_across_projects(self):
        self.hooks.append({'pid': 13930, 'session_id': 'bet-session', 'surface_id': 'BET-TAB',
                           'active_for_surface': True, 'cwd': '/elsewhere',
                           'updated_at_unix': ids.started_epoch(self.START) + 5})
        rows = {r['pid']: r for r in self.inventory(self.cross_project())}
        self.assertEqual((rows[13930]['surface_id'], rows[13930]['project'], rows[13930]['key']),
                         ('BET-TAB', 'beta', 'claude:bet-session'))

    def test_inherited_cmux_environment_outranks_a_tty_only_sighting(self):
        held = set()
        rows = {r['pid']: r for r in self.inventory(self.cross_project(bet_env=True), held)}
        self.assertEqual((rows[13930]['surface_id'], rows[13930]['project']), ('BET-TAB', 'beta'))
        self.assertEqual(held, set())


class RoleInferenceTests(unittest.TestCase):
    def role(self, workspace, **kw):
        return ids.infer_role('delta', workspace, 'S1', {}, **kw)

    def test_a_stale_review_tab_title_does_not_make_a_reviewer(self):
        # the title is no longer consulted at all; the triage slot is a solver
        self.assertEqual(self.role('delta solve'), 'solver')

    def test_the_review_workspace_hosts_reviewers(self):
        self.assertEqual(self.role('delta review'), 'reviewer')

    def test_header_and_manager(self):
        self.assertEqual(self.role('Group 1', header=True), 'guide')
        self.assertEqual(self.role('Group 1', header=True, manager_surface='s1'), 'manager')


if __name__ == '__main__':
    unittest.main()
