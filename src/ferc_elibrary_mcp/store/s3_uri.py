from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class S3Location:
    bucket: str
    prefix: str

    @property
    def uri(self) -> str:
        base = f"s3://{self.bucket}"
        if self.prefix:
            return f"{base}/{self.prefix}"
        return base


def parse_s3_uri(value: str) -> S3Location:
    parsed = urlparse(value.strip())
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"Invalid S3 URI: {value!r}. Expected s3://bucket/prefix")
    prefix = (parsed.path or "").lstrip("/")
    return S3Location(bucket=parsed.netloc, prefix=prefix.rstrip("/"))


def object_key(location: S3Location, relative: str) -> str:
    relative = relative.lstrip("/")
    if location.prefix:
        return f"{location.prefix}/{relative}"
    return relative
