"""Public package export surface."""

from ferc_elibrary_mcp import (
    DateRangeResolution,
    DocketFiling,
    DocketSheet,
    DownloadResult,
    ELibraryClient,
    ELibraryError,
    ELibraryRequestError,
    FileSummary,
    FilingDetail,
    FilingNotFoundError,
    FilingSummary,
    RelatedCollection,
    RestrictedDocumentError,
    SearchHit,
    SearchResponse,
    Transmittal,
    __all__,
    main,
    mcp,
)


def test_public_exports_include_client_and_models() -> None:
    expected = {
        "DateRangeResolution",
        "DocketFiling",
        "DocketSheet",
        "DownloadResult",
        "ELibraryClient",
        "ELibraryError",
        "ELibraryRequestError",
        "FileSummary",
        "FilingDetail",
        "FilingNotFoundError",
        "FilingSummary",
        "RelatedCollection",
        "RestrictedDocumentError",
        "SearchHit",
        "SearchResponse",
        "Transmittal",
        "main",
        "mcp",
    }
    assert set(__all__) == expected
    assert ELibraryClient is not None
    assert callable(main)
    assert mcp is not None
    # Touch imported symbols so unused-import checkers stay quiet.
    assert all(
        name is not None
        for name in (
            DateRangeResolution,
            DocketFiling,
            DocketSheet,
            DownloadResult,
            ELibraryError,
            ELibraryRequestError,
            FileSummary,
            FilingDetail,
            FilingNotFoundError,
            FilingSummary,
            RelatedCollection,
            RestrictedDocumentError,
            SearchHit,
            SearchResponse,
            Transmittal,
        )
    )


def test_client_supports_async_context_manager() -> None:
    assert hasattr(ELibraryClient, "__aenter__")
    assert hasattr(ELibraryClient, "__aexit__")
    assert hasattr(ELibraryClient, "aclose")
