"""Plateia's shared project chat, as installed from a release.

The captured commands live, unchanged, in this package's `scripts/`
directory. Each one puts its own directory on the import path, so its
modules (`chatlib`, `identities`, ...) load from this installed release and
never from a source checkout. The functions below are the thin entry points
the release's console scripts call; `load` gives a command's code without
running it.

This file is written into the release by packages/release/build_release.py.
"""
import importlib.machinery
import importlib.util
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent / "scripts"


def load(command, module_name=None):
    """The module object of a captured command (`pchat`, `chat-bridge`,
    `rotate-logs`, or a `.py` module), executed as an import: a command's
    `if __name__ == "__main__"` block does not run, so nothing starts."""
    path = SCRIPTS / command
    if not path.is_file():
        raise FileNotFoundError(f"no captured command {command!r} in this release")
    name = module_name or path.stem.replace("-", "_")
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def pchat():
    return load("pchat", "pchat_cli").main()


def chat_bridge():
    return load("chat-bridge").main()


def rotate_logs():
    return load("rotate-logs").main()
