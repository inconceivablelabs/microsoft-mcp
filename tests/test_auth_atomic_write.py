"""Atomic token-cache write regression tests (pa-20x9).

On 2026-04-16 the shared microsoft-mcp-tokens Docker volume was corrupted
when two concurrent containers (mcp-gateway spawns one per tool call) wrote
to token_cache.json at the same time via Path.write_text — plain
open/truncate/write/close with no atomicity or locking. Three stray bytes
landed after an otherwise valid JSON object, causing 68 Graph API calls to
fail with JSONDecodeError over 11 hours before manual recovery.

These tests pin the atomic-write behavior so the bug can't silently recur.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from microsoft_mcp.auth import _atomic_write


def test_atomic_write_creates_file_with_content(tmp_path: Path) -> None:
    target = tmp_path / "cache.json"
    _atomic_write(target, '{"foo": "bar"}')
    assert target.read_text() == '{"foo": "bar"}'


def test_atomic_write_creates_parent_dir(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "sub" / "cache.json"
    _atomic_write(target, '{"created": true}')
    assert target.read_text() == '{"created": true}'


def test_atomic_write_leaves_no_tempfile_behind(tmp_path: Path) -> None:
    target = tmp_path / "cache.json"
    _atomic_write(target, '{"x": 1}')
    siblings = [p.name for p in tmp_path.iterdir()]
    assert siblings == ["cache.json"], f"stray files: {siblings}"


def test_atomic_write_survives_concurrent_writers(tmp_path: Path) -> None:
    """Concurrent writers must produce a valid copy of one writer's content.

    Functional check: the atomic pattern (tempfile + os.replace) must
    produce either writer A's full content or writer B's full content —
    never a splice. Single-process threaded writes rarely reproduce the
    original multi-container bug on fast filesystems (the truncate+write
    completes within one timeslice), so this is a correctness sanity
    check rather than a deterministic bug reproducer.
    """
    target = tmp_path / "cache.json"
    content_a = json.dumps({"writer": "A", "payload": "a" * 8000})
    content_b = json.dumps({"writer": "B", "payload": "b" * 8000})

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def writer(content: str) -> None:
        try:
            barrier.wait()
            for _ in range(100):
                _atomic_write(target, content)
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=writer, args=(content_a,)),
        threading.Thread(target=writer, args=(content_b,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"writer threads raised: {errors}"
    final = target.read_text()
    assert final in (content_a, content_b), (
        f"file corrupted by concurrent writers: len={len(final)} "
        f"first_char={final[:1]!r} last_bytes={final[-10:]!r}"
    )
    # Must parse as JSON identical to one of the two intended payloads.
    parsed = json.loads(final)
    assert parsed["writer"] in ("A", "B")
