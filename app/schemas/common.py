from typing import Any, Generic, List, Optional, TypeVar
from pydantic import BaseModel

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    items: List[T]
    total: int
    page: int
    size: int
    pages: int


class ErrorResponse(BaseModel):
    status_code: int
    error: str
    detail: Optional[Any] = None
    request_id: Optional[str] = None


class MessageResponse(BaseModel):
    message: str


class HealthResponse(BaseModel):
    status: str
    app: str
    environment: str
    version: str = "1.0.0"
    checks: Optional[dict] = None
