"""Sticky primary/backup selection for ClipSync relay connections."""

from __future__ import annotations


class RelaySelector:
    def __init__(self, urls: list[str] | tuple[str, ...]) -> None:
        self.urls = [url.strip() for url in urls if url and url.strip()]
        self.index = 0

    @property
    def current(self) -> str:
        if not self.urls:
            return ""
        return self.urls[self.index % len(self.urls)]

    def connected(self) -> None:
        """Keep the successful relay active until the socket fails."""

    def failed(self) -> None:
        if self.urls:
            self.index = (self.index + 1) % len(self.urls)
