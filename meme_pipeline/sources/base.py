from __future__ import annotations

from abc import ABC, abstractmethod

from ..config import Config
from ..models import FetchResult


class SourceAdapter(ABC):
    """A site-specific adapter.

    An adapter only has to turn a public site into a list of ``SourceItem``
    objects.  All integrity, naming, deduplication and catalog work is handled
    by the shared pipeline.
    """

    key: str = ""
    display_name: str = ""
    homepage: str = ""
    version: str = "1"
    referer: str | None = None

    @abstractmethod
    def fetch(self, config: Config) -> FetchResult:
        """Fetch the complete public listing and, when possible, a raw snapshot."""
        raise NotImplementedError

    def describe(self) -> dict[str, str]:
        return {
            "key": self.key,
            "display_name": self.display_name,
            "homepage": self.homepage,
            "version": self.version,
        }
