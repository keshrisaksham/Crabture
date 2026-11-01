"""
rename/history.py — safe batch renaming + a persistent undo journal.

``perform_renames`` is the one place that actually touches the disk.  It moves
every file to a unique temporary name first and only then to its final name,
so permutations (a -> b, b -> a) work, and it rolls a file back when its
final move fails.  Both Power Rename's Apply and its Undo use it.

The journal lives at ``$XDG_STATE_HOME/crabture/rename_history.json``
(default ``~/.local/state/crabture/rename_history.json``; on Windows
``%LOCALAPPDATA%\\Crabture\\rename_history.json``).  It keeps the last
``MAX_BATCHES`` batches and is written atomically.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from typing import Optional

from crabture import osinfo

MAX_BATCHES = 20
JOURNAL_VERSION = 1


# ---------------------------------------------------------------------------
#  Safe two-phase rename
# ---------------------------------------------------------------------------

def perform_renames(pairs: list[tuple[str, str]]) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Rename ``src -> dst`` for every pair.

    Returns ``(renamed, failures)``: ``renamed`` maps source path to final path
    for the moves that succeeded, ``failures`` is ``[(source, reason)]``.
    Pairs whose source and destination are equal are skipped.
    """
    renamed: dict[str, str] = {}
    failures: list[tuple[str, str]] = []

    # Phase 1: move every file that changes to a unique temporary name.
    staged: list[tuple[str, str, str]] = []   # (source, temp, final)
    for src, dst in pairs:
        if os.path.abspath(src) == os.path.abspath(dst):
            continue
        temp = os.path.join(os.path.dirname(src) or ".", f".crabture_tmp_{uuid.uuid4().hex}")
        try:
            os.rename(src, temp)
            staged.append((src, temp, dst))
        except OSError as e:
            failures.append((src, str(e)))

    # Phase 2: move temporaries to their final names; roll back on failure.
    for src, temp, final in staged:
        try:
            if os.path.lexists(final):
                raise FileExistsError(f"'{os.path.basename(final)}' already exists.")
            os.rename(temp, final)
            renamed[src] = final
        except OSError as e:
            failures.append((src, str(e)))
            try:
                os.rename(temp, src)
            except OSError as e2:
                failures.append((src, f"Could not restore original: {e2}"))
    return renamed, failures


# ---------------------------------------------------------------------------
#  Journal
# ---------------------------------------------------------------------------

def journal_path() -> str:
    return os.path.join(osinfo.state_dir(), "rename_history.json")


def _load() -> list[dict]:
    try:
        with open(journal_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        batches = data.get("batches", []) if isinstance(data, dict) else []
        return [b for b in batches if isinstance(b, dict) and isinstance(b.get("items"), list)]
    except (OSError, ValueError):
        return []


def _save(batches: list[dict]) -> None:
    path = journal_path()
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    payload = {"version": JOURNAL_VERSION, "batches": batches[-MAX_BATCHES:]}
    fd, tmp = tempfile.mkstemp(prefix=".rename_history.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def record_rename_batch(renamed: dict[str, str]) -> Optional[dict]:
    """Append a batch for the ``{old: new}`` renames that just succeeded."""
    if not renamed:
        return None
    items = []
    for old, new in renamed.items():
        try:
            st = os.lstat(new)
            size, mtime_ns = st.st_size, st.st_mtime_ns
        except OSError:
            size = mtime_ns = None
        items.append({"old": old, "new": new, "size": size, "mtime_ns": mtime_ns})
    batch = {
        "id": uuid.uuid4().hex,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(items),
        "items": items,
    }
    batches = _load()
    batches.append(batch)
    _save(batches)
    return batch


def last_rename_batch() -> Optional[dict]:
    batches = _load()
    return batches[-1] if batches else None


def has_rename_undo() -> bool:
    return last_rename_batch() is not None


def discard_last_rename_batch() -> None:
    """Forget the newest batch without touching any file."""
    batches = _load()
    if batches:
        batches.pop()
        _save(batches)


def undo_last_rename() -> tuple[int, list[tuple[str, str]]]:
    """Undo the newest batch.  Returns ``(files restored, failures)``.

    A file is only restored if it is still where the batch left it and has
    not been modified since (size and mtime match); otherwise it is skipped
    with a reason.  The batch is removed when everything was restored; after a
    partial undo only the files that could not be restored remain.
    """
    batches = _load()
    if not batches:
        return 0, [("", "There is no rename to undo.")]
    batch = batches[-1]

    failures: list[tuple[str, str]] = []
    pairs: list[tuple[str, str]] = []
    item_for_new: dict[str, dict] = {}
    for item in batch["items"]:
        old, new = item.get("old"), item.get("new")
        if not old or not new:
            continue
        try:
            st = os.lstat(new)
        except OSError:
            failures.append((new, "File no longer exists at the renamed location."))
            continue
        if item.get("size") is not None and (
            st.st_size != item.get("size") or st.st_mtime_ns != item.get("mtime_ns")
        ):
            failures.append((new, "File was modified after the rename."))
            continue
        pairs.append((new, old))
        item_for_new[new] = item

    restored, move_failures = perform_renames(pairs)
    failures.extend(move_failures)

    if not failures:
        batches.pop()
        _save(batches)
    elif restored:
        batch["items"] = [it for it in batch["items"] if it.get("new") not in restored]
        batch["count"] = len(batch["items"])
        _save(batches)
    return len(restored), failures
