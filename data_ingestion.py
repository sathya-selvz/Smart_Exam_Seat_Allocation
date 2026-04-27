import re
from typing import Dict, List, Optional, Tuple


def normalize_rollnum(value: Optional[str]) -> str:
    return str(value or "").strip().upper()


def parse_roll_components(rollnum: str) -> Tuple[int, str, int]:
    text = normalize_rollnum(rollnum)
    match = re.match(r"^(\d+)([A-Z]+)(\d+)$", text)
    if match:
        return int(match.group(1)), match.group(2), int(match.group(3))

    tail_match = re.search(r"(\d+)$", text)
    serial = int(tail_match.group(1)) if tail_match else 0
    return 9999, text, serial


def roll_sort_key(rollnum: str) -> Tuple[int, str, int, str]:
    batch, dept, serial = parse_roll_components(rollnum)
    return batch, dept, serial, normalize_rollnum(rollnum)


def department_key_from_roll_or_meta(rollnum: str, year_label: Optional[str], sheet_name: Optional[str]) -> str:
    roll = normalize_rollnum(rollnum)
    match = re.match(r"^(\d+)([A-Z]+)\d+$", roll)
    if match:
        return f"{match.group(1)}{match.group(2)}"

    year_prefix = {
        "SecondYear": "24",
        "ThirdYear": "23",
        "FourthYear": "22",
    }
    return f"{year_prefix.get(year_label, '00')}{str(sheet_name or 'UNK').upper()}"


def clean_and_dedupe_students(
    records: List[dict],
    year_label: str,
    sheet_name: str,
    existing_rolls: Optional[set] = None,
) -> Tuple[List[dict], Dict[str, int]]:
    existing_rolls = existing_rolls or set()
    seen = set(existing_rolls)

    raw_count = len(records)
    unique_records = []
    duplicate_in_file = 0
    duplicate_existing = 0

    for record in records:
        roll = normalize_rollnum(record.get("rollnum"))
        if not roll:
            continue

        if roll in seen:
            if roll in existing_rolls:
                duplicate_existing += 1
            else:
                duplicate_in_file += 1
            continue

        seen.add(roll)
        normalized = dict(record)
        normalized["rollnum"] = roll
        batch, dept, serial = parse_roll_components(roll)
        normalized["roll_batch"] = batch
        normalized["roll_dept"] = dept
        normalized["roll_serial"] = serial
        normalized["sheet_name"] = sheet_name
        normalized["Year"] = year_label
        normalized["classroom"] = None
        unique_records.append(normalized)

    unique_records.sort(key=lambda row: roll_sort_key(row["rollnum"]))

    stats = {
        "raw": raw_count,
        "clean": len(unique_records),
        "duplicate_in_file": duplicate_in_file,
        "duplicate_existing": duplicate_existing,
    }
    return unique_records, stats


def build_validation_row(
    sample_rollnum: str,
    year_label: str,
    sheet_name: str,
    raw: int,
    clean: int,
    duplicate_existing: int,
) -> dict:
    return {
        "department": department_key_from_roll_or_meta(sample_rollnum, year_label, sheet_name),
        "expected": clean,
        "actual": raw,
        "duplicates_removed": (raw - clean) + duplicate_existing,
    }
