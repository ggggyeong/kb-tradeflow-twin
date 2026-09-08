"""Optional free CPU OCR for scanned PDFs; native text is never used by this backend."""

from pathlib import Path
from typing import Any

from app.schemas.document import LayoutDocument, LayoutLine, LayoutPage


class RapidOcrBackend:
    name = "rapidocr_onnx"

    def __init__(self, *, engine: Any = None) -> None:
        self._engine = engine

    def extract(self, path: Path) -> LayoutDocument:
        import numpy as np
        import pymupdf

        if self._engine is None:
            from rapidocr import RapidOCR

            self._engine = RapidOCR()
        pages = []
        with pymupdf.open(path) as pdf:
            for number, page in enumerate(pdf, 1):
                scale = min(2.5, 2400 / max(page.rect.width, page.rect.height))
                pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB)
                pixels = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                    pix.height, pix.width, 3
                )
                result = self._engine(pixels[:, :, ::-1].copy())  # RapidOCR ndarray is BGR.
                if result.boxes is None or not result.txts:
                    raise ValueError("OCR did not recognize this page")
                lines = []
                for box, text, score in zip(result.boxes, result.txts, result.scores, strict=True):
                    if not text.strip():
                        continue
                    xs, ys = box[:, 0] / pix.width, box[:, 1] / pix.height
                    coords = [float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())]
                    lines.append(
                        LayoutLine(
                            text=text,
                            page=number,
                            bbox=[max(0, min(1, v)) for v in coords],
                            confidence=float(score),
                            source=self.name,
                        )
                    )
                if not lines:
                    raise ValueError("OCR returned no text lines")
                pages.append(
                    LayoutPage(page=number, width=pix.width, height=pix.height, lines=lines)
                )
        return LayoutDocument(backend=self.name, pages=pages)
