from typing import Dict, List, Tuple


CLASSROOM_NAMES = [
    'ADM 303', 'ADM 304', 'ADM 305', 'ADM 306', 'ADM 307', 'ADM 308', 'ADM 309', 'ADM 310', 'ADM 311',
    'EAB 206', 'EAB 306', 'EAB 401', 'EAB 304', 'EAB 303', 'EAB 104', 'EAB 103', 'EAB 203', 'EAB 204',
    'WAB 206', 'WAB 105', 'WAB 107', 'WAB 207', 'WAB 212', 'WAB 210', 'WAB 211', 'WAB 205', 'WAB 305',
    'WAB 303', 'WAB 403', 'WAB 405', 'EAB 415', 'EAB 416', 'WAB 412', 'EAB 310',
]


def classroom_catalog() -> Dict[str, dict]:
    return {
        name: {
            'class_name': name,
            'column': 2,
            'rows': 30,
            'capacity': 60,
        }
        for name in CLASSROOM_NAMES
    }


def normalize_capacity(value: int, default_capacity: int = 60) -> int:
    try:
        capacity = int(value)
    except (TypeError, ValueError):
        capacity = default_capacity

    if capacity < 2:
        capacity = 2
    if capacity > 60:
        capacity = 60
    # Bench has two seats; keep capacity aligned to full benches.
    if capacity % 2 != 0:
        capacity -= 1
    return capacity


def expand_classrooms_for_capacity(
    selected_classrooms: List[dict],
    student_count: int,
    default_capacity: int = 60,
) -> Tuple[List[dict], List[str], int]:
    catalog = classroom_catalog()

    current = []
    for room in selected_classrooms:
        room_copy = dict(room)
        room_copy['capacity'] = normalize_capacity(room_copy.get('capacity', default_capacity), default_capacity=default_capacity)
        current.append(room_copy)

    selected_names = {room.get('class_name') for room in current}

    added = []
    total_capacity = sum(int(room.get('capacity', default_capacity)) for room in current)
    for room_name in CLASSROOM_NAMES:
        if total_capacity >= student_count:
            break
        if room_name in selected_names:
            continue
        room_payload = dict(catalog[room_name])
        room_payload['capacity'] = normalize_capacity(room_payload.get('capacity', default_capacity), default_capacity=default_capacity)
        current.append(room_payload)
        selected_names.add(room_name)
        added.append(room_name)
        total_capacity += int(room_payload['capacity'])

    deficit = max(student_count - total_capacity, 0)
    return current, added, deficit
