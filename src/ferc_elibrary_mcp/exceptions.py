from __future__ import annotations


class ELibraryError(Exception):
    """Base error for eLibrary client and tools."""


class ELibraryRequestError(ELibraryError):
    """The FERC eLibrary backend returned an HTTP or protocol error."""


class FilingNotFoundError(ELibraryError):
    """No filing matched the requested accession or docket."""


class RestrictedDocumentError(ELibraryError):
    """The filing is CEII, privileged, or protected and cannot be downloaded."""
