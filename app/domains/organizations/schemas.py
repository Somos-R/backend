import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domains.organizations.enums import LinkStatus


class OrganizationRef(BaseModel):
    """What one side of a link may know about the other: a name and a city, nothing more."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    legal_name: str
    city: str | None


class DirectoryEntry(OrganizationRef):
    link_status: LinkStatus | None  # the state of your link with it, if you have one


class DirectoryResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[DirectoryEntry]


class RequestLinkRequest(BaseModel):
    association_id: uuid.UUID


class RejectLinkRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=200)


class LinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: LinkStatus
    eca: OrganizationRef
    association: OrganizationRef
    rejection_reason: str | None
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime


class LinkListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[LinkResponse]
