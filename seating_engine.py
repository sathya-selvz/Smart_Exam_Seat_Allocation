from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Tuple


BENCHES_PER_CLASSROOM = 30
SEATS_PER_BENCH = 2
CLASSROOM_CAPACITY = BENCHES_PER_CLASSROOM * SEATS_PER_BENCH


@dataclass(frozen=True)
class StudentSeatCandidate:
    rollnum: str
    department: str
    year: str
    exam_id: str
    class_key: str


@dataclass
class AllocationStats:
    total_students: int
    seated_students: int
    unseated_students: int
    benches_with_two_students: int
    benches_with_single_student: int
    conflict_attempts_without_fit: int


def _normalize_department(value: Optional[str]) -> str:
    return str(value or "UNKNOWN").strip().upper()


def _normalize_year(value: Optional[str]) -> str:
    return str(value or "UNKNOWN").strip()


def _normalize_exam(value: Optional[str]) -> str:
    exam = str(value or "UNKNOWN").strip()
    return exam if exam else "UNKNOWN"


def _class_key(year: str, department: str) -> str:
    return f"{year}::{department}"


def _year_rank(year: str) -> int:
    order = {
        "FourthYear": 0,
        "ThirdYear": 1,
        "SecondYear": 2,
    }
    return order.get(year, 99)


def _is_compatible(left_student: StudentSeatCandidate, right_student: StudentSeatCandidate) -> bool:
    if left_student.department == right_student.department:
        return False
    if left_student.exam_id == right_student.exam_id:
        return False
    return True


def build_candidates_for_date(students: List[dict], exam_date: str) -> List[StudentSeatCandidate]:
    candidates: List[StudentSeatCandidate] = []
    for student in students:
        subjects = student.get("subject") or []
        matched_exam: Optional[str] = None
        for subject in subjects:
            if str(subject.get("date", "")).strip() == exam_date:
                matched_exam = _normalize_exam(subject.get("subject"))
                break
        if not matched_exam:
            continue

        rollnum = str(student.get("rollnum", "")).strip()
        if not rollnum:
            continue

        year = _normalize_year(student.get("Year"))
        dept = _normalize_department(student.get("sheet_name"))
        candidates.append(
            StudentSeatCandidate(
                rollnum=rollnum,
                department=dept,
                year=year,
                exam_id=matched_exam,
                class_key=_class_key(year, dept),
            )
        )
    return candidates


def _build_class_queues(candidates: List[StudentSeatCandidate]) -> Tuple[List[str], Dict[str, Deque[StudentSeatCandidate]]]:
    grouped: Dict[str, List[StudentSeatCandidate]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate.class_key, []).append(candidate)

    # Preserve ingestion/query order; input should already be cleaned and sorted.

    ordered_keys = sorted(
        grouped.keys(),
        key=lambda key: (
            _year_rank(key.split("::", 1)[0]),
            -len(grouped[key]),
            key,
        ),
    )
    queues: Dict[str, Deque[StudentSeatCandidate]] = {
        key: deque(grouped[key]) for key in ordered_keys
    }
    return ordered_keys, queues


def _pop_next_from_order(class_order: List[str], queues: Dict[str, Deque[StudentSeatCandidate]], start_idx: int) -> Tuple[Optional[StudentSeatCandidate], int]:
    if not class_order:
        return None, 0

    n = len(class_order)
    idx = start_idx % n
    checked = 0
    while checked < n:
        class_key = class_order[idx]
        queue = queues[class_key]
        if queue:
            return queue.popleft(), idx
        idx = (idx + 1) % n
        checked += 1
    return None, start_idx


def _pop_compatible_from_queue(
    queue: Deque[StudentSeatCandidate],
    left_student: StudentSeatCandidate,
) -> Optional[StudentSeatCandidate]:
    if not queue:
        return None

    scanned: List[StudentSeatCandidate] = []
    found: Optional[StudentSeatCandidate] = None
    max_scan = len(queue)

    for _ in range(max_scan):
        candidate = queue.popleft()
        if _is_compatible(left_student, candidate):
            found = candidate
            break
        scanned.append(candidate)

    for item in reversed(scanned):
        queue.appendleft(item)

    return found


def _pop_compatible_from_any_class(
    class_order: List[str],
    queues: Dict[str, Deque[StudentSeatCandidate]],
    left_student: StudentSeatCandidate,
    start_idx: int,
) -> Tuple[Optional[StudentSeatCandidate], int]:
    if not class_order:
        return None, 0

    n = len(class_order)
    idx = start_idx % n
    checked = 0
    while checked < n:
        class_key = class_order[idx]
        student = _pop_compatible_from_queue(queues[class_key], left_student)
        if student is not None:
            return student, idx
        idx = (idx + 1) % n
        checked += 1
    return None, start_idx


