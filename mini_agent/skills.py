"""Bounded, workspace-local instruction and skill discovery."""
from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

import yaml

MAX_FILE_BYTES = 32_768
MAX_SKILLS = 32
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


def read_local(root: Path, path: Path) -> str:
    """Reject links/reparse points and oversized files before reading text.

    This is an application-level check, not race-free OS isolation.
    """
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise PermissionError("instruction links/reparse points are not allowed")
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("instructions must be regular files")
    with path.open("rb") as handle:
        data = handle.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("instruction file exceeds byte limit")
    return data.decode("utf-8")


def parse_skill(text: str) -> tuple[str, str, str]:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("missing YAML front matter")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise ValueError("unclosed YAML front matter") from exc
    # BaseLoader avoids arbitrary constructors and recursive object graphs;
    # only two scalar fields are accepted by this MVP.
    metadata = yaml.load("\n".join(lines[1:end]), Loader=yaml.BaseLoader)
    if not isinstance(metadata, dict) or set(metadata) != {"name", "description"}:
        raise ValueError("front matter requires only name and description")
    name, description = metadata["name"], metadata["description"]
    if not isinstance(name, str) or len(name) > 64 or not NAME.fullmatch(name):
        raise ValueError("invalid skill name")
    if not isinstance(description, str) or not description.strip() or len(description) > 500:
        raise ValueError("invalid skill description")
    body = "\n".join(lines[end + 1:]).strip()
    if not body:
        raise ValueError("empty skill body")
    return name, " ".join(description.split()), body


@dataclass(frozen=True)
class SkillEntry:
    path: Path
    description: str


@dataclass
class SkillCatalog:
    root: Path
    entries: dict[str, SkillEntry] = field(default_factory=dict)
    instructions: str = ""
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def discover(cls, workspace: Path) -> SkillCatalog:
        catalog = cls(workspace.resolve())
        try:
            catalog.instructions = read_local(catalog.root, catalog.root / "AGENTS.md")
        except FileNotFoundError:
            pass
        except (OSError, ValueError, UnicodeError):
            catalog.warnings.append("AGENTS.md could not be loaded safely.")
        directory = catalog.root / ".agents" / "skills"
        try:
            # Validate directory components without following linked roots.
            current = catalog.root
            for part in (".agents", "skills"):
                current = current / part
                info = current.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise PermissionError("linked skill directory")
            with os.scandir(directory) as iterator:
                candidates = []
                for item in iterator:
                    if len(candidates) >= MAX_SKILLS:
                        raise ValueError("too many skill entries")
                    candidates.append(directory / item.name / "SKILL.md")
        except FileNotFoundError:
            return catalog
        except (OSError, ValueError):
            catalog.warnings.append("Skill directory unavailable or exceeds entry limit.")
            return catalog
        duplicates: set[str] = set()
        for path in sorted(candidates):
            try:
                name, description, _ = parse_skill(read_local(catalog.root, path))
                if name in catalog.entries or name in duplicates:
                    duplicates.add(name)
                    catalog.entries.pop(name, None)
                    catalog.warnings.append(f"Duplicate skill name rejected: {name}")
                    continue
                catalog.entries[name] = SkillEntry(path, description)
            except (OSError, ValueError, UnicodeError, yaml.YAMLError):
                catalog.warnings.append(f"Invalid or unreadable skill skipped: {path.parent.name}")
        return catalog

    def context(self) -> str:
        sections = []
        if self.instructions:
            sections.append("Workspace guidance from AGENTS.md:\n" + self.instructions)
        if self.entries:
            sections.append("Available skills (summaries only):\n" + "\n".join(
                f"- {name}: {entry.description}" for name, entry in sorted(self.entries.items())
            ))
        return "\n\n".join(sections)

    def load(self, name: str) -> dict:
        if not isinstance(name, str) or name not in self.entries:
            raise ValueError("unknown or invalid skill name")
        entry = self.entries[name]
        actual_name, description, body = parse_skill(read_local(self.root, entry.path))
        if actual_name != name or description != entry.description:
            raise ValueError("skill metadata changed; start a new task to rescan")
        return {"ok": True, "name": name, "base_directory": entry.path.parent.relative_to(self.root).as_posix(), "instructions": body}
