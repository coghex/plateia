"""Stage the small adapter for the existing chat tools; never install or restart.

The originals remain the currently installed infrastructure. This patch builder
is deliberately strict about each replacement and keeps the new protocol in
this package. A future source migration can replace the adapter independently.
"""
import argparse
from pathlib import Path


def replace(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f"adapter preimage differs: {old[:70]!r}")
    return source.replace(old,new,1)


def stage(installed, output, package_root):
    installed, output = Path(installed), Path(output)
    package_root = Path(package_root).expanduser().absolute()
    if not (package_root/'routine_inbox'/'protocol.py').is_file():
        raise ValueError("package root must contain the reviewed routine_inbox package")
    sources = {name:(installed/name).read_text() for name in ("pchat","chatlib.py","chat-bridge")}
    s = sources["chatlib.py"]
    s = replace(s,"from pathlib import Path\n",
                "from pathlib import Path\nimport sys\n"
                f"sys.path.insert(0, {str(package_root)!r})\n"
                "from routine_inbox.protocol import tags as routing_tags\n")
    s = replace(s,'def split_text(text: str, cont: str = "") -> list[str]:',
                'def split_text(text: str, cont: str = "", max_bytes: int = MAX_TEXT) -> list[str]:')
    s = replace(s,'room = MAX_TEXT - len(prefix.encode())','room = max_bytes - len(prefix.encode())\n            if room < 1:\n                raise ChatError("continuation prefix exceeds message budget")')
    s = replace(s,'reply_to: str | None = None) -> int:',
                'reply_to: str | None = None, recipients=None, message_key=None) -> int:')
    s = replace(s,'conn = login(account, cfg, caps=("message-tags",) if reply_to else ())',
                'conn = login(account, cfg, caps=("message-tags",) if reply_to or recipients else ())')
    s = replace(s,'lines = split_text(text, cont)\n        pre = _tag_prefix({"+draft/reply": reply_to} if reply_to else None)\n        privmsgs = [f"{pre}PRIVMSG {channel} :{line}" for line in lines]',
                'lines = split_text(text, cont, 240 if recipients else MAX_TEXT)\n'
                '        privmsgs = []\n'
                '        for number, line in enumerate(lines, 1):\n'
                '            tags = {"+draft/reply": reply_to} if reply_to else {}\n'
                '            if recipients:\n'
                '                tags.update(routing_tags(recipients, message_key, number, len(lines)))\n'
                '            pre = _tag_prefix(tags)\n'
                '            privmsgs.append(f"{pre}PRIVMSG {channel} :{line}")')
    sources["chatlib.py"] = s
    s = sources["pchat"]
    s = replace(s,'import time\n','import time\nimport uuid\n')
    s = replace(s,'    p.add_argument("--reply-to", dest="reply_to")',
                '    p.add_argument("--reply-to", dest="reply_to")\n'
                '    p.add_argument("--to", dest="recipients", action="append", default=[])\n'
                '    p.add_argument("--message-key", help="stable logical message key for reconciliation")')
    s = replace(s,'            cont = f"[{tag}]" if tag else ""',
                '            cont = f"[{tag}]" if tag else ""\n'
                '            if args.message_key and not args.recipients:\n'
                '                raise chatlib.ChatError("--message-key requires --to")\n'
                '            message_key = (args.message_key or uuid.uuid4().hex) if args.recipients else None\n'
                '            if args.recipients:\n'
                '                chatlib.routing_tags(args.recipients, message_key, 1, 1)')
    s = replace(s,'n = chatlib.post(args.channel, text, account, cfg, cont=cont, reply_to=args.reply_to)',
                'n = chatlib.post(args.channel, text, account, cfg, cont=cont, reply_to=args.reply_to,\n'
                '                                 recipients=args.recipients, message_key=message_key)')
    s = replace(s,'                                        "reply_to": args.reply_to,',
                '                                        "reply_to": args.reply_to, "to": args.recipients, "message_key": message_key,')
    sources["pchat"] = s
    s = sources["chat-bridge"]
    s = replace(s,'import chatlib  # noqa: E402',
                'import chatlib  # noqa: E402\nfrom routine_inbox.protocol import envelope, recipients as explicit_recipients')
    start = s.index('def mentions(text, accounts):')
    end = s.index('\n\ndef _append_jsonl',start)
    s = s[:start] + 'def mentions(text, accounts):\n    return explicit_recipients(text, accounts)\n' + s[end:]
    s = replace(s,'    if msg["tags"].get("+draft/reply"):',
                '    try:\n'
                '        routed = envelope(msg["tags"])\n'
                '        if routed:\n'
                '            entry["routing"] = routed\n'
                '    except ValueError as error:\n'
                '        entry["routing_error"] = str(error)\n'
                '    if msg["tags"].get("+draft/reply"):')
    s = replace(s,'    if entry["verified"]:  # an unauthenticated sender never wakes anyone',
                '    if entry["verified"] and not entry.get("routing_error"):  # malformed envelopes never route')
    s = replace(s,'        named = mentions(text, set(cfg.get("accounts", {}))) - {sender}',
                '        known = {a.lower(): a for a in cfg.get("accounts", {})}\n'
                '        if entry.get("routing"):\n'
                '            named = {known[a] for a in entry["routing"]["to"] if a in known} - {sender}\n'
                '        else:\n'
                '            named = mentions(text, set(known.values())) - {sender}')
    s = replace(s,'        if cfg.get("owner") in named and sender != cfg.get("owner"):',
                '        if (cfg.get("owner") in named and sender != cfg.get("owner")\n'
                '                and entry.get("routing", {}).get("part", 1) == 1):')
    s = replace(s,'                         cont=e.get("cont", ""), reply_to=e.get("reply_to"))',
                '                         cont=e.get("cont", ""), reply_to=e.get("reply_to"),\n'
                '                         **({"recipients": e["to"], "message_key": e["message_key"]} if e.get("to") else {}))')
    sources["chat-bridge"] = s
    # Do not emit a partial staging directory if any preimage or syntax check fails.
    for name, text in sources.items():
        compile(text,name,"exec")
    if output.exists():
        raise ValueError("output already exists; refusing to overwrite")
    output.mkdir(parents=True)
    for name, text in sources.items():
        target = output/name
        target.write_text(text)
        target.chmod((installed/name).stat().st_mode & 0o777)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed",required=True)
    parser.add_argument("--output",required=True)
    parser.add_argument("--package-root",required=True,help="reviewed package parent; embedded in the private staged adapter")
    args = parser.parse_args()
    stage(args.installed,args.output,args.package_root)
