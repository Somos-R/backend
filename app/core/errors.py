"""Stable error codes.

Every error response carries a machine-readable `code` next to the human `detail`, so a client
can translate or branch on the code without parsing Spanish text. Codes are part of the API
contract: rename one only together with the clients. `detail` stays as a fallback message.
"""
from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# Used for errors raised by the framework itself (unknown route, wrong method, ...).
STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
}


class ApiError(HTTPException):
    """An HTTP error with a stable, machine-readable code."""

    def __init__(self, code: str, status_code: int, detail: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code


def code_for(exc: StarletteHTTPException) -> str:
    return getattr(exc, "code", None) or STATUS_CODES.get(exc.status_code, f"http_{exc.status_code}")


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "code": code_for(exc)},
        headers=getattr(exc, "headers", None),
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(exc.errors()), "code": "validation_error"},
    )
