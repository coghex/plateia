"""Role colors with the owner's, the assistants' and every other configured
identity's names read from the chat config at runtime: different invented
configs color different names, a renamed identity loses its forced color, the
palette is the baseline's, and only a line's prefix (the nickname) is ever
recolored. Plateia's own test; invented names only."""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolation  # noqa: E402  (first: sandbox home and live-state guard)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import role_colors  # noqa: E402
_isolation.check_bound(role_colors)

BASELINE_PALETTE = {'manager': '135', 'solver': '208', 'reviewer': '196', 'guide': '75',
                    'assistant': '82', 'owner': '51'}


class FakeWeechat:
    """The few WeeChat calls the script makes; colors render as \\x19<code>\\x1c."""
    WEECHAT_RC_OK = 0

    def __init__(self, forced='', prefixes=(), plugin=None):
        self.forced = forced
        self.plugin = {} if plugin is None else plugin
        self.lines = [{'prefix': p, 'message': f'text {i}'} for i, p in enumerate(prefixes)]
        self.updates = []

    def color(self, name):
        return f'\x19{name}\x1c'

    def string_remove_color(self, text, replacement):
        return re.sub('\x19[^\x1c]*\x1c', replacement, text)

    def config_get(self, name):
        return name

    def config_string(self, option):
        return self.forced

    def config_option_set(self, option, value, run_callback):
        self.forced = value

    def config_get_plugin(self, name):
        return self.plugin.get(name, '')

    def config_set_plugin(self, name, value):
        self.plugin[name] = value

    def hdata_get(self, name):
        return name

    def hdata_get_list(self, hdata, name):
        return 'buffer-1' if self.lines else ''

    def hdata_pointer(self, hdata, pointer, name):
        return {'own_lines': 'lines-1', 'first_line': 'line-0', 'data': pointer}[name]

    def hdata_string(self, hdata, pointer, name):
        return self.lines[int(pointer.split('-')[1])][name]

    def hdata_update(self, hdata, pointer, changes):
        self.updates.append((pointer, dict(changes)))
        self.lines[int(pointer.split('-')[1])].update(changes)

    def hdata_move(self, hdata, pointer, count):
        if hdata == 'buffer':
            return ''
        index = int(pointer.split('-')[1]) + count
        return f'line-{index}' if index < len(self.lines) else ''


class RoleColorCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir=_isolation.HOME)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.registry = self.root / 'identities.json'
        self.use_config('config.json', {'owner': 'pat', 'assistants': ['sam']})
        for target, value in (('REGISTRY', self.registry), ('LAST', None), ('_NAMES', {})):
            patch = mock.patch.object(role_colors, target, value)
            patch.start()
            self.addCleanup(patch.stop)

    def use_config(self, name, cfg):
        path = self.root / name
        path.write_text(cfg if isinstance(cfg, str) else json.dumps(cfg))
        patch = mock.patch.object(role_colors, 'CONFIG', path)
        patch.start()
        self.addCleanup(patch.stop)
        return path


class ConfiguredNameTests(RoleColorCase):
    def test_the_palette_is_the_baseline_palette(self):
        self.assertEqual(role_colors.COLORS, BASELINE_PALETTE)

    def test_owner_and_assistants_come_from_the_config(self):
        cases = [({'owner': 'pat', 'assistants': ['sam']}, 'pat', {'sam'}),
                 ({'owner': 'robin', 'assistants': ['kit', 'lee']}, 'robin', {'kit', 'lee'}),
                 ({'owner': 'Quinn', 'assistants': []}, 'quinn', set())]
        for i, (cfg, owner, assistants) in enumerate(cases):
            with self.subTest(cfg=cfg):
                self.use_config(f'config-{i}.json', cfg)
                self.assertEqual(role_colors.role_for_nick(owner), 'owner')
                self.assertEqual(role_colors.role_for_nick(owner.upper()), 'owner')
                for name in assistants | {'claude', 'codex'}:
                    self.assertEqual(role_colors.role_for_nick(name), 'assistant')
                for name in {'pat', 'sam', 'robin', 'kit', 'lee', 'quinn'} - {owner} - assistants:
                    self.assertIsNone(role_colors.role_for_nick(name))

    def test_a_changed_config_is_read_again(self):
        path = self.use_config('config.json', {'owner': 'pat', 'assistants': ['sam']})
        self.assertEqual(role_colors.role_for_nick('pat'), 'owner')
        path.write_text(json.dumps({'owner': 'robin', 'assistants': ['sam', 'kit']}))
        self.assertIsNone(role_colors.role_for_nick('pat'))
        self.assertEqual(role_colors.role_for_nick('robin'), 'owner')
        self.assertEqual(role_colors.role_for_nick('kit'), 'assistant')

    def test_a_missing_or_malformed_config_names_only_the_generic_assistants(self):
        for name, cfg in (('missing.json', None), ('broken.json', '{not json'), ('list.json', '["pat"]'),
                          ('wrong-types.json', {'owner': 7, 'assistants': 'sam'})):
            with self.subTest(config=name):
                if cfg is None:
                    patch = mock.patch.object(role_colors, 'CONFIG', self.root / name)
                    patch.start()
                    self.addCleanup(patch.stop)
                else:
                    self.use_config(name, cfg)
                self.assertEqual(role_colors.configured_names(), (None, ['claude', 'codex']))
                self.assertIsNone(role_colors.role_for_nick('pat'))
                self.assertEqual(role_colors.role_for_nick('codex'), 'assistant')

    def test_other_configured_identities_get_their_configured_role(self):
        identities = {'observer': {'role': 'guide', 'project': 'alpha'}, 'Lead': {'role': 'manager'},
                      'odd': {'role': 'unknown'}, 'bare': 'guide', 'none': {}}
        path = self.use_config('config.json', {'owner': 'pat', 'assistants': ['sam'], 'identities': identities})
        self.assertEqual(role_colors.configured_roles(), {'observer': 'guide', 'Lead': 'manager'})
        self.assertEqual(role_colors.role_for_nick('observer'), 'guide')
        self.assertEqual(role_colors.role_for_nick('lead'), 'manager')
        for name in ('odd', 'bare', 'none'):
            self.assertIsNone(role_colors.role_for_nick(name))
        path.write_text(json.dumps({'owner': 'pat', 'identities': {'watcher': {'role': 'guide'}}}))
        self.assertIsNone(role_colors.role_for_nick('observer'))
        self.assertEqual(role_colors.role_for_nick('watcher'), 'guide')
        path.write_text(json.dumps({'owner': 'pat', 'identities': ['observer']}))
        self.assertEqual(role_colors.configured_roles(), {})

    def test_agent_names_are_colored_by_their_role_whatever_the_config(self):
        for nick, role in [('alp-manager', 'manager'), ('alp-guide', 'guide'), ('alp-solver-20', 'solver'),
                           ('alp-reviewer-3', 'reviewer'), ('alp-worker', 'solver')]:
            self.assertEqual(role_colors.role_for_nick(nick), role)
        self.registry.write_text(json.dumps({'agents': {'helper': {'role': 'guide'}}}))
        self.assertEqual(role_colors.role_for_nick('helper'), 'guide')


