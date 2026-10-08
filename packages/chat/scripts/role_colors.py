"""WeeChat role colors for the owner's project IRC. No message text is changed."""
import json
import os
import re
from pathlib import Path

COLORS = {'manager': '135', 'solver': '208', 'reviewer': '196', 'guide': '75',
          'assistant': '82', 'owner': '51'}
REGISTRY = Path(os.environ.get('CHAT_STATE', Path.home() / '.local/state/chat')) / 'identities.json'
CONFIG = Path(os.environ.get('CHAT_CONFIG', Path.home() / '.config/chat/config.json'))
GENERIC_ASSISTANTS = ('claude', 'codex')
LAST = None
_NAMES = {}


def _configured():
    """(owner or None, assistants, {name: role}) from the chat config, read at
    runtime so no account name lives here. claude and codex are always
    assistants; every other identity under `identities` with a known role
    gets that role. Cached until the config file changes: WeeChat asks once
    per line."""
    try:
        st = CONFIG.stat()
    except OSError:
        return None, list(GENERIC_ASSISTANTS), {}
    key = (st.st_ino, st.st_mtime_ns, st.st_size)
    if key not in _NAMES:
        try:
            cfg = json.loads(CONFIG.read_text())
        except (OSError, ValueError):
            cfg = {}
        cfg = cfg if isinstance(cfg, dict) else {}
        owner = cfg.get('owner') if isinstance(cfg.get('owner'), str) and cfg.get('owner') else None
        listed = cfg.get('assistants') if isinstance(cfg.get('assistants'), list) else []
        assistants = [a for a in listed if isinstance(a, str) and a]
        identities = cfg.get('identities') if isinstance(cfg.get('identities'), dict) else {}
        roles = {n: i['role'] for n, i in identities.items()
                 if n and isinstance(i, dict) and i.get('role') in COLORS}
        _NAMES.clear()
        _NAMES[key] = owner, assistants + [a for a in GENERIC_ASSISTANTS if a not in assistants], roles
    return _NAMES[key]


def configured_names():
    """(owner or None, assistants) from the chat config."""
    return _configured()[:2]


def configured_roles():
    """{name: role} for the chat config's other identities."""
    return _configured()[2]


def role_for_nick(nick):
    nick = nick.lower()
    owner, assistants = configured_names()
    if owner and nick == owner.lower():
        return 'owner'
    if nick in {a.lower() for a in assistants}:
        return 'assistant'
    roles = {n.lower(): r for n, r in configured_roles().items()}
    if nick in roles:
        return roles[nick]
    match = re.fullmatch(r'[a-z]+-(manager|guide|solver-\d+|reviewer-\d+|worker)', nick)
    if match:
        role = match[1].split('-')[0]
        return 'solver' if role == 'worker' else role
    try:
        return json.loads(REGISTRY.read_text()).get('agents', {}).get(nick, {}).get('role')
    except (OSError, ValueError):
        return None


def line_cb(data, line):
    role = None
    for tag in line.get('tags', '').split(','):
        if tag.startswith('nick_'):
            role = role_for_nick(tag[5:])
            break
    if role not in COLORS:
        return {}
    prefix = weechat.string_remove_color(line.get('prefix', ''), '')
    return {'prefix': weechat.color(COLORS[role]) + prefix + weechat.color('reset')}


def refresh(data='', remaining=0):
    global LAST
    try:
        agents = json.loads(REGISTRY.read_text()).get('agents', {})
    except (OSError, ValueError):
        agents = {}
    desired = {n: COLORS[r['role']] for n, r in agents.items() if r.get('role') in COLORS}
    desired.update({name: COLORS[role] for name, role in configured_roles().items()})
    owner, assistants = configured_names()
    desired.update({name: COLORS['assistant'] for name in assistants})
    if owner:
        desired[owner] = COLORS['owner']
    option = weechat.config_get('weechat.look.nick_color_force')
    existing = weechat.config_string(option)
    pairs = dict(p.split(':', 1) for p in existing.split(';') if ':' in p)
    # Entries this script forced before (recorded in its own plugin option, so
    # a restart remembers them) that nothing names any more are dropped, unless
    # someone has changed them since; every other entry is left alone.
    recorded = weechat.config_get_plugin('managed')
    for name, color in (p.split(':', 1) for p in recorded.split(';') if ':' in p):
        if name not in desired and pairs.get(name) == color:
            del pairs[name]
    pairs.update(desired)
    value = ';'.join(f'{n}:{c}' for n, c in sorted(pairs.items()))
    if value != existing:
        weechat.config_option_set(option, value, 1)
    managed = ';'.join(f'{n}:{c}' for n, c in sorted(desired.items()))
    if managed != recorded:
        weechat.config_set_plugin('managed', managed)
    # Recolor existing buffer prefixes too, including logs loaded before this
    # script. This is presentation only; timestamps, tags and message text stay.
    signature = tuple(sorted(desired.items()))
    if signature != LAST:
        LAST = signature
        bh, lh, dh = (weechat.hdata_get(n) for n in ('buffer', 'line', 'line_data'))
        buf = weechat.hdata_get_list(bh, 'gui_buffers')
        while buf:
            lines = weechat.hdata_pointer(bh, buf, 'own_lines')
            ptr = weechat.hdata_pointer(weechat.hdata_get('lines'), lines, 'first_line') if lines else ''
            while ptr:
                ld = weechat.hdata_pointer(lh, ptr, 'data')
                nick = weechat.hdata_string(dh, ld, 'prefix')
                plain = weechat.string_remove_color(nick, '').strip('<> @+~&%')
                role = role_for_nick(plain)
                if role in COLORS:
                    weechat.hdata_update(dh, ld, {'prefix': weechat.color(COLORS[role]) +
                                        weechat.string_remove_color(nick, '') + weechat.color('reset')})
                ptr = weechat.hdata_move(lh, ptr, 1)
            buf = weechat.hdata_move(bh, buf, 1)
    return weechat.WEECHAT_RC_OK


try:
    import weechat
except ImportError:
    weechat = None

if weechat and weechat.register('role_colors', configured_names()[0] or 'owner', '1.0', 'MIT',
                                'Fixed project agent nickname colors by role', '', ''):
    weechat.hook_line('', 'irc.*', 'irc_privmsg,irc_notice', 'line_cb', '')
    weechat.hook_timer(10000, 0, 0, 'refresh', '')
    refresh()
