"""docs/setup_guide.md, the one-document PC setup guide, can't drift silently.

Every ``gos`` command it writes exists in the CLI with the subcommands and flags it uses, every
``scripts/`` file it names is in the repo, its links resolve, its steps are numbered 1..N once,
and no step is finished by opening another document. :func:`problems` lists what is wrong with
a guide text; the mutation test proves it does, on broken copies of the real guide.
"""

from __future__ import annotations

import argparse
import re
import shlex
from pathlib import Path
from typing import NoReturn

import pytest

from gregtec import cli, sync

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs"
GUIDE = DOCS / "setup_guide.md"
TITLE = (
    "# Gregtec OS: set up your PC, from the November install to the building bake-off"
)

STEP = re.compile(r"^### Step (\d+)\. .+$", re.MULTILINE)
HEADING = re.compile(r"^#{1,6} .+$", re.MULTILINE)
PART = re.compile(r"^## Part \d+[a-z]?\. .+$", re.MULTILINE)
FENCE = re.compile(r"^```(\w*)\n(.*?)^```$", re.MULTILINE | re.DOTALL)
SPAN = re.compile(r"`([^`\n]+)`")
LINK = re.compile(r"\]\(([^)\s]+)\)")
SCRIPT = re.compile(r"scripts[\\/]([\w.-]+\.(?:py|ps1))")
STEP_REF = re.compile(r"\bsteps? (\d+)(?:(?:, | and | or | to )(\d+))*", re.IGNORECASE)
# A full command: `& $gos x --flag` or `uv run gos x --flag`, up to the end of the statement.
FULL = re.compile(r"(?:&[ \t]+\$gos|uv run gos)[ \t]+([^;|\n]*)")
# A looser look at the whole text, as test_quick_start.py does: a bare `gos x y` in prose or in
# quoted program output names a command (and a subcommand) the CLI has.
BARE = re.compile(
    r"(?<![\w$.\\-])gos[ \t]+([a-z][a-z0-9-]*)(?:[ \t]+([a-z][a-z0-9-]*))?"
)
SYNC = re.compile(r"\$sync\b([^;|\n]*)")
# The names the "paste this first" blocks define; a part that uses one must define it first.
VARS = ("app", "gos", "py", "gh", "exp", "sync", "clone")
# A step may name another document of the repo only on a "More detail:" line.
DOC_LINK = re.compile(r"\]\((?!https?://|#)")
DOC_NAME = re.compile(r"\b[\w-]+\.md\b")
DOC_NAMES = frozenset(p.name for p in [*DOCS.rglob("*.md"), *REPO.glob("*.md")])
# "see ... step N", but not the guide's own "You should see: ... step N"
SEE_STEP = re.compile(r"(?<!should )\bsee\b(?:(?!\. )[^\n])*\bsteps?\b", re.IGNORECASE)


class _Parsed(Exception):
    def __init__(self, parser: argparse.ArgumentParser) -> None:
        self.parser = parser


def _gos_parser() -> argparse.ArgumentParser:
    """The parser :func:`gregtec.cli.main` builds (the trick of test_quick_start.py)."""

    def stop(
        self: argparse.ArgumentParser, *args: object, **kwargs: object
    ) -> NoReturn:
        raise _Parsed(self)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(argparse.ArgumentParser, "parse_args", stop)
        with pytest.raises(_Parsed) as got:
            cli.main(["doctor"])
    return got.value.parser


def _sync_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sync.add_arguments(parser)
    return parser


