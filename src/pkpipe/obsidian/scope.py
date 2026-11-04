"""The scope of a vault snapshot, derived from the vault policy (spec 019 FR-002, spec 020 FR-009).

Nothing here names a folder of the public pack: the folders are the allowlists of the writing tools, the
layout's write places (:meth:`VaultLayout.snapshot_roots`), the quarantine folder and the managed files of the
policy, so the scope follows any vault layout. The journal folder is excluded: the pipeline never writes
journals and a snapshot must not copy them.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import PurePosixPath

from pkpipe.domain.layout import VaultLayout
from pkpipe.domain.vault_snapshot import WRITING_VAULT_TOOLS, reduce_folders, under

from .policy import VaultPolicy


@dataclass(frozen=True)
class SnapshotScope:
    folders: tuple[str, ...]
    managed: tuple[str, ...]
    exclude: tuple[str, ...]

    @property
    def digest(self) -> str:
        blob = json.dumps(
            {"folders": self.folders, "managed": self.managed, "exclude": self.exclude}, sort_keys=True
        )
        return hashlib.sha256(blob.encode()).hexdigest()

    def covers(self, path: str) -> bool:
        """Whether ``path`` is a managed file or lies in a scope folder and not in an excluded one."""
        if any(under(path, ex) for ex in self.exclude):
            return False
        return path in self.managed or any(under(path, f) for f in self.folders)


def journal_folder(layout: VaultLayout) -> str:
    """The folder of the journal pattern (its segments before the first placeholder segment, without the
    file name): ``DailyJournals/{yyyy}/{yyyy-MM-dd}.md`` -> ``DailyJournals``."""
    keep: list[str] = []
    for seg in PurePosixPath(layout.journal_pattern).parts[:-1]:
        if "{" in seg:
            break
        keep.append(seg)
    return "/".join(keep)


def derive_scope(policy: VaultPolicy) -> SnapshotScope:
    layout = policy.layout
    folders = list(layout.snapshot_roots())
    for tool in sorted(WRITING_VAULT_TOOLS):
        folders += policy.allow.get(tool, [])
    folders += [layout.quarantine_dir]
    managed = tuple(sorted(dict.fromkeys(policy.managed_files)))
    reduced = reduce_folders(folders)
    journal = journal_folder(layout)
    # only a journal folder below a scope folder needs excluding; a pattern that puts day files directly in
    # a scope folder cannot be excluded without dropping the folder
    nested = journal and any(under(journal, f) and journal != f for f in reduced)
    return SnapshotScope(tuple(reduced), managed, (journal,) if nested else ())
