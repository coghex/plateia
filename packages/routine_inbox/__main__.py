"""Run with PYTHONPATH=<checkout>/packages python3 -m routine_inbox ..."""
import argparse
import json
import subprocess
import sys
import sqlite3

from .inbox import Blocked, Inbox, config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="private, non-secret routing manifest")
    parser.add_argument("--state", required=True, help="private durable inbox directory")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("initialize", help="baseline at current EOF; do not enqueue history")
    commands.add_parser("check-managers",help="read-only live identity and access check; never send")
    poll = commands.add_parser("poll", help="collect and reconcile; never send or acknowledge chat")
    poll.add_argument("--limit",type=int,default=5)
    claim = commands.add_parser("claim")
    claim.add_argument("id")
    reply = commands.add_parser("prepare", help="persist a reviewed decision; does not send")
    reply.add_argument("id")
    reply.add_argument("--claim",required=True)
    reply.add_argument("--decision",required=True,help="JSON file with kind, answer and source")
    send = commands.add_parser("send", help="attempt the prepared action once using existing identity")
    send.add_argument("id")
    send.add_argument("--claim",required=True)
    args = parser.parse_args()
    box = None
    try:
        box = Inbox(args.state,config(args.config))
        if args.command == "check-managers":
            result = [box.verify_manager(p) for p in box.cfg['projects']]
        elif args.command == "initialize":
            result = box.initialize()
        elif args.command == "poll":
            if not 1 <= args.limit <= 20:
                raise Blocked("poll limit must be between 1 and 20")
            result = box.poll(args.limit)
        elif args.command == "claim":
            result = box.claim(args.id)
        elif args.command == "prepare":
            from pathlib import Path
            decision = json.loads(Path(args.decision).read_text())
            result = {"argv":box.prepare(args.id,args.claim,decision),"sent":False}
        else:
            # This harmless capability check happens before committing send intent.
            checked = subprocess.run([box.cfg["pchat"],"post","--help"],capture_output=True,text=True,timeout=10)
            if checked.returncode or "--message-key" not in checked.stdout or "--to" not in checked.stdout:
                raise Blocked("installed pchat lacks the reviewed routing adapter; nothing sent")
            result = box.send(args.id,args.claim,lambda argv: subprocess.run(argv,capture_output=True,text=True,timeout=45))
        print(json.dumps(result,indent=2))
        return 0
    except (Blocked,OSError,ValueError,sqlite3.Error,subprocess.SubprocessError) as error:
        print(json.dumps({"blocked":str(error),"sent_or_resolved_not_assumed":True}),file=sys.stderr)
        return 75
    finally:
        if box is not None:
            box.db.close()


if __name__ == "__main__":
    sys.exit(main())
