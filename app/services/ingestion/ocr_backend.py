from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pypdf import PdfReader

from app.schemas.document import LayoutDocument, LayoutLine, LayoutPage


@runtime_checkable
class OcrBackend(Protocol):
    """Minimal provider contract for producing normalized document layout."""

    name: str

    def extract(self, path: Path) -> LayoutDocument:
        """Return page lines with normalized top-left-origin coordinates."""


@dataclass(frozen=True)
class LabelValueEvidence:
    """A value found next to its label instead of in a flattened text blob."""

    label: str
    value: str
    page: int
    bbox: list[float]
    confidence: float
    direction: str


def _canonical(value: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", value.casefold(), flags=re.UNICODE))


def _label_pattern(label: str) -> re.Pattern[str]:
    tokens = re.findall(r"[^\W_]+", label, flags=re.UNICODE)
    if not tokens:
        return re.compile(r"(?!)")
    separator = r"[\W_]*"
    body = separator.join(re.escape(token) for token in tokens)
    return re.compile(rf"{body}[\s.()]*(?:[:;=\-]\s*)?", re.IGNORECASE)


def _bbox_union(lines: Sequence[LayoutLine]) -> list[float]:
    return [
        round(min(line.bbox[0] for line in lines), 6),
        round(min(line.bbox[1] for line in lines), 6),
        round(max(line.bbox[2] for line in lines), 6),
        round(max(line.bbox[3] for line in lines), 6),
    ]


class LayoutDocumentIndex:
    """Reading-order and geometric label-neighbour queries over a LayoutDocument."""

    def __init__(self, document: LayoutDocument) -> None:
        self.document = document
        self._lines = [
            line
            for page in sorted(document.pages, key=lambda item: item.page)
            for line in sorted(page.lines, key=lambda item: (item.bbox[1], item.bbox[0]))
        ]

    @property
    def lines(self) -> tuple[LayoutLine, ...]:
        return tuple(self._lines)

    def find_value(
        self,
        labels: str | Sequence[str],
        *,
        below_index: int = 0,
        allow_inline: bool = True,
        max_vertical_gap: float = 0.12,
    ) -> LabelValueEvidence | None:
        """Find an inline, right-hand, or vertically adjacent value for a label."""
        aliases = (labels,) if isinstance(labels, str) else tuple(labels)
        for alias in aliases:
            canonical_alias = _canonical(alias)
            pattern = _label_pattern(alias)
            for label_line in self._lines:
                canonical_line = _canonical(label_line.text)
                if canonical_alias not in canonical_line:
                    continue
                matched = pattern.search(label_line.text)
                if matched is None:
                    continue
                suffix = label_line.text[matched.end() :].strip(" \t:;=-")
                if allow_inline and suffix:
                    return LabelValueEvidence(
                        label=alias,
                        value=suffix,
                        page=label_line.page,
                        bbox=list(label_line.bbox),
                        confidence=label_line.confidence,
                        direction="INLINE",
                    )

                right = self._right_neighbours(label_line)
                if right:
                    value_line = right[0]
                    return LabelValueEvidence(
                        label=alias,
                        value=value_line.text.strip(),
                        page=value_line.page,
                        bbox=_bbox_union((label_line, value_line)),
                        confidence=min(label_line.confidence, value_line.confidence),
                        direction="RIGHT",
                    )

                below = self._below_neighbours(label_line, max_vertical_gap=max_vertical_gap)
                if below_index < len(below):
                    value_line = below[below_index]
                    return LabelValueEvidence(
                        label=alias,
                        value=value_line.text.strip(),
                        page=value_line.page,
                        bbox=_bbox_union((label_line, value_line)),
                        confidence=min(label_line.confidence, value_line.confidence),
                        direction="BELOW",
                    )
        return None

    def _right_neighbours(self, label: LayoutLine) -> list[LayoutLine]:
        label_center = (label.bbox[1] + label.bbox[3]) / 2
        height = max(label.bbox[3] - label.bbox[1], 0.01)
        candidates = []
        for line in self._lines:
            if line is label or line.page != label.page:
                continue
            center = (line.bbox[1] + line.bbox[3]) / 2
            if abs(center - label_center) > max(height, 0.018):
                continue
            if line.bbox[0] <= label.bbox[0] + 0.01:
                continue
            candidates.append(line)
        return sorted(candidates, key=lambda item: item.bbox[0])

    def _below_neighbours(
        self,
        label: LayoutLine,
        *,
        max_vertical_gap: float,
    ) -> list[LayoutLine]:
        candidates = []
        for line in self._lines:
            if line is label or line.page != label.page:
                continue
            vertical_gap = line.bbox[1] - label.bbox[3]
            if vertical_gap < -0.004 or vertical_gap > max_vertical_gap:
                continue
            horizontally_related = (
                line.bbox[2] >= label.bbox[0] - 0.03 and line.bbox[0] <= label.bbox[2] + 0.20
            )
            if horizontally_related:
                candidates.append(line)
        return sorted(candidates, key=lambda item: (item.bbox[1], item.bbox[0]))


class NativePdfLayoutBackend:
    """Portable fallback using complete native text lines and deterministic pseudo boxes."""

    name = "native_pdf_layout"

    def extract(self, path: Path) -> LayoutDocument:
        reader = PdfReader(path)
        pages: list[LayoutPage] = []
        blank_pages: list[int] = []
        for page_number, pdf_page in enumerate(reader.pages, start=1):
            width = float(pdf_page.mediabox.width)
            height = float(pdf_page.mediabox.height)
            complete_lines = [
                line.strip()
                for line in (pdf_page.extract_text() or "").splitlines()
                if line.strip()
            ]
            line_step = 0.90 / max(len(complete_lines), 1)
            lines = [
                LayoutLine(
                    text=text,
                    page=page_number,
                    bbox=[
                        0.04,
                        _clamp(0.05 + position * line_step),
                        0.96,
                        _clamp(0.05 + position * line_step + min(line_step * 0.7, 0.025)),
                    ],
                    confidence=1.0,
                    source=self.name,
                )
                for position, text in enumerate(complete_lines)
            ]
            if not lines:
                blank_pages.append(page_number)
            pages.append(
                LayoutPage(
                    page=page_number,
                    width=width,
                    height=height,
                    lines=sorted(lines, key=lambda item: (item.bbox[1], item.bbox[0])),
                )
            )
        if blank_pages:
            raise ValueError(
                f"No usable native text layer on PDF page(s) {blank_pages}: {path.name}"
            )
        return LayoutDocument(
            backend=self.name,
            pages=pages,
            warnings=[
                "텍스트 PDF의 위치 정보는 읽기 순서 기반의 근사값입니다. "
                "정확한 문서 위치는 원본 페이지와 함께 확인해 주세요."
            ],
        )


class PaddleOcrBackend:
    """Default OCR provider with a deterministic native-layout fallback."""

    name = "paddleocr"

    def __init__(self, fallback: OcrBackend | None = None) -> None:
        self.fallback = fallback or NativePdfLayoutBackend()
        self._engine: Any | None = None

    def extract(self, path: Path) -> LayoutDocument:
        try:
            engine = self._load_engine()
            if hasattr(engine, "predict"):
                raw_result = list(engine.predict(input=str(path)))
            else:
                raw_result = engine.ocr(str(path), cls=True)
            document = _normalize_paddle_result(path, raw_result)
            if any(not page.lines for page in document.pages):
                raise ValueError("PaddleOCR did not recognize every page")
            return document
        except Exception as error:
            return self._fallback(path, error)

    def _load_engine(self) -> Any:
        if self._engine is not None:
            return self._engine
        module = import_module("paddleocr")
        paddle_ocr = module.PaddleOCR
        try:
            self._engine = paddle_ocr(
                lang="korean",
                use_doc_orientation_classify=True,
                use_doc_unwarping=True,
                use_textline_orientation=True,
            )
        except TypeError:
            # PaddleOCR 2.x compatibility; 3.x is the preferred portfolio runtime.
            self._engine = paddle_ocr(lang="korean", use_angle_cls=True)
        return self._engine

    def _fallback(self, path: Path, error: Exception) -> LayoutDocument:
        try:
            fallback_document = self.fallback.extract(path)
        except Exception:
            raise RuntimeError(
                "PaddleOCR failed and the PDF has no complete native text layer. "
                "Install/configure PaddleOCR models or provide an OCR-capable backend."
            ) from error
        warning = (
            "PaddleOCR unavailable or failed; used native PDF layout fallback "
            f"({type(error).__name__})."
        )
        return fallback_document.model_copy(
            update={
                "fallback_used": True,
                "warnings": [*fallback_document.warnings, warning],
            }
        )


def _clamp(value: float) -> float:
    return round(min(max(value, 0.0), 1.0), 6)


def _first(payload: dict[str, Any], keys: Sequence[str]) -> Any | None:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return value
    return None


def _as_payload(value: Any) -> dict[str, Any] | None:
    payload = getattr(value, "json", value)
    if callable(payload):
        payload = payload()
    if not isinstance(payload, dict):
        return None
    nested = payload.get("res")
    return nested if isinstance(nested, dict) else payload


def _box_coordinates(value: Any) -> tuple[float, float, float, float] | None:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return None
    if len(value) == 4 and all(isinstance(item, (int, float)) for item in value):
        x0, y0, x1, y1 = (float(item) for item in value)
        return x0, y0, x1, y1
    points = [point.tolist() if hasattr(point, "tolist") else point for point in value]
    if points and all(
        isinstance(point, Sequence) and not isinstance(point, (str, bytes)) and len(point) >= 2
        for point in points
    ):
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
        return min(xs), min(ys), max(xs), max(ys)
    return None


def _normalized_lines(
    entries: Sequence[tuple[str, float, tuple[float, float, float, float]]],
    *,
    page: int,
    page_width: float,
    page_height: float,
) -> list[LayoutLine]:
    if not entries:
        return []
    max_x = max(box[2] for _, _, box in entries)
    max_y = max(box[3] for _, _, box in entries)
    scale = max(max_x / page_width, max_y / page_height, 1.0)
    image_width = max(page_width * scale, 1.0)
    image_height = max(page_height * scale, 1.0)
    return sorted(
        [
            LayoutLine(
                text=text.strip(),
                page=page,
                bbox=[
                    _clamp(box[0] / image_width),
                    _clamp(box[1] / image_height),
                    _clamp(box[2] / image_width),
                    _clamp(box[3] / image_height),
                ],
                confidence=_clamp(confidence),
                source="paddleocr",
            )
            for text, confidence, box in entries
            if text.strip()
        ],
        key=lambda item: (item.bbox[1], item.bbox[0]),
    )


def _v3_entries(
    payload: dict[str, Any],
) -> list[tuple[str, float, tuple[float, float, float, float]]]:
    texts = _first(payload, ("rec_texts", "texts"))
    scores = _first(payload, ("rec_scores", "scores"))
    boxes = _first(payload, ("rec_boxes", "dt_polys", "boxes"))
    if texts is None or boxes is None:
        return []
    texts = texts.tolist() if hasattr(texts, "tolist") else list(texts)
    boxes = boxes.tolist() if hasattr(boxes, "tolist") else list(boxes)
    if scores is None:
        score_values = [1.0] * len(texts)
    else:
        score_values = scores.tolist() if hasattr(scores, "tolist") else list(scores)
    entries = []
    for text, score, box_value in zip(texts, score_values, boxes, strict=False):
        box = _box_coordinates(box_value)
        if box is not None and str(text).strip():
            entries.append((str(text), float(score), box))
    return entries


def _legacy_pages(raw_result: Any) -> list[list[Any]]:
    if not isinstance(raw_result, list) or not raw_result:
        return []

    def is_entry(value: Any) -> bool:
        return (
            isinstance(value, Sequence)
            and not isinstance(value, (str, bytes))
            and len(value) == 2
            and _box_coordinates(value[0]) is not None
        )

    if all(is_entry(item) for item in raw_result):
        return [raw_result]
    return [page for page in raw_result if isinstance(page, list)]


def _normalize_paddle_result(path: Path, raw_result: Any) -> LayoutDocument:
    reader = PdfReader(path)
    page_sizes = [
        (float(page.mediabox.width), float(page.mediabox.height)) for page in reader.pages
    ]
    entries_by_page: dict[int, list[tuple[str, float, tuple[float, float, float, float]]]] = {}
    payload_found = False
    for position, result in enumerate(raw_result if isinstance(raw_result, Iterable) else []):
        payload = _as_payload(result)
        if payload is None:
            continue
        entries = _v3_entries(payload)
        if not entries:
            continue
        payload_found = True
        raw_page_index = _first(payload, ("page_index", "page_idx"))
        page_number = int(raw_page_index) + 1 if raw_page_index is not None else position + 1
        entries_by_page.setdefault(page_number, []).extend(entries)

    if not payload_found:
        for position, page_result in enumerate(_legacy_pages(raw_result), start=1):
            entries = []
            for item in page_result:
                if not isinstance(item, Sequence) or len(item) != 2:
                    continue
                box = _box_coordinates(item[0])
                recognized = item[1]
                if (
                    box is None
                    or not isinstance(recognized, Sequence)
                    or isinstance(recognized, (str, bytes))
                    or len(recognized) < 2
                ):
                    continue
                entries.append((str(recognized[0]), float(recognized[1]), box))
            entries_by_page[position] = entries

    pages = []
    for page_number, (width, height) in enumerate(page_sizes, start=1):
        pages.append(
            LayoutPage(
                page=page_number,
                width=width,
                height=height,
                lines=_normalized_lines(
                    entries_by_page.get(page_number, []),
                    page=page_number,
                    page_width=width,
                    page_height=height,
                ),
            )
        )
    return LayoutDocument(backend="paddleocr", pages=pages)


@lru_cache(maxsize=1)
def default_ocr_backend() -> OcrBackend:
    """Reuse one lazy PaddleOCR engine across an ingestion batch."""
    return PaddleOcrBackend()
