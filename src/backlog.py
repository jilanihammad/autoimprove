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
    category: str  # Plugin-provided category (e.g. error_handling, clarity, flow_clarity)
    status: str = "pending"  # pending, in_progress, done, failed, skipped
    attempts: int = 0
    last_rejection_reason: str = ""
    plugin_name: str = ""  # Which plugin handles this item (empty = default)


class Backlog:
    """Ordered list of improvement tasks."""

    def __init__(self) -> None:
        self.items: list[BacklogItem] = []

    def load_from_analyst(self, raw_items: list[dict]) -> None:
        """Populate from analyst agent output."""
        for i, raw in enumerate(raw_items):
            category = raw.get("category", "general")
            plugin_name = ""
            # Extract plugin_name from tagged categories (e.g., "code:error_handling")
            if ":" in category:
                plugin_name, category = category.split(":", 1)
            self.items.append(BacklogItem(
                id=i,
                title=raw.get("title", f"Task {i}"),
                description=raw.get("description", ""),
                files=raw.get("files", []),
                priority=float(raw.get("priority", 0.5)),
                category=category,
                plugin_name=plugin_name,
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

    def merge_new_items(self, raw_items: list[dict]) -> int:
        """Merge new analyst items without duplicating existing ones. Returns count added."""
        existing_titles = {i.title.lower().strip() for i in self.items}
        next_id = max((i.id for i in self.items), default=-1) + 1
        added = 0
        for raw in raw_items:
            title = raw.get("title", "").strip()
            if title.lower() in existing_titles:
                continue
            category = raw.get("category", "general")
            plugin_name = ""
            if ":" in category:
                plugin_name, category = category.split(":", 1)
            self.items.append(BacklogItem(
                id=next_id,
                title=title,
                description=raw.get("description", ""),
                files=raw.get("files", []),
                priority=float(raw.get("priority", 0.5)),
                category=category,
                plugin_name=plugin_name,
            ))
            existing_titles.add(title.lower())
            next_id += 1
            added += 1
        self.items.sort(key=lambda x: -x.priority)
        return added

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
