"""Explicit source-review metadata. Routing is not a lending eligibility decision."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.paths import PRODUCT_CATALOG_PATH
from app.schemas.portfolio import (
    FinancialScenarioCode,
    PortfolioFinancialEvent,
    PortfolioRunRequest,
)


class SourceSection(BaseModel):
    """A reviewed span within one PDF page, not a generated product description."""

    model_config = ConfigDict(extra="forbid")
    section_id: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    page: int = Field(ge=1)
    start_anchor: str = Field(min_length=3)
    end_anchor: str = Field(min_length=3)


class ProductRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: str
    canonical_name: str
    bank_name: str
    source_file: str
    source_url: str | None = None
    source_date: date | None = None
    reviewed_on: date | None = None
    reviewed_sha256: str | None = None
    review_status: Literal["PENDING", "REVIEWED", "MISSING", "ARCHIVED"] = "PENDING"
    enabled: bool = False
    selection_reason: str
    scenario_codes: list[FinancialScenarioCode]
    trade_directions: list[Literal["EXPORT", "IMPORT"]]
    requires_import_payment: bool = False
    requires_export_receivable: bool = False
    include_pages: list[int] = Field(default_factory=list)
    sections: list[SourceSection] = Field(default_factory=list)

    @model_validator(mode="after")
    def reviewed_before_activation(self) -> ProductRecord:
        if len({s.section_id for s in self.sections}) != len(self.sections):
            raise ValueError("Duplicate source section id")
        if self.sections and set(self.include_pages) != {s.page for s in self.sections}:
            raise ValueError("include_pages must match reviewed section pages")
        if any(p < 1 for p in self.include_pages) or len(self.include_pages) != len(
            set(self.include_pages)
        ):
            raise ValueError("include_pages must contain unique positive PDF page numbers")
        if Path(self.source_file).name != self.source_file or not self.source_file.endswith(".pdf"):
            raise ValueError("source_file must be a PDF basename")
        if self.enabled and (
            self.review_status != "REVIEWED"
            or not self.reviewed_on
            or not self.source_url
            or not self.reviewed_sha256
            or len(self.reviewed_sha256) != 64
            or not self.scenario_codes
            or not self.trade_directions
        ):
            raise ValueError(
                "활성화에는 원문 URL·검토일·해시·검색 조건과 REVIEWED 상태가 필요합니다."
            )
        return self


class ProductCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["kb-product-catalog-v3"]
    products: list[ProductRecord] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids(self) -> ProductCatalog:
        ids = [item.product_id for item in self.products]
        files = [item.source_file for item in self.products]
        if len(ids) != len(set(ids)) or len(files) != len(set(files)):
            raise ValueError("Duplicate catalog id or file")
        return self

    @classmethod
    def load(cls, path: Path = PRODUCT_CATALOG_PATH) -> ProductCatalog:
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def eligible(
        self, request: PortfolioRunRequest, event: PortfolioFinancialEvent
    ) -> list[ProductRecord]:
        return [
            p
            for p in self.products
            if p.enabled
            and p.review_status == "REVIEWED"
            and event.scenario_code in p.scenario_codes
            and request.trade_direction in p.trade_directions
            and (not p.requires_import_payment or event.payment_purpose == "IMPORT")
            and (not p.requires_export_receivable or request.export_receivable_confirmed)
        ]
