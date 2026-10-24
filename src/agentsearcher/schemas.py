import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    query: str = Field(min_length=1)
    search_depth: Literal["basic", "advanced", "fast", "ultra-fast"] = "basic"
    topic: Literal["general", "news", "finance"] = "general"
    max_results: int = Field(5, ge=0, le=20)
    chunks_per_source: int = Field(3, ge=1, le=3)
    time_range: Literal["day", "week", "month", "year", "d", "w", "m", "y"] | None = None
    days: int | None = Field(None, ge=1)
    start_date: date | None = None
    end_date: date | None = None
    include_answer: bool | Literal["basic", "advanced"] = False
    include_raw_content: bool | Literal["markdown", "text"] = False
    include_images: bool = False
    include_image_descriptions: bool = False
    include_favicon: bool = False
    include_published_date: bool = False
    filter_by_published_date: bool = False
    include_domains: list[str] = Field(default_factory=list, max_length=300)
    exclude_domains: list[str] = Field(default_factory=list, max_length=150)
    include_domains_mode: Literal["restrict", "prefer"] | None = None
    country: str | None = None
    language: str | None = None
    filter_by_language: bool = False
    auto_parameters: bool = False
    exact_match: bool = False
    safe_search: bool = False
    dedup: bool = True
    max_per_domain: int | None = Field(None, ge=1)
    include_usage: bool = False
    api_key: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "SearchRequest":
        if self.include_domains_mode and not self.include_domains:
            raise ValueError("include_domains_mode requires include_domains")
        if self.filter_by_language and not self.language:
            raise ValueError("filter_by_language requires language")
        return self


class ExtractRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    urls: str | list[str]
    query: str | None = None
    chunks_per_source: int = Field(3, ge=1, le=5)
    extract_depth: Literal["basic", "advanced"] = "basic"
    include_images: bool = False
    include_favicon: bool = False
    format: Literal["markdown", "text"] = "markdown"
    timeout: float | None = Field(None, ge=1.0, le=60.0)
    include_usage: bool = False
    api_key: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "ExtractRequest":
        if isinstance(self.urls, str):
            self.urls = [self.urls]
        if not 1 <= len(self.urls) <= 20:
            raise ValueError("urls must contain 1 to 20 items")
        return self


class MapRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str = Field(min_length=1)
    instructions: str | None = None
    max_depth: int = Field(1, ge=1, le=5)
    max_breadth: int = Field(20, ge=1, le=500)
    limit: int = Field(50, ge=1)
    select_paths: list[str] = Field(default_factory=list)
    select_domains: list[str] = Field(default_factory=list)
    exclude_paths: list[str] = Field(default_factory=list)
    exclude_domains: list[str] = Field(default_factory=list)
    allow_external: bool = True
    timeout: float = Field(150.0, ge=10.0, le=150.0)
    include_usage: bool = False
    api_key: str | None = None

    @field_validator("select_paths", "select_domains", "exclude_paths", "exclude_domains")
    @classmethod
    def _regexes(cls, patterns: list[str]) -> list[str]:
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f"invalid regex {pattern!r}: {e}") from e
        return patterns

    @field_validator("url")
    @classmethod
    def _scheme(cls, url: str) -> str:
        return url if "://" in url else f"https://{url}"


class CrawlRequest(MapRequest):
    chunks_per_source: int = Field(3, ge=1, le=5)
    extract_depth: Literal["basic", "advanced"] = "basic"
    format: Literal["markdown", "text"] = "markdown"
    include_images: bool = False
    include_favicon: bool = False
    dedup: bool = True
