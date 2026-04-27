import csv
from io import StringIO
from typing import List


def to_csv_bytes(rows: List[dict]) -> bytes:
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['Date', 'Time', 'Classroom', 'Teacher', 'Subject', 'Students'])
    for row in rows:
        writer.writerow([
            row.get('date', ''),
            row.get('time', 'N/A'),
            row.get('classroom', ''),
            row.get('teacher', 'Unassigned'),
            row.get('subject', 'N/A'),
            row.get('student_count', 0),
        ])
    return buffer.getvalue().encode('utf-8')
