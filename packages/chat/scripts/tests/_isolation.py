"""Imported first by every test module, before any script under test.

The scripts bind their state paths at import (chatlib.STATE_DIR, the bridge's
PENDING, PM_STATE, ...) from CHAT_STATE, CHAT_CONFIG and HOME. Setting those
here, once per process and before anything reads them, keeps every test in a
throwaway home whatever order the modules load in. An audit hook then refuses,
for the rest of the process, any file access under the owner's real state or
config, any network connection, and any command that could reach a live agent.
"""
import os
import pwd
import sys
import tempfile
from pathlib import Path

REAL_HOME = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)
PROTECTED = tuple(os.path.join(REAL_HOME, p) for p in (
    ".local/state", ".config", ".local/bin", "Library/Application Support/kanban"))
BLOCKED_COMMANDS = {"cmux", "gh", "curl", "launchctl", "osascript", "open", "ssh", "pchat", "chat-bridge",
                    "nudge-managers", "reconcile", "agentcli", "claude", "codex", "copilot",
                    "ps", "lsof"}  # process evidence must be faked: a test may not read live process metadata


class LiveStateAccess(RuntimeError):
    """A test reached for the owner's live state, network or agents."""


def _protected(value):
    if isinstance(value, int) or value is None:
        return False
    try:
        path = os.fsdecode(os.fspath(value))
    except TypeError:
        return False
    path = os.path.realpath(os.path.join(os.getcwd(), path)) if not os.path.isabs(path) else os.path.realpath(path)
    return any(path == root or path.startswith(root + os.sep) for root in PROTECTED)


def _command(args):
    argv = args[1] if len(args) > 1 else None
    first = argv[0] if isinstance(argv, (list, tuple)) and argv else (argv if isinstance(argv, (str, bytes)) else args[0])
    try:
        return os.path.basename(os.fsdecode(first))
    except TypeError:
        return ""


def _hook(event, args):
    if event == "open" or event.startswith(("os.", "shutil.", "glob.", "pathlib.")):
        for value in args:
            if isinstance(value, (str, bytes, os.PathLike)) and _protected(value):
                raise LiveStateAccess(f"test isolation: {event} on live path {os.fsdecode(os.fspath(value))}")
    if event == "socket.connect":
        raise LiveStateAccess(f"test isolation: network connection to {args[1]!r}")
    if event in ("subprocess.Popen", "os.posix_spawn", "os.exec", "os.spawn"):
        name = _command(args)
        if name in BLOCKED_COMMANDS:
            raise LiveStateAccess(f"test isolation: refused to run {name}")


def _install():
    inherited = os.environ.get("CHAT_TEST_SANDBOX")
    if inherited and os.path.realpath(os.environ.get("HOME", "")) == inherited:
        sys.addaudithook(_hook)  # a child process of an isolated run: same home, same guard
        return Path(inherited)
    if "chatlib" in sys.modules:
        raise LiveStateAccess("test isolation: chatlib was imported before the sandbox; its paths may be live")
    home = os.path.realpath(tempfile.mkdtemp(prefix="chat-test-home-"))
    os.environ.update(HOME=home, CHAT_TEST_SANDBOX=home,
                      CHAT_STATE=os.path.join(home, ".local/state/chat"),
                      CHAT_CONFIG=os.path.join(home, ".config/chat/config.json"))
    for var in ("CMUX_SURFACE_ID", "CMUX_WORKSPACE_ID", "CMUX_SOCKET_PATH", "CHAT_AGENT_ID", "CHAT_CHANNEL",
                "CHAT_REQUEST_ID"):
        os.environ.pop(var, None)
    os.makedirs(os.environ["CHAT_STATE"], exist_ok=True)
    sys.addaudithook(_hook)
    return Path(home)


HOME = _install()
CHAT_STATE = Path(os.environ["CHAT_STATE"])


def check_bound(*modules):
    """Fail closed if a module bound a path outside the sandbox."""
    for module in modules:
        for name, value in vars(module).items():
            if isinstance(value, Path) and value.is_absolute() and _protected(value):
                raise LiveStateAccess(f"test isolation: {module.__name__}.{name} is live ({value})")
