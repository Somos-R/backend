import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, Field, field_validator

from app.core.config import settings

OperationType = Literal["weighing", "collection"]
ItemStatus = Literal["created", "duplicate", "error"]


class SyncOperation(BaseModel):
    """One record the device saved while it had no connection."""

    type: OperationType
    idempotency_key: uuid.UUID  # the device creates it with the record and repeats it on every attempt
    # When the device saved it, with its time zone (a time without one is a 422). It orders the batch and is stored on
    # the record as a fact; it is not the record's own date.
    client_created_at: AwareDatetime
    payload: dict[str, Any]  # the body of the endpoint that creates this kind of record


class SyncBatchRequest(BaseModel):
    operations: list[SyncOperation] = Field(min_length=1)

    @field_validator("operations")
    @classmethod
    def within_the_batch_limit(cls, operations: list[SyncOperation]) -> list[SyncOperation]:
        if len(operations) > settings.sync_batch_max_items:
            raise ValueError(f"Un lote admite como máximo {settings.sync_batch_max_items} operaciones")
        return operations

    @field_validator("operations")
    @classmethod
    def within_the_pickup_limit(cls, operations: list[SyncOperation]) -> list[SyncOperation]:
        pickups = sum(operation.type == "collection" for operation in operations)
        if pickups > settings.sync_batch_max_collections:
            raise ValueError(
                f"Un lote admite como máximo {settings.sync_batch_max_collections} solicitudes de recolección")
        return operations


class SyncItemError(BaseModel):
    code: str
    detail: Any  # a message, or the list of validation errors of the payload


class SyncItemResult(BaseModel):
    index: int  # position of the operation in the request
    type: OperationType
    idempotency_key: uuid.UUID
    client_created_at: datetime
    status: ItemStatus
    record: dict[str, Any] | None  # what the creation endpoint answers (created or duplicate)
    error: SyncItemError | None


class SyncSummary(BaseModel):
    created: int
    duplicate: int
    error: int


class SyncBatchResponse(BaseModel):
    summary: SyncSummary
    results: list[SyncItemResult]  # in the order of the request
