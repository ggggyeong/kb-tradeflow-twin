"""Download a small official corpus. No customer files or credentials are sent."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "shinhan_corporate_loan.pdf": "https://img.shinhan.com/sbank2016/form/20110131618000010051WF00001000000001.PDF?1768922540586=",
    "hsbc_trade_finance.pdf": "https://www.hsbc.co.kr/-/media/korea/attachments/korea-kr/cmb-agreement/biz_agreement_029_kr.pdf",
    "hsbc_export_receivables_agreement.pdf": "https://www.hsbc.co.kr/-/media/korea/attachments/korea-kr/cmb-agreement/biz_agreement_036_kr.pdf",
    "boc_fx_forward_sell.pdf": "https://pic.bankofchina.com/bocappd/korea/202604/P020260427380685258568.pdf",
}


def main() -> None:
    target = ROOT / "data" / "knowledge" / "products"
    target.mkdir(parents=True, exist_ok=True)
    for name, url in SOURCES.items():
        path = target / name
        if path.exists():
            data = path.read_bytes()
        else:
            with httpx.stream("GET", url, timeout=45, follow_redirects=True) as response:
                response.raise_for_status()
                parts = []
                size = 0
                for part in response.iter_bytes():
                    size += len(part)
                    if size > 15_000_000:
                        raise ValueError(f"Oversized PDF: {name}")
                    parts.append(part)
                data = b"".join(parts)
            if not data.startswith(b"%PDF") or len(data) > 15_000_000:
                raise ValueError(f"Invalid or oversized PDF: {name}")
            path.write_bytes(data)
        print(
            json.dumps(
                {"file": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            )
        )


if __name__ == "__main__":
    main()
