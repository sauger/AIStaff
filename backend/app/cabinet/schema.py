from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CabinetEntryRead(BaseModel):
    id: str
    kind: Literal["file", "folder"]
    name: str
    path: str
    parent_path: str = ""
    size_bytes: int = 0
    content_type: str | None = None
    sha256: str | None = None
    source: str = "console"
    created_at: str
    updated_at: str

    model_config = ConfigDict(from_attributes=True)


class CabinetListResponse(BaseModel):
    agent_id: str
    path: str
    can_write: bool
    used_bytes: int
    max_file_bytes: int
    max_cabinet_bytes: int
    entries: list[CabinetEntryRead] = Field(default_factory=list)


class CabinetFolderCreateRequest(BaseModel):
    tenant_id: str
    path: str = ""
    name: str = Field(min_length=1, max_length=180)


class CabinetUploadRequest(BaseModel):
    tenant_id: str
    path: str = ""
    filename: str = Field(min_length=1, max_length=180)
    content_base64: str
    overwrite: bool = False


class CabinetMoveRequest(BaseModel):
    tenant_id: str
    path: str = Field(min_length=1)
    destination_path: str = Field(min_length=1)
    overwrite: bool = False


class CabinetFindMatch(BaseModel):
    path: str
    name: str
    kind: Literal["file", "folder"]
    size_bytes: int = 0


class CabinetFindResult(BaseModel):
    query: str
    matches: list[CabinetFindMatch] = Field(default_factory=list)
    needs_clarification: bool = False
    message: str = ""


class CabinetProduceRequest(BaseModel):
    template: str = Field(min_length=1)
    destination_path: str | None = None
    overwrite: bool = False
    replacements: dict[str, str] = Field(default_factory=dict)


class CabinetSaveRequest(BaseModel):
    workspace_path: str = Field(min_length=1)
    cabinet_path: str = Field(min_length=1)
    overwrite: bool = False


class CabinetRuntimeFile(BaseModel):
    path: str
    workspace_path: str
    name: str
    size_bytes: int
    content_type: str
    sha256: str
    overwritten: bool = False
    details: dict[str, Any] = Field(default_factory=dict)
