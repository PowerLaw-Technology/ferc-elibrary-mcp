"""Public package export surface."""

from ferc_elibrary_mcp import (
    BundleDownloadResult,
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
    SkippedAccession,
    Transmittal,
    __all__,
    main,
    mcp,
)


def test_public_exports_include_client_and_models() -> None:
    expected = {
        "BundleDownloadResult",
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
        "SkippedAccession",
        "Transmittal",
        "main",
        "mcp",
    }
    assert set(__all__) == expected
    assert ELibraryClient is not None
    assert callable(main)
    assert mcp is not None
    assert all(
        name is not None
        for name in (
            BundleDownloadResult,
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
            SkippedAccession,
            Transmittal,
        )
    )


def test_client_supports_async_context_manager() -> None:
    assert hasattr(ELibraryClient, "__aenter__")
    assert hasattr(ELibraryClient, "__aexit__")
    assert hasattr(ELibraryClient, "aclose")


def test_module_entrypoint_exports_main() -> None:
    from ferc_elibrary_mcp.__main__ import main as module_main

    assert module_main is main
