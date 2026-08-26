from __future__ import annotations

from pydantic import BaseModel, Field

from app.config import TRANSFERMARKT_BATCH_LIMIT


class FetchRequest(BaseModel):
    url: str


class SofifaFetchRequest(BaseModel):
    url_or_id: str


class TransfermarktFetchRequest(BaseModel):
    url: str
    expected_name: str = ""


class TransfermarktBatchRequest(BaseModel):
    limit: int = Field(
        default=TRANSFERMARKT_BATCH_LIMIT,
        ge=1,
        le=25000,
    )
    skip_updated: bool = True
    stop_after_consecutive_failures: int = Field(
        default=5,
        ge=0,
        le=50,
    )


class CsvImportRequest(BaseModel):
    csv_path: str = "data/raw/players.csv"
    source_dataset: str = ""
    source_version: str = ""
