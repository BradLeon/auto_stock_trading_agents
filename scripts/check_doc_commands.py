"""Check that every `ats ...` command written in the operator docs still exists.

Reads fenced code blocks and inline `code` spans of README, OPERATIONS, DEVELOPMENT, ARCHITECTURE and docs/agents/*.
For each command it checks that the subcommand path and every `--flag` are known to the real parser, and lists the
commands that now only answer with a retirement stub (the doc must say so in words). Runs nothing: the parser is
captured, never executed. Exit code 1 when a command is unknown.

    .venv/bin/python scripts/check_doc_commands.py
"""
import argparse, re, shlex, sys, glob
from ats.runtime import cli

class Captured(Exception):
    def __init__(self, parser): self.parser = parser

def _grab(self, *a, **k): raise Captured(self)
argparse.ArgumentParser.parse_args = _grab
try:
    cli.main([])
except Captured as c:
    root = c.parser

def subs(p):
    for a in p._actions:
        if isinstance(a, argparse._SubParsersAction):
            return a.choices
    return {}

def flags(p):
    out = set()
    for a in p._actions: out.update(a.option_strings)
    return out

moved = {}
for name in ("RESEARCH_MANUAL_MOVED", "CONSUMER_CUTOVER_MOVED", "DISPATCH_OWNER_MOVED"):
    for k in getattr(cli, name): moved[tuple(k)] = name

files = ["README.md", "docs/OPERATIONS.md", "docs/DEVELOPMENT.md", "docs/ARCHITECTURE.md"] + sorted(glob.glob("docs/agents/*.md"))
files = [f for f in files if glob.os.path.exists(f)]
bad, total, retired_shown = [], 0, []
for f in files:
    text = open(f).read().splitlines()
    inblock, buf, start = False, "", 0
    cmds = []
    for i, line in enumerate(text, 1):
        if line.strip().startswith("```"):
            inblock = not inblock; buf = ""; continue
        if not inblock:
            for m in re.finditer(r"`(ats\s[^`]+)`", line):
                cmds.append((i, m.group(1)))
            continue
        s = line.strip()
        if buf:
            s = buf + " " + s; buf = ""
        else:
            start = i
        if s.endswith("\\"):
            buf = s[:-1].strip(); continue
        if re.match(r"^(\$ )?ats\s", s): cmds.append((start, s.lstrip("$ ")))
    for ln, s in cmds:
        total += 1
        s = re.split(r"\s+(\||&&|;|>|2>|#)\s*", s)[0] if re.search(r"\s(\||&&|;|>|2>)\s", s) else s
        s = re.sub(r"\s+#.*$", "", s)
        try: toks = shlex.split(s)
        except ValueError as e: bad.append((f, ln, s, f"unparseable: {e}")); continue
        p, path = root, []
        rest = toks[1:]
        while rest:
            ch = subs(p)
            if rest[0] in ch:
                p = ch[rest[0]]; path.append(rest[0]); rest = rest[1:]
            else: break
        pos = [a for a in p._actions if not a.option_strings and a.choices and not isinstance(a, argparse._SubParsersAction)]
        if pos and rest and not rest[0].startswith("-") and not rest[0].startswith("<"):
            if rest[0] not in pos[0].choices:
                bad.append((f, ln, s, f"'{rest[0]}' is not one of {sorted(pos[0].choices)} for 'ats {' '.join(path)}'")); continue
            path.append(rest[0]); rest = rest[1:]
        key = tuple(path[:2])
        if key in moved:
            retired_shown.append((f, ln, " ".join(path))); 
        if subs(p) and not rest and path:
            bad.append((f, ln, s, f"incomplete: needs one of {sorted(subs(p))[:6]}")); continue
        if subs(p) and rest and not rest[0].startswith(("-", "<")):
            bad.append((f, ln, s, f"unknown subcommand '{rest[0]}' under 'ats {' '.join(path)}'")); continue
        if not path and (not rest or rest[0].startswith(("-", "<"))):
            continue  # `ats --help`, `ats <命令>`: placeholders, not commands
        if not path:
            bad.append((f, ln, s, "unknown top-level command")); continue
        known = flags(p) | {"-h", "--help"}
        for t in rest:
            if t.startswith("--"):
                fl = t.split("=")[0]
                if fl not in known: bad.append((f, ln, s, f"unknown flag {fl} for 'ats {' '.join(path)}'"))
print(f"commands checked: {total}; problems: {len(bad)}; commands that point at a retired stub: {len(retired_shown)}")
for f, ln, s, why in bad: print(f"BAD  {f}:{ln}  {why}\n       {s[:150]}")
for f, ln, p in retired_shown: print(f"RETIRED-STUB  {f}:{ln}  ats {p}")
sys.exit(1 if bad else 0)
