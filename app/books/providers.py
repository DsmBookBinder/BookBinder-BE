"""Interfaces for external book data and school catalogue integrations."""
from typing import Protocol


class BookProvider(Protocol):
    def search(self, query: str, page: int, page_size: int) -> dict: ...
    def get(self, isbn: str) -> dict | None: ...


class LibraryCatalog(Protocol):
    def ownership(self, isbn: str) -> str: ...
    def search(self, query: str, page: int, page_size: int) -> dict: ...


class ProviderUnavailable(Exception):
    pass


class ProviderError(Exception):
    pass


class ProviderTimeout(Exception):
    pass
