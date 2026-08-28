from __future__ import annotations


class ELibraryError(Exception):
    """Base error for eLibrary client and tools."""


class ELibraryRequestError(ELibraryError):
    """The FERC eLibrary backend returned an HTTP or protocol error."""


class FilingNotFoundError(ELibraryError):
    """No filing matched the requested accession or docket."""


class RestrictedDocumentError(ELibraryError):
    """The filing is CEII, privileged, or protected and cannot be downloaded."""


class RateLimitError(ELibraryError):
    """FERC returned HTTP 429 or a ban-like response; back off before retrying."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after
