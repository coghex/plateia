"""Core command interface. A future approved host supplies an Adapter instance."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

from .adapter import AdapterError
from .inbox import Blocked, Inbox, config


def main(argv=None, adapter=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--state", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("initialize")
    poll = commands.add_parser("poll")
    poll.add_argument("--limit", type=int, default=5)
    claim = commands.add_parser("claim")
    claim.add_argument("id")
    prepare = commands.add_parser("prepare")
    prepare.add_argument("id")
    prepare.add_argument("--claim", required=True)
    prepare.add_argument("--decision", required=True)
    send = commands.add_parser("send")
    send.add_argument("id")
    send.add_argument("--claim", required=True)
    resend = commands.add_parser("authorize-resend", help="explicit operator action; never a periodic retry")
    resend.add_argument("id")
    resend.add_argument("--reason", required=True)
    repin = commands.add_parser("repin-config", help="explicit operator reconciliation, never periodic")
    repin.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    try:
        box = Inbox(args.state, config(args.config), adapter)
        if args.command == "initialize":
            result = box.initialize()
        elif args.command == "poll":
            result = box.poll(args.limit)
        elif args.command == "claim":
            result = box.claim(args.id)
        elif args.command == "prepare":
            result = box.prepare(args.id, args.claim, json.loads(Path(args.decision).read_text()))
        elif args.command == "send":
            result = box.send(args.id, args.claim)
        elif args.command == "repin-config":
            result = box.repin_config(args.reason)
        else:
            result = box.authorize_resend(args.id, args.reason)
        print(json.dumps(result, indent=2))
        return 0
    except (Blocked, AdapterError, OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        print(json.dumps({"blocked": str(error), "success_not_assumed": True}), file=sys.stderr)
        return 75


if __name__ == "__main__":
    sys.exit(main())
