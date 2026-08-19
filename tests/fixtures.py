from __future__ import annotations

from copy import deepcopy

SAMPLE_SEARCH_HIT = {
    "reference": "{3A5C0B7A-70F0-CA30-9529-772417200000}-{020AAB97-66E2-5005-8110-C31FAFC91712}",
    "documentId": "3A5C0B7A-70F0-CA30-9529-772417200000",
    "description": "Application for Preliminary Permit for Document of Premium Energy Holdings, LLC Ashokan PSP under P-15056.",
    "summary": None,
    "category": "Submittal",
    "acesssionNumber": "20201119-5202",
    "issuedDate": "11/18/2020",
    "filedDate": "11/19/2020",
    "postedDate": "11/20/2020",
    "classTypes": [
        {
            "documentClass": "Application/Petition/Request",
            "documentType": "Application for Preliminary Permit",
        }
    ],
    "availCode": "P",
    "familyValue": "none",
    "libraries": ["Hydro"],
    "score": 85.35,
    "docketNumbers": ["P-15056-000"],
    "transmittals": [
        {
            "fileId": "020AAB97-66E2-5005-8110-C31FAFC91712",
            "fileType": "PDF",
            "fileFormat": "PDF",
            "fileName": "Premium Energy Preliminary Permit App Ashokan PSP.PDF",
            "fileDesc": "Application for Preliminary Permit for Ashokan PSP",
            "fileSize": 472488,
            "transmittalFk": None,
        }
    ],
    "affiliations": [
        {
            "afType": "AUTHOR",
            "affiliation": "Premium Energy Holdings, LLC",
            "lastName": "Rojas",
            "firstInitial": "V",
            "middleInitial": "M",
        }
    ],
}

SAMPLE_SEARCH = {
    "searchHits": [SAMPLE_SEARCH_HIT],
    "totalHits": 1,
    "numHits": 1,
    "success": True,
    "errorMessage": None,
    "searchResultId": None,
}

EMPTY_SEARCH = {
    "searchHits": [],
    "totalHits": 0,
    "numHits": 0,
    "success": True,
    "errorMessage": None,
    "searchResultId": None,
}

SAMPLE_DOCKET_SHEET = {
    "Page": {"totalHits": 1, "numHits": 1, "pageNumber": 0},
    "ErrorList": [],
    "DataList": [
        {
            "DocumentsItem": [
                {
                    "document_id": 0,
                    "category_cd": 0,
                    "DOCKET_TEXT": "P-15056",
                    "SUBDOCKET_TEXT": "000",
                    "DOCKET_CODE": None,
                    "subDocketNumber": 0,
                    "accession_no": "20201119-5202",
                    "accession_date": "11/19/2020",
                    "availability_code": None,
                    "category": "Submittal",
                    "doc_desc": "Application for Preliminary Permit for Ashokan PSP.",
                    "Affiliation_Organization": ["Premium Energy Holdings, LLC"],
                    "filed_date": "11/19/2020",
                    "issued_date": "11/18/2020",
                    "fed_reg_date": None,
                    "comments_due_date": None,
                    "FERC_CITE": None,
                }
            ],
            "AuthorsItem": [],
            "FedCitesItem": [],
        }
    ],
}


def docket_sheet_row(
    accession: str,
    *,
    sub: str = "000",
    docket: str = "EL25-49",
    filed: str = "2026-02-19T00:00:00",
    desc: str = "Motion to Intervene",
    orgs: list[str] | None = None,
) -> dict:
    return {
        "document_id": 0,
        "category_cd": 0,
        "DOCKET_TEXT": docket,
        "SUBDOCKET_TEXT": sub,
        "DOCKET_CODE": None,
        "subDocketNumber": 0,
        "accession_no": accession,
        "accession_date": filed,
        "availability_code": None,
        "category": "Submittal",
        "doc_desc": desc,
        "Affiliation_Organization": orgs if orgs is not None else ["Some Utility, LLC"],
        "filed_date": filed,
        # The sheet always returns the .NET null sentinel for issuance.
        "issued_date": "0001-01-01T00:00:00",
        "fed_reg_date": None,
        "comments_due_date": None,
        "FERC_CITE": None,
    }


def docket_sheet(rows: list[dict], *, reported_total: int | None = None) -> dict:
    """Docket sheet whose Page.totalHits counts associations, as FERC's does."""
    return {
        "Page": {
            "totalHits": reported_total if reported_total is not None else len(rows),
            "numHits": len(rows),
            "pageNumber": 0,
        },
        "ErrorList": [],
        "DataList": [
            {"DocumentsItem": rows, "AuthorsItem": [], "FedCitesItem": []}
        ],
    }


# One filing captioned to three subdockets plus one captioned to one: four
# association rows, two actual filings. This is the EL25-49 shape.
CROSS_DOCKETED_SHEET = docket_sheet(
    [
        docket_sheet_row("20260219-5002", sub="000", filed="2026-02-19T00:00:00"),
        docket_sheet_row("20260219-5002", sub="001", filed="2026-02-19T00:00:00"),
        docket_sheet_row("20260219-5002", sub="002", filed="2026-02-19T00:00:00"),
        docket_sheet_row("20250220-3091", sub="000", filed="2025-02-20T00:00:00"),
    ],
    reported_total=4,
)


def search_with_files(files: list[dict]) -> dict:
    """Search response for one hit carrying the given transmittals."""
    hit = deepcopy(SAMPLE_SEARCH_HIT)
    hit["transmittals"] = [
        {
            "fileId": f.get("fileId", f"FILE{index}"),
            "fileType": f.get("fileType", "PDF"),
            "fileFormat": f.get("fileFormat", "PDF"),
            "fileName": f.get("fileName", ""),
            "fileDesc": f.get("fileDesc", ""),
            "fileSize": f.get("fileSize", 1000),
            "transmittalFk": None,
        }
        for index, f in enumerate(files)
    ]
    return {
        "searchHits": [hit],
        "totalHits": 1,
        "numHits": 1,
        "success": True,
        "errorMessage": None,
        "searchResultId": None,
    }


def search_with_dockets(docket_numbers: list[str], *, avail: str = "P") -> dict:
    hits = []
    for index, docket in enumerate(docket_numbers):
        hit = deepcopy(SAMPLE_SEARCH_HIT)
        hit["docketNumbers"] = [docket]
        hit["acesssionNumber"] = f"20201119-{5202 + index:04d}"
        hit["availCode"] = avail
        hits.append(hit)
    return {
        "searchHits": hits,
        "totalHits": len(hits),
        "numHits": len(hits),
        "success": True,
        "errorMessage": None,
        "searchResultId": None,
    }
