"""Backlog — prioritized improvement tasks produced by the analyst agent."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class BacklogItem:
    id: int
    title: str
    description: str
    files: list[str]
    priority: float  # 0.0–1.0, higher = more important
    category: str  # error_handling, complexity, type_safety, performance, etc.
    status: str = "pending"  # pending, in_progress, done, failed, skipped
    attempts: int = 0
    last_rejection_reason: str = ""


class Backlog:
    """Ordered list of improvement tasks."""

    def __init__(self) -> None:
        self.items: list[BacklogItem] = []

    def load_from_analyst(self, raw_items: list[dict]) -> None:
        """Populate from analyst agent output."""
        for i, raw in enumerate(raw_items):
            self.items.append(BacklogItem(
                id=i,
                title=raw.get("title", f"Task {i}"),
                description=raw.get("description", ""),
                files=raw.get("files", []),
                priority=float(raw.get("priority", 0.5)),
                category=raw.get("category", "general"),
            ))
        self.items.sort(key=lambda x: -x.priority)

    def next(self) -> BacklogItem | None:
        """Return the highest-priority pending item."""
        for item in self.items:
            if item.status == "pending":
                item.status = "in_progress"
                item.attempts += 1
                return item
        return None

    def has_pending(self) -> bool:
        return any(i.status == "pending" for i in self.items)

    def mark_done(self, item: BacklogItem) -> None:
        item.status = "done"

    def mark_failed(self, item: BacklogItem, reason: str) -> None:
        item.last_rejection_reason = reason
        if item.attempts >= 2:
            item.status = "skipped"
        else:
            item.status = "pending"
            item.priority *= 0.5  # deprioritize after failure

    def summary(self) -> str:
        done = sum(1 for i in self.items if i.status == "done")
        failed = sum(1 for i in self.items if i.status in ("failed", "skipped"))
        pending = sum(1 for i in self.items if i.status == "pending")
        return f"{done} done, {failed} failed/skipped, {pending} pending of {len(self.items)} total"

    def save(self, path: Path) -> None:
        with open(path, "w") as f:
            json.dump([asdict(i) for i in self.items], f, indent=2)

    @classmethod
    def load(cls, path: Path) -> Backlog:
        b = cls()
        with open(path) as f:
            b.items = [BacklogItem(**i) for i in json.load(f)]
        return b
