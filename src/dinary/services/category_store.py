"""In-memory category cache with 1-hour TTL."""

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 3600


@dataclass
class Category:
    name: str
    group: str


@dataclass
class CategoryStore:
    _categories: list[Category] = field(default_factory=list)
    _loaded_at: float = 0.0

    @property
    def expired(self) -> bool:
        return time.monotonic() - self._loaded_at > CACHE_TTL_SECONDS

    @property
    def categories(self) -> list[Category]:
        return list(self._categories)

    def load(self, categories: list[Category]) -> None:
        self._categories = list(categories)
        self._loaded_at = time.monotonic()

    def group_for(self, category_name: str) -> str | None:
        for cat in self._categories:
            if cat.name == category_name:
                return cat.group
        return None