def _remaining_students(queues: Dict[str, Deque[StudentSeatCandidate]]) -> int:
    return sum(len(queue) for queue in queues.values())


def allocate_classrooms(
    candidates: List[StudentSeatCandidate],
    selected_classrooms: List[dict],
) -> Tuple[List[dict], Dict[str, List[dict]], AllocationStats]:
    class_order, class_queues = _build_class_queues(candidates)

    classroom_layouts: List[dict] = []
    seat_updates: Dict[str, List[dict]] = {}
    left_cursor = 0
    right_cursor = 0
    conflict_attempts_without_fit = 0

    for room in selected_classrooms:
        room_name = room.get("class_name")
        room_capacity = int(room.get("capacity", CLASSROOM_CAPACITY) or CLASSROOM_CAPACITY)
        if room_capacity < 2:
            room_capacity = 2
        if room_capacity > CLASSROOM_CAPACITY:
            room_capacity = CLASSROOM_CAPACITY
        if room_capacity % 2 != 0:
            room_capacity -= 1

        benches = [{"bench": bench_no, "left": None, "right": None} for bench_no in range(1, BENCHES_PER_CLASSROOM + 1)]
        left_slots = room_capacity // 2
        right_slots = room_capacity - left_slots

        # Fill left seats first: bench 1..30 (column-wise left side).
        for bench in benches[:left_slots]:
            left_student, left_cursor = _pop_next_from_order(class_order, class_queues, left_cursor)
            if left_student is None:
                break
            bench["left"] = {
                "rollnum": left_student.rollnum,
                "dept": left_student.department,
                "year": left_student.year,
                "exam": left_student.exam_id,
                "class_key": left_student.class_key,
            }
            seat_updates.setdefault(left_student.rollnum, []).append(
                {
                    "seatnum": f"B{bench['bench']}-L",
                    "classroom": room_name,
                    "subject": left_student.exam_id,
                }
            )

        # Fill right seats next: bench 1..30 (column-wise right side).
        for bench in benches[:right_slots]:
            left = bench["left"]
            if left is None:
                continue

            left_student = StudentSeatCandidate(
                rollnum=left["rollnum"],
                department=left["dept"],
                year=left["year"],
                exam_id=left["exam"],
                class_key=left["class_key"],
            )
            right_student, right_cursor = _pop_compatible_from_any_class(
                class_order,
                class_queues,
                left_student,
                right_cursor,
            )
            if right_student is None:
                conflict_attempts_without_fit += 1
                continue

            bench["right"] = {
                "rollnum": right_student.rollnum,
                "dept": right_student.department,
                "year": right_student.year,
                "exam": right_student.exam_id,
                "class_key": right_student.class_key,
            }
            seat_updates.setdefault(right_student.rollnum, []).append(
                {
                    "seatnum": f"B{bench['bench']}-R",
                    "classroom": room_name,
                    "subject": right_student.exam_id,
                }
            )

        used_benches = [bench for bench in benches if bench["left"] is not None or bench["right"] is not None]
        if used_benches:
            classroom_layouts.append(
                {
                    "class_name": room_name,
                    "benches": benches,
                    "bench_count": BENCHES_PER_CLASSROOM,
                    "active_benches": room_capacity // 2,
                    "capacity": room_capacity,
                }
            )

    seated_students = len(seat_updates)
    unseated_students = _remaining_students(class_queues)

    benches_with_two_students = 0
    benches_with_single_student = 0
    for classroom in classroom_layouts:
        for bench in classroom["benches"]:
            if bench["left"] and bench["right"]:
                benches_with_two_students += 1
            elif bench["left"] or bench["right"]:
                benches_with_single_student += 1

    stats = AllocationStats(
        total_students=len(candidates),
        seated_students=seated_students,
        unseated_students=unseated_students,
        benches_with_two_students=benches_with_two_students,
        benches_with_single_student=benches_with_single_student,
        conflict_attempts_without_fit=conflict_attempts_without_fit,
    )
    return classroom_layouts, seat_updates, stats


def validate_layout(classroom_layouts: List[dict]) -> List[dict]:
    issues: List[dict] = []
    for classroom in classroom_layouts:
        class_name = classroom.get("class_name", "UNKNOWN")
        for bench in classroom.get("benches", []):
            left = bench.get("left")
            right = bench.get("right")
            if not left or not right:
                continue
            if left.get("dept") == right.get("dept"):
                issues.append(
                    {
                        "classroom": class_name,
                        "bench": bench.get("bench"),
                        "type": "same_department",
                        "left": left.get("rollnum"),
                        "right": right.get("rollnum"),
                    }
                )
            if left.get("exam") == right.get("exam"):
                issues.append(
                    {
                        "classroom": class_name,
                        "bench": bench.get("bench"),
                        "type": "same_exam",
                        "left": left.get("rollnum"),
                        "right": right.get("rollnum"),
                    }
                )
    return issues
