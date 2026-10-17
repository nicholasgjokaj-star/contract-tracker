from pathlib import Path

from imbue.imbue_common.ratchet_testing import standard_ratchet_checks as rc
from imbue.imbue_common.ratchet_testing.common_ratchets import (
    PREVENT_DATACLASSES_IMPORT,
    check_ratchet_rule,
)
from inline_snapshot import snapshot

_DIR = Path(__file__).parent

# claude_p.py is a verbatim vendored copy of the shared keyless-Claude helper,
# now taken from .agents/skills/use-ai-integration/scripts/claude_p.py (the
# workspace's current credential-resolving version; the pre-migration copy came
# from the same helper's older, process-env-reading revision). We keep it
# byte-for-byte in sync with that source rather than rewriting it, so its small
# dataclasses result containers are exempt from the dataclasses ratchet -- but no
# other file in this app is.
_VENDORED_FILES = ("claude_p.py",)


# --- Code safety ---


def test_prevent_todos() -> None:
    rc.check_todos(_DIR, snapshot(0))


def test_prevent_exec_usage() -> None:
    rc.check_exec(_DIR, snapshot(0))


def test_prevent_eval_usage() -> None:
    rc.check_eval(_DIR, snapshot(0))


def test_prevent_while_true() -> None:
    rc.check_while_true(_DIR, snapshot(0))


def test_prevent_time_sleep() -> None:
    rc.check_time_sleep(_DIR, snapshot(0))


def test_prevent_global_keyword() -> None:
    rc.check_global_keyword(_DIR, snapshot(0))


def test_prevent_bare_print() -> None:
    rc.check_bare_print(_DIR, snapshot(0))


# --- Exception handling ---


def test_prevent_bare_except() -> None:
    rc.check_bare_except(_DIR, snapshot(0))


def test_prevent_broad_exception_catch() -> None:
    rc.check_broad_exception_catch(_DIR, snapshot(0))


def test_prevent_builtin_exception_raises() -> None:
    rc.check_builtin_exception_raises(_DIR, snapshot(0))


# --- Import style ---


def test_prevent_inline_imports() -> None:
    rc.check_inline_imports(_DIR, snapshot(0))


def test_prevent_relative_imports() -> None:
    rc.check_relative_imports(_DIR, snapshot(0))


# --- Banned libraries and patterns ---


def test_prevent_asyncio_import() -> None:
    rc.check_asyncio_import(_DIR, snapshot(0))


def test_prevent_dataclasses_import() -> None:
    excluded = ("test_ratchets.py", "standard_ratchet_checks.py") + _VENDORED_FILES
    chunks = check_ratchet_rule(PREVENT_DATACLASSES_IMPORT, _DIR, excluded)
    assert len(chunks) == snapshot(0), PREVENT_DATACLASSES_IMPORT.format_failure(chunks)
