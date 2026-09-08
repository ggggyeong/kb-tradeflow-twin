"""Reference-style forms with fictional transaction-linked data, never real trade documents."""

from pathlib import Path
from shutil import copy2

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "data/fixtures/tradeflow_example"
DELIVERY = ROOT / "output/pdf/tradeflow-forms"
TERMS = "T/T 30 CALENDAR DAYS AFTER B/L ON BOARD DATE"


class Form:
    """Top-origin drawing helpers; text width checks catch overflowing form cells."""

    def __init__(self, name: str, title: str, *, sans: bool = False) -> None:
        self.canvas = Canvas(str(TARGET / name), pagesize=A4, invariant=1)
        self.canvas.setTitle(title + " - fictional specimen")
        self.canvas.setAuthor("TradeFlow portfolio - synthetic data")
        self.font = "Helvetica" if sans else "Times-Roman"
        self.bold = "Helvetica-Bold" if sans else "Times-Bold"
        self.canvas.setLineWidth(0.55)

    def text(
        self,
        x: float,
        y: float,
        value: str,
        size: float = 10,
        *,
        bold: bool = False,
        width: float | None = None,
    ) -> None:
        font = self.bold if bold else self.font
        if width and self.canvas.stringWidth(value, font, size) > width:
            raise ValueError(f"Text overflows its form cell: {value}")
        self.canvas.setFont(font, size)
        self.canvas.drawString(x, A4[1] - y, value)

    def lines(
        self, x: float, y: float, values: list[str], *, width: float, size: float = 10
    ) -> None:
        for i, value in enumerate(values):
            self.text(x, y + i * (size + 3), value, size, width=width)

    def h(self, y: float, left: float = 42, right: float = 553) -> None:
        self.canvas.line(left, A4[1] - y, right, A4[1] - y)

    def v(self, x: float, top: float, bottom: float) -> None:
        self.canvas.line(x, A4[1] - top, x, A4[1] - bottom)

    def box(self, left: float, top: float, width: float, height: float) -> None:
        self.canvas.rect(left, A4[1] - top - height, width, height)

    def centered(self, y: float, value: str, size: float) -> None:
        x = (A4[0] - self.canvas.stringWidth(value, self.font, size)) / 2
        self.text(x, y, value, size)

    def finish(self) -> None:
        self.centered(818, "SYNTHETIC SAMPLE - NOT VALID FOR TRADE, PAYMENT OR CARRIAGE", 7)
        self.canvas.save()


def invoice() -> None:
    f = Form("02_invoice.pdf", "Commercial Invoice")
    f.centered(59, "COMMERCIAL INVOICE", 19)
    f.centered(75, "FICTIONAL TRANSACTION / FORM SAMPLE", 7)
    f.box(42, 89, 511, 697)
    f.v(288, 89, 425)
    for y in (182, 322, 362):
        f.h(y, 42, 288)
    for y in (133, 182, 269, 362):
        f.h(y, 288, 553)
    f.h(393, 42, 288)
    f.v(173, 362, 393)
    f.h(425)
    f.text(46, 101, "1  Shipper/Seller", 10)
    f.lines(
        51,
        130,
        ["DEMO KOREA EXPORT CO., LTD.", "15 SAMPLE-RO, JUNG-GU,", "SEOUL, REPUBLIC OF KOREA"],
        width=230,
    )
    f.text(293, 101, "7  Invoice No. and date", 10)
    f.text(304, 115, "INV-DEMO-001", 10)
    f.text(420, 115, "2026-09-15", 10)
    f.text(293, 145, "8  L/C No. and date", 10)
    f.text(304, 166, "NOT APPLICABLE - T/T SETTLEMENT", 9)
    f.text(46, 194, "2  Consignee (for account and risk of buyer)", 9)
    f.lines(
        53,
        222,
        ["DEMO US IMPORT LLC", "200 SAMPLE AVENUE,", "LOS ANGELES, CA 90001", "U. S. A."],
        width=224,
    )
    f.text(293, 194, "9  Buyer (if other than consignee)", 10)
    f.lines(
        304, 216, ["SAME AS CONSIGNEE", "DEMO US IMPORT LLC", "LOS ANGELES, U. S. A."], width=235
    )
    f.text(293, 281, "10  Other references", 10)
    f.lines(
        304,
        301,
        ["COUNTRY OF ORIGIN:", "REPUBLIC OF KOREA", "PURCHASE ORDER: PO-DEMO-001"],
        width=235,
        size=9,
    )
    f.text(46, 334, "3  Departure date (scheduled)", 10)
    f.text(54, 353, "SEPTEMBER 18, 2026", 10)
    f.text(46, 374, "4  Vessel/flight", 10)
    f.text(52, 388, "DEMO OCEAN / 026E", 9)
    f.text(178, 374, "5  From", 10)
    f.text(178, 388, "BUSAN, KOREA", 9)
    f.text(46, 405, "6  To", 10)
    f.text(65, 419, "LOS ANGELES, U. S. A.", 9)
    f.text(293, 375, "11  Terms of delivery and payment", 10)
    f.text(304, 392, "F.O.B. BUSAN", 10)
    f.text(297, 414, TERMS, 8.5, width=249)
    columns = [42, 143, 240, 338, 401, 471, 553]
    labels = [
        ["12 Shipping Marks"],
        ["13 No. & kind of", "packages"],
        ["14 Goods", "description"],
        ["15 Quantity"],
        ["16 Unit price"],
        ["17 Amount", "USD"],
    ]
    for x, right, labels_here in zip(columns, columns[1:], labels, strict=False):
        f.lines(x + 3, 438, labels_here, width=right - x - 6, size=8.5)
    for x in columns[1:-1]:
        f.v(x, 425, 461)
    f.lines(
        50,
        501,
        ["DEMO / LA", "LOS ANGELES", "LOT NO. 001", "C/NO. 1-100", "MADE IN KOREA"],
        width=90,
        size=9,
    )
    f.lines(149, 501, ["100 CARTONS", "1 X 40FT", "CONTAINER"], width=88, size=9)
    f.lines(246, 486, ["INDUSTRIAL", "LIGHTING", "COMPONENTS"], width=87, size=9)
    f.text(346, 486, "1,000 PCS", 9, width=53)
    f.text(422, 486, "50.00", 10)
    f.text(493, 486, "50,000.00", 10, width=55)
    f.lines(
        149,
        560,
        ["NET WEIGHT: 3,600 KGS", "GROSS WEIGHT: 4,000 KGS", "MEASUREMENT: 45.000 CBM"],
        width=220,
        size=9,
    )
    f.box(407, 691, 146, 95)
    f.text(414, 707, "Signed by", 10)
    f.lines(414, 732, ["DEMO KOREA EXPORT", "CO., LTD.", "UNSIGNED SAMPLE"], width=131, size=9)
    f.finish()