def _subparsers(p: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for a in p._actions:
        if isinstance(a, argparse._SubParsersAction):
            return dict(a.choices)
    return {}


def _needs_subcommand(p: argparse.ArgumentParser) -> bool:
    return any(
        isinstance(a, argparse._SubParsersAction) and a.required for a in p._actions
    )


def _words(command: str) -> list[str]:
    """A command's words as PowerShell passes them: quotes kept together, no escapes."""
    lexer = shlex.shlex(command, posix=True)
    lexer.whitespace_split = True
    lexer.escape = ""
    lexer.commenters = "#"
    return list(lexer)


def _command_problem(parser: argparse.ArgumentParser, command: str) -> str | None:
    """Why ``command`` (the words after ``gos``) isn't one the parser takes, or None.

    Subcommands are followed as far as the parser has them; every ``-x`` / ``--flag`` must be an
    option of the parser reached, and its value (when it takes one) is skipped. Positional
    values aren't judged.
    """
    try:
        words = _words(command)
    except ValueError as exc:
        return f"`gos {command.strip()}`: {exc}"
    said = "gos"
    skip = False
    for word in words:
        if skip:
            skip = False
            continue
        if word.startswith("-") and not re.fullmatch(r"-?[\d.]+", word):
            flag = word.split("=", 1)[0]
            action = parser._option_string_actions.get(flag)
            if action is None:
                return f"`{said}` has no flag {flag}"
            skip = "=" not in word and action.nargs != 0
            continue
        subs = _subparsers(parser)
        if subs:
            if word not in subs:
                return f"`{said}` has no subcommand {word}"
            parser = subs[word]
            said += f" {word}"
    if _needs_subcommand(parser):
        return f"`{said}` needs a subcommand"
    return None


def _code(text: str) -> list[str]:
    """The PowerShell the guide tells him to type: its powershell blocks' lines, and the inline
    code spans outside every block."""
    lines = []
    for lang, body in FENCE.findall(text):
        if lang == "powershell":
            lines += body.splitlines()
    return lines + SPAN.findall(FENCE.sub("", text))


def command_problems(text: str) -> list[str]:
    gos, syncer = _gos_parser(), _sync_parser()
    top = _subparsers(gos)
    out = []
    for line in _code(text):
        for command in FULL.findall(line):
            if why := _command_problem(gos, command):
                out.append(why)
        for flags in SYNC.findall(line):
            if why := _command_problem(syncer, flags):
                out.append(why.replace("`gos`", "`sync_instances.py`"))
    for cmd, sub in BARE.findall(text):
        if cmd not in top:
            out.append(f"`gos` has no subcommand {cmd}")
        elif sub and (subs := _subparsers(top[cmd])) and sub not in subs:
            out.append(f"`gos {cmd}` has no subcommand {sub}")
    for lang, body in FENCE.findall(text):
        if lang == "powershell" and (m := re.search(r"<[A-Za-z]", body)):
            where = body[m.start() :].split("\n", 1)[0]
            out.append(
                f"a powershell block isn't paste-ready: PowerShell refuses {where}"
            )
    return out


def _parts(text: str) -> list[tuple[str, str]]:
    """Each ``## Part`` heading with its text, up to the next ``##`` heading."""
    heads = [m for m in re.finditer(r"^## .+$", text, re.MULTILINE)]
    ends = [m.start() for m in heads[1:]] + [len(text)]
    return [
        (m.group(0), text[m.end() : end])
        for m, end in zip(heads, ends)
        if PART.fullmatch(m.group(0))
    ]


def variable_problems(text: str) -> list[str]:
    """A part's powershell blocks use only the names the part's own blocks have set before."""
    out = []
    for head, body in _parts(text):
        code = "\n".join(b for lang, b in FENCE.findall(body) if lang == "powershell")
        for name in VARS:
            used = re.search(rf"\${name}\b(?![ \t]*=)", code)
            made = re.search(rf"^\${name}[ \t]*=", code, re.MULTILINE)
            if used and (made is None or made.start() > used.start()):
                out.append(f"{head}: ${name} is used before the part sets it")
    return out


def step_problems(text: str) -> list[str]:
    numbers = [int(n) for n in STEP.findall(text)]
    out = []
    if numbers != list(range(1, len(numbers) + 1)):
        out.append(f"the steps aren't numbered 1..{len(numbers)} once each: {numbers}")
    for m in STEP_REF.finditer(text):
        for n in re.findall(r"\d+", m.group(0)):
            if not 1 <= int(n) <= len(numbers):
                out.append(f"`{m.group(0)}` names a step the guide doesn't have")
    return out


def _steps(text: str) -> list[tuple[str, str]]:
    """Each step's heading and its text, up to the next heading of any level."""
    out = []
    for m in STEP.finditer(text):
        nxt = HEADING.search(text, m.end())
        out.append((m.group(0), text[m.end() : nxt.start() if nxt else len(text)]))
    return out


def handoff_problems(text: str) -> list[str]:
    """A step is whole in this guide: it says what he should see, never sends him to another
    document's step, and names another document only on a "More detail:" line."""
    out = []
    for head, body in _steps(text):
        prose = FENCE.sub("", body)
        if "You should see:" not in prose:
            out.append(f"{head}: no `You should see:`")
        if m := SEE_STEP.search(prose):
            out.append(f"{head}: `{m.group(0)}` sends him elsewhere to finish the step")
        for line in prose.splitlines():
            named = DOC_LINK.search(line) or DOC_NAMES & set(DOC_NAME.findall(line))
            if named and not line.startswith("More detail:"):
                out.append(f"{head}: another document outside `More detail:`: {line}")
    return out


def script_problems(text: str) -> list[str]:
    names = sorted(set(SCRIPT.findall(text)))
    return [
        f"scripts/{n} isn't in the repo"
        for n in names
        if not (REPO / "scripts" / n).is_file()
    ]


def _slug(heading: str) -> str:
    """GitHub's anchor for a heading (test_deployment_checklist.py's rule)."""
    words = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return words.replace(" ", "-")


def link_problems(text: str) -> list[str]:
    anchors = {
        _slug(m.group(1)) for m in re.finditer(r"^#{1,6} (.+)$", text, re.MULTILINE)
    }
    out = []
    for target in LINK.findall(text):
        if target.startswith(("http://", "https://")):
            continue
        path, _, anchor = target.partition("#")
        ok = (DOCS / path).exists() if path else anchor in anchors
        if not ok:
            out.append(f"the link {target} doesn't resolve")
    return out


def problems(text: str) -> list[str]:
    return [
        *step_problems(text),
        *command_problems(text),
        *variable_problems(text),
        *handoff_problems(text),
        *script_problems(text),
        *link_problems(text),
    ]


def _guide() -> str:
    return GUIDE.read_text(encoding="utf-8")


def test_the_guide_has_no_problems() -> None:
    text = _guide()
    assert text.startswith(TITLE + "\n")
    assert problems(text) == []


def test_the_guard_looked_at_enough() -> None:
    """The checks above pass on an empty page too, so count what they read."""
    text = _guide()
    code = _code(text)
    assert len(STEP.findall(text)) >= 60
    assert len(_parts(text)) >= 10
    assert sum(len(FULL.findall(line)) for line in code) >= 50
    assert sum(len(SYNC.findall(line)) for line in code) >= 2
    assert {"sync_instances.py", "check_tweaks.py"} <= set(SCRIPT.findall(text))
    assert len([t for t in LINK.findall(text) if not t.startswith("http")]) >= 8


# old text in the real guide -> its broken replacement, and a word of the problem it must raise
MUTATIONS = (
    ("a repeated step number", "### Step 7. ", "### Step 6. ", "numbered"),
    ("a missing step", "### Step 30. ", "### Step 130. ", "numbered"),
    ("a step that isn't there", "go back to step 20", "go back to step 920", "names"),
    ("an unknown command", "& $gos pair\n", "& $gos pairing\n", "no subcommand"),
    (
        "an unknown subcommand",
        "& $gos shell bakeoff run $plan",
        "& $gos shell bakeoff start $plan",
        "no subcommand start",
    ),
    (
        "an unknown flag",
        "& $gos serve --detach",
        "& $gos serve --background",
        "no flag --background",
    ),
    (
        "a flag of another command",
        "& $gos shell bakeoff report $run2 --reveal",
        "& $gos shell bakeoff show $run2 --reveal",
        "no flag --reveal",
    ),
    (
        "an unknown command in prose",
        "`gos export collect`",
        "`gos export gather`",
        "no subcommand gather",
    ),
    (
        "an unknown sync flag",
        "& $py $sync --dry-run",
        "& $py $sync --dryrun",
        "--dryrun",
    ),
    (
        "a placeholder PowerShell can't run",
        "& $gos layout $plan\n",
        "& $gos layout <plan-id>\n",
        "paste-ready",
    ),
    (
        "a variable the part never sets",
        '$exp   = "C:\\Games\\PrismLauncher\\instances\\GTNH-Export\\.minecraft"',
        "",
        "$exp is used before",
    ),
    (
        "a script that isn't there",
        "scripts\\sync_instances.py",
        "scripts\\sync_instance.py",
        "isn't in the repo",
    ),
    (
        "a broken link",
        "](runbooks/core_service.md)",
        "](runbooks/core_servce.md)",
        "doesn't resolve",
    ),
    (
        "a step finished in another document",
        "### Step 50. ",
        "See phase1_pc_checks.md step 2 for the rest.\n\n### Step 50. ",
        "sends him elsewhere",
    ),
    (
        "another document in a step's own text",
        "### Step 50. ",
        "The details are in phase1_pc_checks.md.\n\n### Step 50. ",
        "outside `More detail:`",
    ),
    (
        "a step that doesn't say what to expect",
        "You should see:",
        "Note:",
        "no `You should",
    ),
)


@pytest.mark.parametrize(
    ("old", "new", "word"), [m[1:] for m in MUTATIONS], ids=[m[0] for m in MUTATIONS]
)
def test_the_guard_catches_a_broken_guide(old: str, new: str, word: str) -> None:
    text = _guide()
    assert old in text, "the mutation must change the real guide"
    found = problems(text.replace(old, new))
    assert any(word in p for p in found), found


def test_the_guide_is_linked_from_the_quick_start_and_the_checklist() -> None:
    for name in ("quick_start.md", "deployment_checklist.md"):
        assert "](setup_guide.md)" in (DOCS / name).read_text(encoding="utf-8"), name
