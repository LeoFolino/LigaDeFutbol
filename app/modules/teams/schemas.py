from __future__ import annotations

from pydantic import BaseModel, Field


class TeamCreate(BaseModel):
    name: str = Field(min_length=1)
    owner: str = ""
    logo_url: str = ""


class TeamPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    owner: str | None = None
    logo_url: str | None = None


class TeamAssignPlayer(BaseModel):
    query: str | None = None
    player_id: str | None = None
    sofifa_id: str | None = None
    force: bool = False


class TeamLogoPayload(BaseModel):
    filename: str
    data_url: str