def bill_of_lading() -> None:
    f = Form("03_bill_of_lading.pdf", "Bill of Lading")
    f.centered(59, "Bill of Lading", 20)
    f.centered(77, "NON-NEGOTIABLE FICTIONAL SPECIMEN", 7)
    f.box(42, 92, 511, 703)
    for y in (145, 190, 240, 281, 324, 375, 550, 650):
        f.h(y)
    f.v(309, 92, 324)
    f.v(172, 240, 324)
    f.text(47, 105, "1  Shipper/Exporter", 10)
    f.lines(
        58,
        121,
        ["DEMO KOREA EXPORT CO., LTD.", "15 SAMPLE-RO, JUNG-GU, SEOUL, KOREA"],
        width=243,
        size=9,
    )
    f.text(315, 105, "11  B/L No. : BL-DEMO-001", 10)
    f.text(47, 158, "2  Consignee", 10)
    f.text(59, 176, "DEMO US IMPORT LLC", 10)
    f.text(47, 202, "3  Notify Party", 10)
    f.lines(
        59, 218, ["DEMO US IMPORT LLC", "200 SAMPLE AVENUE, LOS ANGELES, USA"], width=241, size=9
    )
    f.text(47, 253, "Pre-Carriage by", 10)
    f.text(59, 271, "-", 10)
    f.text(177, 253, "6  Place of Receipt", 10)
    f.text(182, 271, "BUSAN, KOREA", 10)
    f.text(47, 295, "4  Ocean Vessel", 10)
    f.text(59, 313, "DEMO OCEAN", 10)
    f.text(177, 295, "7  Voyage No.", 10)
    f.text(185, 313, "026E", 10)
    f.text(315, 295, "12  Flag", 10)
    for x, label, value in [
        (47, "5 Port of Loading", "BUSAN, KOREA"),
        (171, "8 Port of Discharge", "LOS ANGELES, USA"),
        (303, "9 Place of Delivery", "LOS ANGELES, USA"),
        (432, "10 Final Destination", "LOS ANGELES, USA"),
    ]:
        f.text(x, 339, label, 9)
        f.text(x, 362, value, 9, width=119)
    cols = [42, 134, 215, 321, 410, 479, 553]
    headers = [
        ["13 Container No."],
        ["14 Seal No.", "Marks & No."],
        ["15 No. & Kinds", "of Containers", "or Packages"],
        ["16 Description", "of Goods"],
        ["17 Gross", "Weight"],
        ["Measurement"],
    ]
    for x, right, labels in zip(cols, cols[1:], headers, strict=False):
        f.lines(x + 3, 388, labels, width=right - x - 5, size=8.5)
    for x in cols[1:-1]:
        f.v(x, 375, 510 if x < 321 else 550)
    f.h(510, 42, 321)
    f.text(48, 451, "DEMU0000001", 9)
    f.lines(140, 451, ["DSE000001", "DEMO / LA"], width=72, size=9)
    f.lines(225, 451, ["1 CNTR", "100 CARTONS"], width=92, size=9)
    f.lines(327, 451, ["INDUSTRIAL", "LIGHTING", "COMPONENTS", "(1,000 PCS)"], width=79, size=8.5)
    f.text(415, 451, "4,000 KGS", 9)
    f.text(483, 451, "45.000 CBM", 9)
    f.lines(48, 525, ["Total packages (in words):", "ONE HUNDRED CARTONS ONLY"], width=260, size=9)
    charge_cols = [42, 178, 269, 354, 420, 485, 553]
    for x in charge_cols[1:-1]:
        f.v(x, 550, 650)
    for x, right, labels in zip(
        charge_cols,
        charge_cols[1:],
        [
            ["18 Freight and Charges"],
            ["19 Revenue", "tons"],
            ["20 Rate"],
            ["21 Per"],
            ["22 Prepaid"],
            ["22-1 Collect"],
        ],
        strict=False,
    ):
        f.lines(x + 3, 563, labels, width=right - x - 5, size=8.5)
    f.text(49, 608, "FREIGHT PREPAID", 9)
    f.v(309, 650, 795)
    f.v(172, 650, 744)
    f.h(698, 42, 309)
    f.h(744, 42, 309)
    f.text(47, 663, "23 Freight prepaid at", 9)
    f.text(57, 685, "BUSAN", 9)
    f.text(177, 663, "24 Freight payable at", 9)
    f.text(187, 685, "BUSAN", 9)
    f.text(47, 711, "Total prepaid in", 9)
    f.text(177, 711, "25 No. of original B/L", 9)
    f.text(187, 731, "ZERO - SAMPLE", 9)
    f.text(47, 757, "27 Laden on board vessel", 10)
    f.text(56, 775, "On Board Date: 2026-09-21", 10)
    f.text(315, 663, "26 Place and Date of Issue", 10)
    f.text(326, 681, "BUSAN, 2026-09-22", 10)
    f.text(326, 702, "Signature: UNSIGNED SPECIMEN", 9)
    f.lines(
        315,
        750,
        ["28 DEMO SHIPPING CO., LTD.", "as agent for DEMO OCEAN LINE", "NOT A DOCUMENT OF TITLE"],
        width=232,
        size=9,
    )
    f.finish()


