from io import BytesIO
from typing import List


TABLE_COLUMNS = [
    "Date",
    "Time",
    "Subject",
    "Classroom",
    "Teacher",
    "Students",
]

STUDENT_COLUMNS = [
    "Date",
    "Subject",
    "Seat Number",
    "Classroom",
]


def build_timetable_pdf(rows: List[dict]) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:
        raise RuntimeError("reportlab is required for PDF export. Install dependencies from requirements.txt") from exc

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=24,
        rightMargin=24,
        topMargin=24,
        bottomMargin=24,
    )

    styles = getSampleStyleSheet()
    elements = [Paragraph("Exam Timetable and Invigilation Plan", styles["Title"]), Spacer(1, 12)]

    table_data = [TABLE_COLUMNS]
    for row in rows:
        table_data.append(
            [
                str(row.get("date", "")),
                str(row.get("time", "N/A")),
                str(row.get("subject", "N/A")),
                str(row.get("classroom", "")),
                str(row.get("teacher", "Unassigned")),
                str(row.get("student_count", 0)),
            ]
        )

    table = Table(table_data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2E3A59")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.HexColor("#F7FAFF")]),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )

    elements.append(table)
    doc.build(elements)
    return buffer.getvalue()


def build_student_seating_pdf(rollnum: str, rows: List[dict]) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:
        raise RuntimeError("reportlab is required for PDF export. Install dependencies from requirements.txt") from exc

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=24,
        rightMargin=24,
        topMargin=24,
        bottomMargin=24,
    )

    styles = getSampleStyleSheet()
    elements = [
        Paragraph("Student Exam Seating Schedule", styles["Title"]),
        Spacer(1, 6),
        Paragraph(f"Roll Number: <b>{rollnum}</b>", styles["Heading3"]),
        Spacer(1, 10),
    ]

    table_data = [STUDENT_COLUMNS]
    for row in rows:
        table_data.append(
            [
                str(row.get("date", "")),
                str(row.get("subject", "N/A")),
                str(row.get("seatnum", "")),
                str(row.get("classroom", "")),
            ]
        )

    table = Table(table_data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2E3A59")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.HexColor("#F7FAFF")]),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )

    elements.append(table)
    doc.build(elements)
    return buffer.getvalue()
