from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ferc_elibrary_mcp.config import (
    DESCRIPTION_MAX_LEN,
    ELIBRARY_UI_BASE,
    NONPUBLIC_KEYWORDS,
    NONPUBLIC_PREFIX_EXCEPTIONS,
    NONPUBLIC_PREFIX_RE,
)

DownloadFormat = Literal["native", "pdf", "zip"]
MatchMode = Literal["phrase", "all", "any"]
SearchScope = Literal["description", "full_text", "both"]
DateField = Literal["filed", "issued"]
DateRangeSource = Literal["explicit", "default_60_day", "none"]
CountBasis = Literal["distinct_accession", "docket_association"]
AvailabilityScope = Literal["public_only", "all"]
SortOrder = Literal["oldest_first", "newest_first"]


class Affiliation(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    af_type: str = Field(default="", alias="afType")
    affiliation: str = ""
    last_name: str = Field(default="", alias="lastName")
    first_initial: str = Field(default="", alias="firstInitial")
    middle_initial: str = Field(default="", alias="middleInitial")


class ClassType(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    document_class: str = Field(default="", alias="documentClass")
    document_type: str = Field(default="", alias="documentType")

    def label(self) -> str:
        if self.document_class and self.document_type:
            return f"{self.document_class}: {self.document_type}"
        return self.document_class or self.document_type


class Transmittal(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    file_id: str = Field(default="", alias="fileId")
    file_type: str = Field(default="", alias="fileType")
    file_format: str = Field(default="", alias="fileFormat")
    file_name: str = Field(default="", alias="fileName")
    file_desc: str = Field(default="", alias="fileDesc")
    file_size: int = Field(default=0, alias="fileSize")


class SearchHit(BaseModel):
    """Raw AdvancedSearch hit. FERC misspells accession as acesssionNumber."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    document_id: str = Field(default="", alias="documentId")
    accession_number: str = Field(default="", alias="acesssionNumber")
    description: str = ""
    category: str | None = None
    filed_date: str = Field(default="", alias="filedDate")
    issued_date: str = Field(default="", alias="issuedDate")
    posted_date: str = Field(default="", alias="postedDate")
    avail_code: str = Field(default="P", alias="availCode")
    docket_numbers: list[str] = Field(default_factory=list, alias="docketNumbers")
    libraries: list[str] = Field(default_factory=list)
    class_types: list[ClassType] = Field(default_factory=list, alias="classTypes")
    transmittals: list[Transmittal] = Field(default_factory=list)
    affiliations: list[Affiliation] = Field(default_factory=list)
    score: float | None = None

    @field_validator("docket_numbers", "libraries", mode="before")
    @classmethod
    def _none_to_list(cls, value: Any) -> list[Any]:
        return value or []


class SearchResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    success: bool = True
    total_hits: int = Field(default=0, alias="totalHits")
    num_hits: int = Field(default=0, alias="numHits")
    error_message: str | None = Field(default=None, alias="errorMessage")
    search_hits: list[SearchHit] = Field(default_factory=list, alias="searchHits")

    @field_validator("search_hits", mode="before")
    @classmethod
    def _none_hits(cls, value: Any) -> list[Any]:
        return value or []


class FileSummary(BaseModel):
    file_id: str
    file_name: str
    file_type: str
    file_size: int
    description: str = ""


class FilingSummary(BaseModel):
    accession_number: str
    filed_date: str
    issued_date: str | None = None
    description: str
    docket_numbers: list[str]
    category: str | None = None
    class_types: list[str]
    availability: str
    industry: list[str] = Field(default_factory=list)
    files: list[FileSummary] = Field(default_factory=list)
    url: str


class FilingDetail(FilingSummary):
    affiliations: list[Affiliation] = Field(default_factory=list)
    has_nonpublic_counterpart: bool = False
    nonpublic_counterpart_basis: str | None = None


class DateRangeResolution(BaseModel):
    """What date filter was actually applied, and why.

    Every search-shaped tool resolves its window through this one type and
    reports it via as_envelope(), so a caller can never mistake a filtered
    count for a total. Adding a second copy of this logic is what let the
    silent-window defect reappear in get_docket.
    """

    start: str | None = None
    end: str | None = None
    source: DateRangeSource = "none"
    field: DateField = "filed"
    filtered_client_side: bool = False

    @property
    def may_be_date_limited(self) -> bool:
        return self.source == "default_60_day"

    def as_envelope(self) -> dict[str, Any]:
        return {
            "date_range_applied": {"start": self.start, "end": self.end},
            "date_range_source": self.source,
            "date_field_applied": self.field,
            "results_may_be_date_limited": self.may_be_date_limited,
            "date_field_filtered_client_side": self.filtered_client_side,
        }


class DocketFiling(BaseModel):
    accession_number: str
    description: str
    category: str | None = None
    filed_date: str = ""
    issued_date: str = ""
    docket: str = ""
    sub_docket: str = ""
    # Every subdocket this accession is captioned to. Consolidated proceedings
    # cross-docket heavily, and collapsing this to one pair loses that.
    docket_numbers: list[str] = Field(default_factory=list)
    sub_dockets: list[str] = Field(default_factory=list)
    organizations: list[str] = Field(default_factory=list)
    url: str = ""


class DocketSheet(BaseModel):
    docket_number: str
    total_hits: int
    page: int
    page_size: int
    page_base: int = 1
    count_basis: CountBasis = "distinct_accession"
    includes_subdockets: list[str] = Field(default_factory=list)
    # The docket sheet exposes no availability code, so unlike search_filings
    # (public-only by default) it cannot filter by availability.
    availability_scope: AvailabilityScope = "all"
    sort_order: SortOrder = "oldest_first"
    date_range_applied: dict[str, str | None] = Field(default_factory=dict)
    date_range_source: DateRangeSource = "none"
    date_field_applied: DateField = "filed"
    results_may_be_date_limited: bool = False
    date_field_filtered_client_side: bool = False
    applicants: list[str] = Field(default_factory=list)
    filings: list[DocketFiling] = Field(default_factory=list)


class DownloadResult(BaseModel):
    accession_number: str
    path: str
    size: int
    content_type: str
    file_name: str
    url: str
    is_bundle: bool = False
    expected_size: int | None = None
    size_matches_metadata: bool | None = None
    skipped: bool = False
    skip_reason: str | None = None


class SkippedAccession(BaseModel):
    """Why one accession was left out of a download_bundle."""

    accession_number: str
    reason: str
    # "restricted" = privileged/CEII/protected; "not_found" = absent from eLibrary
    # (typo candidate). Agents should retry typos and give up on restricted.
    category: str = "not_found"


class TextExtractionResult(BaseModel):
    """Plain text extracted from a public filing attachment.

    Agents cannot open paths under FERC_DOWNLOAD_DIR, so summarization must use
    this payload rather than download_file alone.
    """

    accession_number: str
    file_name: str
    file_id: str | None = None
    path: str
    content_type: str
    text: str
    char_count: int
    truncated: bool = False
    page_count: int | None = None
    extractor: str
    url: str
    skipped: bool = False
    skip_reason: str | None = None


class BundleDownloadResult(BaseModel):
    """One Zip & Download archive spanning one or many accessions."""

    path: str
    size: int
    content_type: str = "application/zip"
    file_name: str
    file_count: int
    accession_numbers: list[str] = Field(default_factory=list)
    file_ids: list[str] = Field(default_factory=list)
    organized_by_accession: bool = True
    expected_size: int | None = None
    files_capped: bool = False
    skipped_accessions: list[SkippedAccession] = Field(default_factory=list)
    skipped: bool = False
    skip_reason: str | None = None


class RelatedCollection(BaseModel):
    query: str | None = None
    document_type: str | None = None
    date_range_applied: dict[str, str | None] = Field(default_factory=dict)
    date_range_source: DateRangeSource = "none"
    date_field_applied: DateField = "filed"
    results_may_be_date_limited: bool = False
    search_total_hits: int
    dockets_returned: int
    dockets_capped: bool
    filings_capped_per_docket: int
    groups: list[DocketSheet]
    downloads: list[DownloadResult] = Field(default_factory=list)
    bundle: BundleDownloadResult | None = None


def file_list_url(accession_number: str) -> str:
    return f"{ELIBRARY_UI_BASE}/filelist?accession_number={accession_number}"


def truncate_description(text: str, limit: int = DESCRIPTION_MAX_LEN) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def availability_label(code: str) -> str:
    mapping = {
        "p": "Public",
        "c": "CEII",
        "s": "Protected",
        "n": "Privileged",
    }
    return mapping.get((code or "p").lower(), code or "Public")


def hit_to_summary(hit: SearchHit) -> FilingSummary:
    return FilingSummary(
        accession_number=hit.accession_number,
        filed_date=hit.filed_date,
        issued_date=hit.issued_date or None,
        description=truncate_description(hit.description),
        docket_numbers=hit.docket_numbers,
        category=hit.category,
        class_types=[ct.label() for ct in hit.class_types if ct.label()],
        availability=availability_label(hit.avail_code),
        industry=hit.libraries,
        files=[
            FileSummary(
                file_id=t.file_id,
                file_name=t.file_name,
                file_type=t.file_type or t.file_format,
                file_size=t.file_size,
                description=t.file_desc,
            )
            for t in hit.transmittals
        ],
        url=file_list_url(hit.accession_number),
    )


def detect_nonpublic_counterpart(files: list[FileSummary]) -> tuple[bool, str | None]:
    """Infer whether a sealed counterpart exists from file naming convention.

    A filer who labels one version "PUBLIC" or "REDACTED" is distinguishing it
    from a sealed version on the same accession. This is a heuristic signal so a
    caller knows to move for access; it never exposes protected content.
    """
    for item in files:
        for text in (item.file_name, item.description):
            candidate = (text or "").strip().lower()
            if not candidate:
                continue
            if any(keyword in candidate for keyword in NONPUBLIC_KEYWORDS):
                return True, "file_naming_convention"
            prefix = re.match(NONPUBLIC_PREFIX_RE, candidate)
            if not prefix:
                continue
            remainder = candidate[prefix.end() :]
            if remainder.startswith(NONPUBLIC_PREFIX_EXCEPTIONS):
                continue
            return True, "file_naming_convention"
    return False, None


def hit_to_detail(hit: SearchHit) -> FilingDetail:
    summary = hit_to_summary(hit)
    has_counterpart, basis = detect_nonpublic_counterpart(summary.files)
    return FilingDetail(
        **summary.model_dump(),
        affiliations=hit.affiliations,
        has_nonpublic_counterpart=has_counterpart,
        nonpublic_counterpart_basis=basis,
    )