def booking() -> None:
    f = Form("01_booking.pdf", "Booking Receipt Notice", sans=True)
    left, right = 34, 561
    f.text(36, 45, "DSE", 27, bold=True)
    f.text(36, 56, "DEMO SHIPPING EXPRESS", 5.5)
    f.text(182, 44, "Booking Receipt Notice", 17, bold=True)
    f.text(421, 62, "10 SEP 26 15:09   Page : 1/1", 8)
    f.h(68, left, right)
    f.h(73, left, right)
    f.text(37, 87, "To      :  DEMO KOREA EXPORT CO., LTD. / EXPORT DESK", 9)
    f.text(37, 106, "From  :  DEMO SHIPPING EXPRESS / BUSAN OFFICE", 9)
    f.h(114, left, right)
    f.centered(126, "We received a booking request. Please review the details below.", 8)
    f.text(37, 143, "Booking No : BK-DEMO-001", 9, bold=True)
    f.text(247, 143, "Booking Ref. : REF-DEMO-001", 8, bold=True)
    f.text(436, 143, "Date : 2026-09-10", 8, bold=True)
    f.h(151, left, right)

    def row(y: float, label: str, value: str, right_label: str = "", right_value: str = "") -> None:
        f.text(37, y, label, 8.5, bold=True, width=133)
        f.text(172, y, ":", 8)
        f.text(182, y, value, 8, width=132 if right_label else 372)
        if right_label:
            f.text(324, y, right_label, 8, bold=True, width=120)
            f.text(445, y, ":", 8)
            f.text(454, y, right_value, 7.8, width=105)

    row(165, "Booking Staff", "DEMO EXPORT DESK", "Export Ref. No", "EXP-DEMO-001")
    row(182, "Sales Rep", "DEMO SALES TEAM", "B/L No.", "BL-DEMO-001")
    row(199, "Shipper", "DEMO KOREA EXPORT CO., LTD.")
    row(216, "Forwarder", "DEMO LOGISTICS", "Rate Agreement No.", "RA-DEMO-001")
    f.h(224, left, right)
    row(238, "Pre Carrier", "DIRECT SERVICE", "Latest ETA/ETD", "-")
    row(255, "IMO/Flag/Call Sign", "-", "NRT", "-")
    f.h(264, left, right)
    row(278, "Trunk Vessel", "DEMO OCEAN / 026E", "Latest ETA / ETD", "17 Sep / 18 Sep 26")
    row(295, "MRN (Korea only)", "-", "CCN", "-")
    row(312, "IMO/Flag/Call Sign", "DEMO / KOREA / DEMO", "NRT", "-")
    f.h(321, left, right)
    row(335, "Post Carrier", "-", "ETA / ETD", "-")
    row(352, "IMO/Flag/Call Sign", "-", "NRT", "-")
    f.h(361, left, right)
    row(375, "Place of Receipt", "BUSAN", "Proforma 1st vessel ETD", "2026-09-18")
    row(392, "Port of Loading", "BUSAN", "Terminal", "DEMO BUSAN CY")
    row(409, "Port of Discharging", "LOS ANGELES", "Terminal", "DEMO LA TERMINAL")
    row(426, "Place of Delivery", "LOS ANGELES", "Terminal", "DEMO LA TERMINAL")
    row(443, "T/S Port", "NONE", "POD / DEL ETA", "05 Oct / 05 Oct 26")
    row(460, "Ocean Route Type", "DIRECT", "Rcv/Del Term", "CY/CY")
    f.h(468, left, right)
    row(482, "Equipment Type/Q'ty", "40'DRY HIGH CUBE - 1")
    row(499, "Commodity", "LIGHTING COMPONENTS", "Estimated Weight", "4,000.000 KGS")
    f.h(507, left, right)
    row(521, "Empty Pick Up CY", "DEMO BUSAN DEPOT", "Empty Pick Up Date", "15 Sep 26")
    row(538, "Address", "15 SAMPLE PORT ROAD, BUSAN, KOREA")
    row(555, "TEL", "NOT PROVIDED - SAMPLE", "Yard PIC", "DEMO DESK")
    f.h(563, left, right)
    row(577, "Full Return CY", "DEMO BUSAN CY", "Full Return Date", "16 Sep 26")
    row(594, "Address", "30 SAMPLE TERMINAL ROAD, BUSAN, KOREA")
    row(611, "TEL", "NOT PROVIDED - SAMPLE", "Yard PIC", "DEMO DESK")
    f.h(619, left, right)
    row(633, "Doc. Cut-off", "16 Sep 26 11:00", "Customs Cut-off", "16 Sep 26 15:00")
    row(650, "VGM Cut-off", "16 Sep 26 11:00", "Rail Receiving Date", "-")
    row(667, "Port Cargo Cut-off", "16 Sep 26 16:00")
    f.h(675, left, right)
    f.text(37, 690, "Special Cargo Information", 9, bold=True)
    for x, label in [(37, "Dangerous"), (160, "Reefer"), (281, "Awkward"), (410, "Break Bulk")]:
        f.box(x, 700, 12, 12)
        f.text(x + 18, 710, label, 8)
    f.h(722, left, right)
    row(738, "Remarks 1", "SUBJECT TO VESSEL SPACE AND SCHEDULE CHANGES.")
    row(755, "Remarks 2", "FICTIONAL TRAINING FORM. NO CARRIER ACCEPTANCE.")
    f.h(769, left, right)
    f.text(37, 782, "VESSEL SCHEDULE MAY CHANGE WITHOUT NOTICE. DATES ARE ESTIMATES ONLY.", 7.5)
    f.text(37, 795, "THIS IS NOT A BOOKING CONFIRMATION ISSUED BY AN ACTUAL CARRIER.", 7.5)
    f.finish()


def main() -> None:
    import pymupdf

    TARGET.mkdir(parents=True, exist_ok=True)
    DELIVERY.mkdir(parents=True, exist_ok=True)
    invoice()
    bill_of_lading()
    booking()
    with pymupdf.open(TARGET / "02_invoice.pdf") as source, pymupdf.open() as scan:
        page = source[0]
        pixmap = page.get_pixmap(dpi=200)
        output = scan.new_page(width=page.rect.width, height=page.rect.height)
        output.insert_image(output.rect, pixmap=pixmap)
        scan.save(TARGET / "02_invoice_scanned.pdf")
    for name in (
        "01_booking.pdf",
        "02_invoice.pdf",
        "02_invoice_scanned.pdf",
        "03_bill_of_lading.pdf",
    ):
        copy2(TARGET / name, DELIVERY / name)
    print(
        "Created reference-style invoice, B/L, booking and image-only invoice; original kb_doc files unchanged."
    )


if __name__ == "__main__":
    main()
