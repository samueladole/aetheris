"""Verify that README.md's factual claims still match the repository.

The README is prose humans read, so it drifts silently: a command gets
removed, a test suite grows, a milestone closes, and the README keeps
describing an older repository. This script recomputes the claims most likely
to drift and fails if the README disagrees.

It deliberately checks *facts*, not wording, so a rewrite of the prose is fine
while a stale assertion fails.

It also checks *pending* claims: any README paragraph or list item that says
something is "not yet implemented" must cite the task that will implement it,
and every cited task must still be open in the pipeline tasks.md. Once the
task is checked off, the claim fails until the README is updated.

Run directly, or through CI:

    python scripts/check_readme_sync.py
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
README = REPO / "README.md"
PIPELINE_TASKS = REPO / ".specify" / "specs" / "tasks.md"
DOCS_TASKS = REPO / ".specify" / "specs" / "001-docs-site-cicd" / "tasks.md"

# Facts the README is required to assert, and how to recompute each one.
# `pattern` is matched against the README; `actual()` recomputes it now.
CHECKS: list[dict[str, object]] = [
    {
        "name": "test count",
        "pattern": r"\*\*(\d+) tests?\*\*",
        "actual": lambda: _test_count(),
        "hint": "Update the 'Verification' block to `pytest --collect-only`'s count.",
    },
    {
        "name": "python version",
        "pattern": r"\*\*Language/Version\*\*: Python (\d+\.\d+)\+",
        "actual": lambda: _requires_python(),
        "hint": "Match requires-python in pyproject.toml.",
    },
    {
        "name": "cli commands",
        "pattern": r"\*\*(\d+) commands?\*\*",
        "actual": lambda: _cli_command_count(),
        "hint": "Count `aetheris --help` subcommands.",
    },
    {
        "name": "import contracts",
        "pattern": r"\*\*(\d+) import contracts? kept\*\*",
        "actual": lambda: _import_contracts(),
        "hint": "Run `lint-imports` and count 'KEPT'.",
    },
    {
        "name": "reference pages",
        "pattern": r"(\d+) API reference pages",
        "actual": lambda: _reference_pages(),
        "hint": "Count modules gen_api.py would document (src/**/*.py minus __init__).",
    },
    {
        "name": "requirement ids",
        "pattern": r"FR-001…FR-(\d+)",
        "actual": lambda: _requirement_ids(),
        "numeric": True,
        "hint": "Count distinct FR- ids in .specify/specs/spec.md.",
    },
    {
        "name": "pipeline task progress",
        "pattern": r"Pipeline \(`src/`\) \| (\d+) / (\d+)",
        "actual": lambda: _task_progress(PIPELINE_TASKS),
        "tuple": True,
        "hint": "Read the checked/total task counts in .specify/specs/tasks.md.",
    },
    {
        "name": "docs-site task progress",
        "pattern": r"Documentation site \| (\d+) / (\d+)",
        "actual": lambda: _task_progress(DOCS_TASKS),
        "tuple": True,
        "hint": "Read the checked/total task counts in the 001-docs-site-cicd task file.",
    },
]

# Wording that asserts a task is still open. Matched after markdown emphasis is
# stripped and whitespace collapsed, so wrapped or bolded phrasing still counts.
_PENDING = re.compile(
    r"not yet implemented|not implemented yet|yet to be implemented|unimplemented",
    re.IGNORECASE,
)
# A line that starts a new list item or table row within a paragraph.
_ITEM_START = re.compile(r"^\s*(?:[-*+]|\d+\.|\|)\s")


def _task_progress(path: Path) -> tuple[int, int]:
    """Checked and total task counts in a Spec Kit task breakdown."""
    if not path.is_file():
        raise SyncError(f"{path} is missing")
    checked = total = 0
    for line in path.read_text().splitlines():
        if line.startswith("- [X] T"):
            checked += 1
        if line.startswith("- [") and " T" in line:
            total += 1
    return checked, total


def _requirement_ids() -> int:
    """Highest FR- id declared in the pipeline spec."""
    text = (REPO / ".specify" / "specs" / "spec.md").read_text()
    ids = {int(m) for m in re.findall(r"FR-(\d+)", text)}
    if not ids:
        raise SyncError("no FR- ids found in .specify/specs/spec.md")
    return max(ids)


def _anchors(readme: str) -> set[str]:
    """GitHub-style anchors for every heading in the README."""
    anchors: set[str] = set()
    for line in readme.splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if not m:
            continue
        anchor = re.sub(r"[^\w\s-]", "", m.group(2)).strip().lower()
        anchors.add(anchor.replace(" ", "-"))
    return anchors


def _broken_anchors(readme: str) -> list[str]:
    """Internal links whose target heading does not exist."""
    anchors = _anchors(readme)
    used = re.findall(r"\]\(#([a-z0-9-]+)\)", readme)
    return sorted({target for target in used if target not in anchors})


class SyncError(RuntimeError):
    """Raised when the README disagrees with the repository."""


def _run(argv: list[str], *, cwd: Path = REPO) -> str:
    proc = subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, timeout=300
    )
    return proc.stdout + proc.stderr


def _test_count() -> int:
    # No -q here: pyproject.toml already sets addopts="-q", and a second -q
    # suppresses the summary line this parses.
    return _parse_collected_count(_run([".venv/bin/pytest", "tests/", "--collect-only"]))


def _parse_collected_count(out: str) -> int:
    """Collected test count from ``pytest --collect-only`` output.

    The collected count is the same on every machine, unlike a passed count,
    which drops when Node is missing or provider credentials are absent.
    Whether the tests pass is the CI Tests step's job. Collection errors still
    refuse, because a module that fails to import hides its tests.
    """
    match = re.search(r"\b(\d+) tests? collected(.*)$", out, re.MULTILINE)
    if not match:
        raise SyncError(f"could not parse test count from pytest output: {out[-400:]}")
    errors = re.search(r"\d+ errors?", match.group(2))
    if errors:
        raise SyncError(
            f"collection reported {errors.group(0)}, so some tests were not "
            "counted. Fix the import errors first."
        )
    return int(match.group(1))


def _requires_python() -> str:
    text = (REPO / "pyproject.toml").read_text()
    match = re.search(r'requires-python\s*=\s*">=(\d+\.\d+)"', text)
    if not match:
        raise SyncError("requires-python not found in pyproject.toml")
    return match.group(1)


def _cli_command_count() -> int:
    out = _run([".venv/bin/aetheris", "--help"])
    return len(re.findall(r"^    ([a-z][a-z-]+)\s", out, re.MULTILINE))


def _import_contracts() -> int:
    out = _run([".venv/bin/lint-imports"])
    match = re.search(r"Contracts: (\d+) kept", out)
    if not match:
        raise SyncError(f"could not parse lint-imports output: {out[-400:]}")
    return int(match.group(1))


def _reference_pages() -> int:
    total = 0
    for path in (REPO / "src").rglob("*.py"):
        if "__pycache__" in path.parts or path.name == "__init__.py":
            continue
        total += 1
    return total


def _paths_mentioned(readme: str) -> list[str]:
    """Markdown link *destinations* that look like repository paths.

    Parses `[label](target)` rather than backtick labels: the label is
    display text (often a bare filename) while the target is the path that
    must actually exist.
    """
    found: set[str] = set()

    for target in re.findall(r"\]\(([^)#\s]+)\)", readme):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        # Keep only repo-relative, file-shaped targets.
        if re.match(r"^[A-Za-z0-9_.][A-Za-z0-9_./-]*\.[a-z]{1,6}$", target) or target.startswith(
            ("src/", "docs/", "scripts/", "tests/", ".specify/", ".github/")
        ):
            found.add(target)

    # Backtick code references to project files, which are not markdown links.
    backticked = re.findall(
        r"`((?:src|docs|scripts|tests|\.specify|\.github)/[A-Za-z0-9_./-]+)`", readme
    )
    for match in backticked:
        found.add(match)

    return sorted(found)


def _missing_paths(readme: str) -> list[str]:
    missing = []
    for rel in _paths_mentioned(readme):
        if rel.endswith((".github.io",)):  # not a repo path
            continue
        if not (REPO / rel).exists():
            missing.append(rel)
    return missing


def _task_states(tasks: str) -> dict[str, bool]:
    """Map each task id in a task breakdown to whether it is checked off."""
    return {
        task: mark in "xX"
        for mark, task in re.findall(r"^- \[([ xX])\] (T\d{3})\b", tasks, re.MULTILINE)
    }


def _claim_units(readme: str) -> list[str]:
    """Split the README into paragraphs, with each list item or table row its own unit.

    A pending claim and the task it cites can sit in different sentences of one
    paragraph, but a task named in a sibling bullet is a separate claim.
    """
    units: list[str] = []
    for paragraph in re.split(r"\n\s*\n", readme):
        current: list[str] = []
        for line in paragraph.splitlines():
            if _ITEM_START.match(line) and current:
                units.append(" ".join(current))
                current = []
            current.append(line.strip())
        if current:
            units.append(" ".join(current))
    return units


def _stale_pending_claims(readme: str, tasks: str) -> list[str]:
    """Failures for "not yet implemented" claims that cite no open task."""
    states = _task_states(tasks)
    failures: list[str] = []
    for unit in _claim_units(readme):
        text = re.sub(r"\s+", " ", unit.replace("*", ""))
        if not _PENDING.search(text):
            continue
        excerpt = text if len(text) <= 80 else text[:77] + "..."
        cited = sorted(set(re.findall(r"\bT\d{3}\b", text)))
        if not cited:
            failures.append(
                f"[pending claim] {excerpt!r} says something is not implemented but "
                "cites no task, so it cannot be verified. Cite the task id that will "
                "implement it."
            )
        for task in cited:
            if task not in states:
                failures.append(
                    f"[pending claim] {excerpt!r} cites {task}, which is not in "
                    ".specify/specs/tasks.md"
                )
            elif states[task]:
                failures.append(
                    f"[stale pending claim] {excerpt!r} says {task} is not yet "
                    "implemented, but .specify/specs/tasks.md marks it done"
                )
    return failures


def _check_facts(readme: str, checks: list[dict[str, object]]) -> list[str]:
    """Failures for every fact in ``checks`` the README omits or misstates."""
    failures: list[str] = []
    for check in checks:
        name = check["name"]
        pattern = str(check["pattern"])
        found = re.search(pattern, readme)
        if not found:
            failures.append(
                f"[{name}] the README does not assert this fact at all. "
                f"Expected pattern {pattern!r}. {check['hint']}"
            )
            continue
        documented = found.group(1)
        try:
            actual = check["actual"]()  # type: ignore[operator]
        except SyncError as exc:
            failures.append(f"[{name}] could not be verified: {exc}")
            continue
        # A "<checked> / <total>" claim is compared as a pair, and zero-padded
        # ids like FR-021 compare numerically so "021" is not reported against 21.
        if check.get("tuple"):
            groups = found.groups()
            if len(groups) != 2:
                failures.append(
                    f"[{name}] README does not state a checked/total pair. {check['hint']}"
                )
                continue
            documented = " / ".join(groups)
            same = tuple(int(g) for g in groups) == tuple(actual)
            actual = " / ".join(str(n) for n in actual)
        elif check.get("numeric"):
            same = documented.isdigit() and int(documented) == int(actual)
        else:
            same = str(actual) == str(documented)
        if not same:
            failures.append(
                f"[{name}] README says {documented!r} but the repository says "
                f"{actual!r}. {check['hint']}"
            )
    return failures


def main() -> int:
    """Recompute every checked claim and report any that disagree."""
    if not README.is_file():
        print(f"error: {README} is missing")
        return 2

    readme = README.read_text()
    failures = _check_facts(readme, CHECKS)
    failures += _stale_pending_claims(readme, PIPELINE_TASKS.read_text())

    missing = _missing_paths(readme)
    for rel in missing:
        failures.append(f"[missing path] the README references `{rel}`, which does not exist")

    for target in _broken_anchors(readme):
        failures.append(
            f"[broken anchor] the README links to #{target}, which is not a heading"
        )

    if failures:
        print("README is out of sync with the repository:")
        for failure in failures:
            print(f"  - {failure}")
        print(f"\n{len(failures)} issue(s). Update README.md and re-run.")
        return 1

    print("README is in sync with the repository")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
