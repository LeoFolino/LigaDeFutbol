from __future__ import annotations

from pydantic import BaseModel, Field


class GlobalPlayerCreate(BaseModel):
    name: str = Field(min_length=1)
    position: str = ""
    club: str = ""
    nationality: str = ""
    sofifa_id: str = ""
    sofifa_url: str = ""
    sofifa_version: str = ""
    transfermarkt_url: str = ""
    image_url: str = ""
    overall: int | None = Field(default=None, ge=1, le=99)
    market_value_m: float | None = Field(default=None, ge=0)
    market_value_currency: str = "EUR"
    market_value_checked_at: str = ""
    weak_foot: int | None = Field(default=None, ge=1, le=5)
    skill_moves: int | None = Field(default=None, ge=1, le=5)
    international_reputation: int | None = Field(default=None, ge=1, le=5)
    body_type: str = ""
    real_face: str = ""
    release_clause_m: float | None = Field(default=None, ge=0)
    acceleration_type: str = ""
    play_styles: str = ""
    specialities: str = ""
    roles: list[str] = Field(default_factory=list)
    pace: int | None = Field(default=None, ge=1, le=99)
    shooting: int | None = Field(default=None, ge=1, le=99)
    passing: int | None = Field(default=None, ge=1, le=99)
    dribbling: int | None = Field(default=None, ge=1, le=99)
    defending: int | None = Field(default=None, ge=1, le=99)
    physical: int | None = Field(default=None, ge=1, le=99)
    tags: list[str] = Field(default_factory=list)
    notes: str = ""


class GlobalPlayerPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    position: str | None = None
    club: str | None = None
    nationality: str | None = None
    sofifa_id: str | None = None
    sofifa_url: str | None = None
    sofifa_version: str | None = None
    transfermarkt_url: str | None = None
    image_url: str | None = None
    overall: int | None = Field(default=None, ge=1, le=99)
    market_value_m: float | None = Field(default=None, ge=0)
    market_value_currency: str | None = None
    market_value_checked_at: str | None = None
    weak_foot: int | None = Field(default=None, ge=1, le=5)
    skill_moves: int | None = Field(default=None, ge=1, le=5)
    international_reputation: int | None = Field(default=None, ge=1, le=5)
    body_type: str | None = None
    real_face: str | None = None
    release_clause_m: float | None = Field(default=None, ge=0)
    acceleration_type: str | None = None
    play_styles: str | None = None
    specialities: str | None = None
    roles: list[str] | None = None
    pace: int | None = Field(default=None, ge=1, le=99)
    shooting: int | None = Field(default=None, ge=1, le=99)
    passing: int | None = Field(default=None, ge=1, le=99)
    dribbling: int | None = Field(default=None, ge=1, le=99)
    defending: int | None = Field(default=None, ge=1, le=99)
    physical: int | None = Field(default=None, ge=1, le=99)
    tags: list[str] | None = None
    notes: str | None = None