class PrefixOnlyTests(RoleColorCase):
    def test_a_line_gets_only_its_prefix_recolored(self):
        fake = FakeWeechat()
        with mock.patch.object(role_colors, 'weechat', fake):
            line = {'tags': 'irc_privmsg,nick_pat,log1', 'prefix': fake.color('99') + 'pat',
                    'message': 'hello pat, sam and alp-manager'}
            self.assertEqual(role_colors.line_cb('', line), {'prefix': '\x1951\x1cpat\x19reset\x1c'})
            line['tags'] = 'irc_privmsg,nick_sam'
            self.assertEqual(role_colors.line_cb('', line), {'prefix': '\x1982\x1cpat\x19reset\x1c'})
            line['tags'] = 'irc_privmsg,nick_stranger'
            self.assertEqual(role_colors.line_cb('', line), {})

    def test_refresh_forces_configured_names_and_keeps_other_entries(self):
        self.registry.write_text(json.dumps({'agents': {'alp-solver-1': {'role': 'solver'},
                                                        'alp-odd': {'role': 'unknown'}}}))
        fake = FakeWeechat(forced='stranger:1')
        with mock.patch.object(role_colors, 'weechat', fake):
            self.assertEqual(role_colors.refresh(), fake.WEECHAT_RC_OK)
        self.assertEqual(fake.forced, 'alp-solver-1:208;claude:82;codex:82;pat:51;sam:82;stranger:1')
        self.assertEqual(fake.plugin['managed'], 'alp-solver-1:208;claude:82;codex:82;pat:51;sam:82')

    def test_refresh_forces_other_configured_identities(self):
        self.use_config('config.json', {'owner': 'pat', 'identities': {'observer': {'role': 'guide'}}})
        fake = FakeWeechat()
        with mock.patch.object(role_colors, 'weechat', fake):
            role_colors.refresh()
        self.assertEqual(fake.forced, 'claude:82;codex:82;observer:75;pat:51')

    def test_renamed_identities_lose_their_forced_colors_across_refreshes(self):
        path = self.use_config('config.json', {'owner': 'pat', 'assistants': ['sam'],
                                               'identities': {'observer': {'role': 'guide'}}})
        fake = FakeWeechat(forced='stranger:1')
        with mock.patch.object(role_colors, 'weechat', fake):
            role_colors.refresh()
            self.assertEqual(fake.forced, 'claude:82;codex:82;observer:75;pat:51;sam:82;stranger:1')
            fake.forced = fake.forced.replace('sam:82', 'sam:99')  # the user recolors sam by hand
            path.write_text(json.dumps({'owner': 'robin', 'assistants': ['kit'],
                                        'identities': {'watcher': {'role': 'guide'}}}))
            role_colors.refresh()
            expected = 'claude:82;codex:82;kit:82;robin:51;sam:99;stranger:1;watcher:75'
            self.assertEqual(fake.forced, expected)
            role_colors.refresh()
            self.assertEqual(fake.forced, expected)
            self.assertIsNone(role_colors.role_for_nick('pat'))
        # a restart keeps the record in the plugin option, so a later rename is still cleaned up
        restarted = FakeWeechat(forced=fake.forced, plugin=dict(fake.plugin))
        path.write_text(json.dumps({'owner': 'robin', 'assistants': []}))
        with mock.patch.object(role_colors, 'weechat', restarted), mock.patch.object(role_colors, 'LAST', None):
            role_colors.refresh()
        self.assertEqual(restarted.forced, 'claude:82;codex:82;robin:51;sam:99;stranger:1')

    def test_refresh_recolors_existing_prefixes_and_never_the_text(self):
        fake = FakeWeechat(prefixes=['pat', '@sam', 'stranger'])
        before = [line['message'] for line in fake.lines]
        with mock.patch.object(role_colors, 'weechat', fake):
            role_colors.refresh()
        self.assertEqual([pointer for pointer, _ in fake.updates], ['line-0', 'line-1'])
        self.assertTrue(all(set(changes) == {'prefix'} for _, changes in fake.updates))
        self.assertEqual([line['prefix'] for line in fake.lines],
                         ['\x1951\x1cpat\x19reset\x1c', '\x1982\x1c@sam\x19reset\x1c', 'stranger'])
        self.assertEqual([line['message'] for line in fake.lines], before)


if __name__ == '__main__':
    unittest.main()
