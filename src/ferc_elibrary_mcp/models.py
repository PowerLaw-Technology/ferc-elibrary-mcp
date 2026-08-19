from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ferc_elibrary_mcp.config import DESCRIPTION_MAX_LEN, ELIBRARY_UI_BASE


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


class DocketFiling(BaseModel):
    accession_number: str
    description: str
    category: str | None = None
    filed_date: str = ""
    docket: str = ""
    sub_docket: str = ""
    organizations: list[str] = Field(default_factory=list)
    url: str = ""


class DocketSheet(BaseModel):
    docket_number: str
    total_hits: int
    page: int
    page_size: int
    applicants: list[str] = Field(default_factory=list)
    filings: list[DocketFiling] = Field(default_factory=list)


class DownloadResult(BaseModel):
    accession_number: str
    path: str
    size: int
    content_type: str
    file_name: str
    url: str
    skipped: bool = False
    skip_reason: str | None = None


class RelatedCollection(BaseModel):
    query: str | None = None
    document_type: str | None = None
    search_total_hits: int
    dockets_returned: int
    dockets_capped: bool
    filings_capped_per_docket: int
    groups: list[DocketSheet]
    downloads: list[DownloadResult] = Field(default_factory=list)


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


def hit_to_detail(hit: SearchHit) -> FilingDetail:
    summary = hit_to_summary(hit)
    return FilingDetail(**summary.model_dump(), affiliations=hit.affiliations)


DownloadFormat = Literal["native", "pdf"]
