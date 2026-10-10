"""The captured chat guidance (packages/chat/SKILL.md) matches the captured
commands: every `pchat` subcommand and option it mentions exists in the
captured `pchat`, read from pchat's own argument parsers, and the post types it
lists are pchat's. Plateia's own test."""
import argparse
import importlib.machinery
import importlib.util
import re
import shlex
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolation  # noqa: E402  (first: sandbox home and live-state guard)
SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import agentcli  # noqa: E402
import chatlib  # noqa: E402

_loader = importlib.machinery.SourceFileLoader("pchat_cli", str(SCRIPTS / "pchat"))
_spec = importlib.util.spec_from_loader("pchat_cli", _loader)
pchat = importlib.util.module_from_spec(_spec)
_loader.exec_module(pchat)
_isolation.check_bound(chatlib, agentcli, pchat)

SKILL = (SCRIPTS.parent / "SKILL.md").read_text(encoding="utf-8")


class _Built(Exception):
    def __init__(self, parser):
        self.parser = parser


def built_parser(main):
    """The top-level parser `main` builds, caught before it parses anything."""
    def capture(parser, *args, **kwargs):
        raise _Built(parser)
    with mock.patch.object(argparse.ArgumentParser, "parse_args", capture):
        try:
            main([])
        except _Built as built:
            return built.parser
    raise AssertionError(f"{main} built no parser")


def subcommands(parser):
    """{name: options} for every subcommand of `parser`."""
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return {name: set(sub._option_string_actions) for name, sub in action.choices.items()}


PCHAT = subcommands(built_parser(pchat.main))
AGENT = subcommands(built_parser(agentcli.main))


def mentions():
    """(words after `pchat`, where) for every pchat command line in the guidance:
    each line of a fenced block, and each inline code span, that starts with
    `pchat` followed by something."""
    found = []
    for block in re.findall(r"```[a-z]*\n(.*?)```", SKILL, re.S):
        for line in block.splitlines():
            if line.startswith("pchat "):
                found.append((shlex.split(line, comments=True)[1:], line))
    prose = re.sub(r"```.*?```", "", SKILL, flags=re.S)
    for span in re.findall(r"`([^`]+)`", prose):
        words = span.split()
        if words[:1] == ["pchat"] and len(words) > 1:
            found.append((words[1:], span))
    return found


def resolve(words):
    """(subcommand, the options it accepts) for words after `pchat`."""
    if words[0] == "agents":  # pchat agents ... runs `pchat agent list --refresh ...`
        return "agent list", AGENT["list"]
    if words[0] == "agent":
        return f"agent {words[1]}", AGENT.get(words[1])
    return words[0], PCHAT.get(words[0])


# The paragraph on `install-identities`, the rollout helper no capture includes
# (D-20): its options are that command's, not pchat's.
ROLLOUT = next(p for p in SKILL.split("\n\n") if p.startswith("The installed rollout helper is"))


class GuidanceTests(unittest.TestCase):
    def test_the_rollout_paragraph_is_about_the_uncaptured_helper_only(self):
        self.assertIn("`chat/scripts/install-identities`", ROLLOUT)
        self.assertNotIn("pchat", ROLLOUT)
        self.assertFalse((SCRIPTS / "install-identities").exists())

    def test_the_guidance_names_commands_at_all(self):
        self.assertGreaterEqual(len(mentions()), 20)

    def test_every_mentioned_subcommand_and_option_exists(self):
        for words, where in mentions():
            with self.subTest(where=where):
                name, options = resolve(words)
                self.assertIsNotNone(options, f"pchat has no subcommand {name!r}")
                for word in words:
                    option = re.match(r"\[?(--[a-z][a-z-]*)", word)
                    if option:
                        self.assertIn(option[1], options, f"pchat {name} has no option {option[1]}")

    def test_options_named_on_their_own_belong_to_post(self):
        prose = re.sub(r"```.*?```", "", SKILL, flags=re.S).replace(ROLLOUT, "")
        named = {m for span in re.findall(r"`([^`]+)`", prose) for m in re.findall(r"^(--[a-z][a-z-]*)", span)}
        self.assertTrue(named)
        for option in named:
            with self.subTest(option=option):
                self.assertIn(option, PCHAT["post"])

    def test_the_listed_post_types_are_pchats(self):
        listed = re.search(r"`--type` is one of (.*?)\.", SKILL, re.S)[1]
        types = set(re.findall(r"`([a-z]+)`", listed))
        self.assertEqual(types | {"interrupt"}, set(pchat.TYPES))
        self.assertRegex(SKILL, r"--type\s+interrupt")

    def test_the_queueing_guidance_describes_19s_behaviour(self):
        """#19 requirement 11: the "No lost posts" paragraph and "When chat is down"
        say that only the unposted remainder is queued, that an uncertain part is
        checked against the record first, and that posts, acks and notices queue
        while the bridge cannot answer, even with the chat server reachable."""
        lost = SKILL.split("- **No lost posts.**", 1)[1].split("\n- **", 1)[0]
        down = SKILL.split("## When chat is down", 1)[1].split("\n## ", 1)[0]
        flat = lambda text: " ".join(text.split())  # noqa: E731
        lost, down = flat(lost), flat(down)
        self.assertIn("only the unposted remainder is queued", lost)
        self.assertIn("checks it against the channel record before any resend", lost)
        self.assertIn("`pchat post`, `pchat ack` and an agent's notices queue instead of sending, even when the "
                      "chat server itself is reachable", lost)
        self.assertIn("only the bridge is down, even if the chat server answers", down)
        self.assertIn("checked against the channel record before any resend", down)

    def test_the_check_catches_a_missing_option(self):
        self.assertNotIn("--no-such-option", PCHAT["post"])
        self.assertIsNone(resolve(["agent", "no-such-subcommand"])[1])


if __name__ == "__main__":
    unittest.main()
