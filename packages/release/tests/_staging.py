"""Invented fixtures for the staging tests: a home holding every managed target
the way the live tools lay them out, the skills tree's importers, chat config
and state with secret-looking values, a capture provenance for the invented
skills tree, and fake service and identity commands that record any call.
Nothing here reads or touches the real home directory."""
import hashlib
import json
import os
import plistlib
import stat
from pathlib import Path

import _support

SECRETS = ("hunter2-correct-horse-battery", "tok-5ecret-zz9-plural-z-alpha", "ntfy-topic-q8w7e6r5",
           "launchd-env-s3cret-value")
REAL_PROVENANCE = json.loads((_support.CHECKOUT / "packages/chat/provenance.json").read_text())
CAPTURED = [e["baseline_path"] for e in REAL_PROVENANCE["captured"]]
FAKE_COMMANDS = ("launchctl", "systemctl", "install-identities", "pchat", "chat-bridge", "rotate-logs",
                 "merge-worker", "drain-prs", "pr-drainer")
KNOWN_IMPORTERS = {
    "project-manager/scripts/launch-worker": 'HERE = Path(__file__).resolve().parent\n'
                                             'sys.path.insert(0, str(HERE.parent.parent / "chat" / "scripts"))\n',
    "project-manager/scripts/childrun": 'sys.path.insert(0, str(HERE.parents[1] / "chat" / "scripts"))\n',
    "project-manager/scripts/report": 'sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "chat" / '
                                      '"scripts"))\n',
    "project-manager/scripts/reconcile": '    chat_scripts = str(Path(__file__).resolve().parents[2] / "chat" / '
                                         '"scripts")\n    if chat_scripts not in sys.path:\n'
                                         '        sys.path.insert(0, chat_scripts)\n',
    "model-classes/scripts/modelclass": "        chat_scripts = Path(__file__).resolve().parents[2] / 'chat' / "
                                        "'scripts'\n        if chat_scripts.is_dir():\n"
                                        "            sys.path.insert(0, str(chat_scripts))\n",
}


def write(path, text, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)
    return path


def link(path, target):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(target)


def plist(path, label, program, extra=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"Label": label, "ProgramArguments": ["/usr/bin/python3", str(program)], "RunAtLoad": True,
            "EnvironmentVariables": {"PATH": "/usr/bin:/bin", "CHAT_TOKEN": SECRETS[3]},
            "StandardOutPath": "/tmp/invented.log"}
    data.update(extra or {})
    with open(path, "wb") as stream:
        plistlib.dump(data, stream)


def make_home(home):
    """An invented home with every managed target, and its provenance."""
    skills = home / ".codex/skills"
    chat = skills / "chat"
    for rel in CAPTURED:
        executable = rel.split("/")[-1] in ("pchat", "chat-bridge", "rotate-logs")
        write(skills / rel, f"# invented {rel}\nprint('invented')\n", 0o755 if executable else 0o644)
    write(chat / "scripts/install-identities", "# invented identity rollout, excluded from capture\n", 0o755)
    write(chat / "scripts/__pycache__/chatlib.cpython-314.pyc", "invented bytecode\n")
    for rel, body in KNOWN_IMPORTERS.items():
        write(skills / rel, f"#!/usr/bin/env python3\nimport sys\nfrom pathlib import Path\n{body}", 0o755)
    write(skills / "unrelated/scripts/tool", "#!/usr/bin/env python3\nimport sys\nprint('no chat import')\n", 0o755)
    link(home / ".claude/skills/chat", chat)
    link(home / ".local/bin/pchat", chat / "scripts/pchat")
    link(home / ".local/share/weechat/python/autoload/role_colors.py", chat / "scripts/role_colors.py")
    agents = home / "Library/LaunchAgents"
    plist(agents / "com.coghex.chat-bridge.plist", "com.coghex.chat-bridge", chat / "scripts/chat-bridge",
          {"KeepAlive": True})
    plist(agents / "com.coghex.log-rotate.plist", "com.coghex.log-rotate", chat / "scripts/rotate-logs",
          {"StartInterval": 3600})
    plist(agents / "com.coghex.ergo.plist", "com.coghex.ergo", "/usr/local/bin/ergo")
    write(home / ".config/chat/config.json", json.dumps(
        {"owner": "pat", "assistants": ["sam"], "server": {"password": SECRETS[0]},
         "ntfy": f"https://ntfy.sh/{SECRETS[2]}"}, indent=2))
    state = home / ".local/state/chat"
    write(state / "identities.json", json.dumps({"version": 1, "counters": {"alpha": 3},
                                                 "agents": {"alpha-1": {"token": SECRETS[1]}}}))
    write(state / "outbox.jsonl", '{"id": "o-1", "text": "an invented queued message"}\n')
    for name in ("deliveries.jsonl", "dead-letters.jsonl", "acks.jsonl", "evidence.jsonl"):
        write(state / name, '{"id": "d-1", "state": "delivered"}\n')
    write(state / "pending.json", "{}\n")
    write(state / "checkpoints.json", '{"#alpha": "msg-1"}\n')
    write(state / "logs/alpha.jsonl", '{"msgid": "m-1", "text": "an invented chat line"}\n')
    write(state / "experiments/review-start-receipts-v1.enabled", "")
    write(state / "bridge.log", "invented bridge log\n")
    pm = home / ".local/state/project-manager/alpha"
    write(pm / "manager.json", '{"surface": "invented"}\n')
    write(pm / "runs/run-20261001T120000Z-abcdef123456/state.json", json.dumps({"schema": "childrun/2"}))
    write(home / ".local/state/ergo/ergo.log", "invented server log\n")
    return skills


def provenance_for(skills, path):
    """A provenance recording the invented skills tree's captured files as
    the baseline: the expectation staging checks against."""
    captured = []
    for rel in CAPTURED:
        data = (skills / rel).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        mode = "100755" if (skills / rel).stat().st_mode & stat.S_IXUSR else "100644"
        captured.append({"baseline_path": rel, "baseline": {"type": "file", "mode": mode, "sha256": digest},
                         "source_commit": "0" * 40, "effective_sha256": digest})
    data = {"schema": "plateia-chat-capture/1", "baseline": "0" * 40, "monitored": REAL_PROVENANCE["monitored"],
            "captured": captured}
    path.write_text(json.dumps(data, indent=2))
    return path


def spec_for(provenance, path):
    spec = json.loads((_support.RELEASE / "staging.json").read_text())
    spec["provenance"] = str(provenance)
    path.write_text(json.dumps(spec, indent=2))
    return path


def fake_commands(where):
    """A bin directory of commands that only record that they were called."""
    log = where / "fake-calls.log"
    bin_dir = where / "fake-bin"
    bin_dir.mkdir()
    for name in FAKE_COMMANDS:
        write(bin_dir / name, f'#!/bin/sh\necho "{name} $*" >> "{log}"\n', 0o755)
    return bin_dir, log


def snapshot_tree(root):
    """Every entry under root: a file's sha256 and mode, a link's target, a
    directory, without following links."""
    out = {}
    for folder, dirs, files in os.walk(root):
        for name in dirs + files:
            path = Path(folder, name)
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                out[rel] = ("link", os.readlink(path))
            elif path.is_dir():
                out[rel] = ("dir",)
            else:
                out[rel] = ("file", hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mode)
    return out
