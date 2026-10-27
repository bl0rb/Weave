from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SourceCatalogCategory = Literal['internal', 'legal', 'reference']
CrawlScope = Literal['page', 'prefix', 'host']


class SourceCatalogEntryBase(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(default='', max_length=4000)
    category: SourceCatalogCategory
    publisher: str = Field(default='', max_length=255)
    start_url: str = Field(min_length=1, max_length=512)
    crawl_scope: CrawlScope = 'prefix'
    include_linked_documents: bool = False
    max_pages: int | None = Field(default=None, ge=1)
    interval_seconds: int | None = Field(default=None, ge=1)
    legal_source: bool = False


class SourceCatalogEntryCreateRequest(SourceCatalogEntryBase):
    pass


class SourceCatalogEntryUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    category: SourceCatalogCategory | None = None
    publisher: str | None = Field(default=None, max_length=255)
    start_url: str | None = Field(default=None, min_length=1, max_length=512)
    crawl_scope: CrawlScope | None = None
    include_linked_documents: bool | None = None
    max_pages: int | None = Field(default=None, ge=1)
    interval_seconds: int | None = Field(default=None, ge=1)
    legal_source: bool | None = None


class SourceCatalogEntryResponse(SourceCatalogEntryBase):
    id: str
    kind: str = 'website'
    # The host the entry's start page lives on -- the website connection an
    # import needs (created on the fly by the "add source" form when missing).
    host: str = ''
    created_at: datetime
    updated_at: datetime

    model_config = {'from_attributes': True}


class SourceCatalogListResponse(BaseModel):
    items: list[SourceCatalogEntryResponse]
    # Whether the caller may create/edit/delete entries (admins).
    can_manage: bool = False
