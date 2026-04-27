from typing import Dict, List, Optional, Tuple


def _is_available_for_date(availability: str, date_value: str) -> bool:
    text = str(availability or "").strip().lower()
    if not text or text in ("all", "any", "daily"):
        return True
    slots = [item.strip() for item in text.split(",") if item.strip()]
    return date_value.strip().lower() in slots


def _classroom_department_profile(classroom_layout: dict) -> str:
    counts: Dict[str, int] = {}
    for bench in classroom_layout.get("benches", []):
        left = bench.get("left")
        right = bench.get("right")
        if left and left.get("dept"):
            dept = str(left.get("dept")).upper()
            counts[dept] = counts.get(dept, 0) + 1
        if right and right.get("dept"):
            dept = str(right.get("dept")).upper()
            counts[dept] = counts.get(dept, 0) + 1
    if not counts:
        return ""
    return max(counts.items(), key=lambda item: item[1])[0]


def assign_teachers_for_date(
    date_value: str,
    classrooms: List[dict],
    teachers: List[dict],
    global_teacher_load: Optional[Dict[str, int]] = None,
) -> Tuple[Dict[str, dict], List[str], Dict[str, int]]:
    global_teacher_load = global_teacher_load or {}

    used_today = set()
    per_day_load: Dict[str, int] = {}
    assignments: Dict[str, dict] = {}
    warnings: List[str] = []

    room_infos = []
    for classroom in classrooms:
        students = 0
        for bench in classroom.get("benches", []):
            if bench.get("left"):
                students += 1
            if bench.get("right"):
                students += 1
        room_infos.append(
            {
                "classroom": classroom.get("class_name"),
                "student_count": students,
                "dominant_dept": _classroom_department_profile(classroom),
            }
        )

    room_infos.sort(key=lambda room: (-room["student_count"], room["classroom"]))

    for room in room_infos:
        room_name = room["classroom"]
        preferred_dept = room["dominant_dept"]

        candidates = []
        for teacher in teachers:
            teacher_id = str(teacher.get("teacher_id", "")).strip().upper()
            if not teacher_id:
                continue
            if teacher_id in used_today:
                continue
            if not _is_available_for_date(teacher.get("availability", "all"), date_value):
                continue

            max_per_day = int(teacher.get("max_assignments_per_day", 1) or 1)
            today_count = per_day_load.get(teacher_id, 0)
            if today_count >= max_per_day:
                continue

            teacher_dept = str(teacher.get("department", "")).strip().upper()
            same_dept_penalty = 1 if preferred_dept and teacher_dept == preferred_dept else 0
            total_load = global_teacher_load.get(teacher_id, 0)

            candidates.append(
                (
                    same_dept_penalty,
                    total_load,
                    today_count,
                    teacher_id,
                    teacher,
                )
            )

        candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]))

        if not candidates:
            warnings.append(f"No available teacher for {room_name} on {date_value}")
            assignments[room_name] = {
                "teacher_id": "UNASSIGNED",
                "name": "Unassigned",
                "department": "N/A",
            }
            continue

        _, _, _, teacher_id, chosen_teacher = candidates[0]
        used_today.add(teacher_id)
        per_day_load[teacher_id] = per_day_load.get(teacher_id, 0) + 1
        global_teacher_load[teacher_id] = global_teacher_load.get(teacher_id, 0) + 1

        assignments[room_name] = {
            "teacher_id": teacher_id,
            "name": chosen_teacher.get("name", "Unknown"),
            "department": str(chosen_teacher.get("department", "")).strip().upper(),
        }

    return assignments, warnings, global_teacher_load
