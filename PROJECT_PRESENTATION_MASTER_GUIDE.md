# Exam Hall Management System - Presentation Master Guide

## 1) Project One-Liner
Exam Hall Management System is a Flask + MongoDB based web application that automates exam operations: student ingestion, timetable mapping, classroom capacity planning, anti-malpractice seating allocation, teacher invigilation assignment, and report export (PDF/CSV).

## 2) Problem Statement
Manual exam seating and invigilator assignment are error-prone and slow.

Typical problems solved by this project:
- Duplicate student records and inconsistent roll numbers.
- Wrong or missing timetable-to-student mapping.
- Classroom under/over-utilization.
- Non-compliant seating (same department or same exam adjacent).
- Manual invigilation duty planning without fairness.
- Difficulty sharing student-wise and admin-wise reports.

## 3) Core Objectives
- Upload and normalize student data for multiple academic years.
- Upload timetables and attach subject schedules to students.
- Configure classrooms with custom capacity.
- Generate rule-compliant bench seating.
- Assign invigilators fairly and by availability.
- Provide roster and PDF/CSV outputs for operations.
- Provide a student self-service seat lookup portal.

## 4) Technology Stack
- Backend: Flask
- Database: MongoDB Atlas via PyMongo
- Authentication: Flask-Bcrypt
- Excel Parsing: OpenPyXL
- PDF Export: ReportLab
- Frontend: Jinja templates + Bootstrap + custom CSS

## 5) High-Level Architecture
- Request/UI layer: HTML templates and Flask routes.
- Service/logic layer:
  - Ingestion and dedup: data_ingestion.py
  - Seating engine: seating_engine.py
  - Classroom allocator: classroom_allocator.py
  - Teacher assignment: teacher_assignment.py
  - Exports: pdf_export.py, invigilator_roster.py
- Persistence layer: MongoDB collections (users, student, teachers).
- Static state layer: static/dates.txt and static/stuarrange.txt.

## 6) Data Model

### users collection
- username
- password (bcrypt-hashed)

### student collection
- name
- rollnum
- original_rollnum (optional)
- Year
- sheet_name
- roll_batch, roll_dept, roll_serial
- subject (list of exam dictionaries)
- seatnum (list of assigned seat dictionaries)
- classroom (initially None)

### teachers collection
- teacher_id
- name
- department
- availability
- max_assignments_per_day

## 7) End-to-End Workflow
1. Admin logs in.
2. Admin uploads student Excel files (Second, Third, Fourth year).
3. System normalizes and deduplicates roll numbers; stores students.
4. Admin uploads timetable Excel files.
5. System maps timetable to student documents and updates dates list.
6. Admin selects classrooms and per-room capacities.
7. System generates seating by date with compatibility rules.
8. System assigns invigilators per room/date.
9. System produces seating preview, roster, timetable PDF, roster CSV.
10. Student searches by roll number and can download personal seating PDF.

## 8) Core Concepts You Should Explain in Presentation
- Persisted state vs session state:
  - MongoDB and static files keep data across app restarts.
- Idempotent generation flow:
  - Existing seating fields are cleared before new generation.
- Capacity-aware allocation:
  - Rooms can be expanded automatically when demand exceeds selected capacity.
- Constraint-based seating:
  - Same-department and same-exam adjacency is prevented on the same bench.
- Fairness heuristics:
  - Teacher assignment minimizes overload and respects availability.
- Validation-first ingestion:
  - Duplicate handling and integrity checks are run before seating generation.

## 9) Algorithms (Detailed)

### 9.1 Student Ingestion + Dedup Algorithm
Input: Excel rows per year/sheet.

Steps:
1. Normalize roll numbers to uppercase trimmed text.
2. Parse roll components (batch, dept, serial) for sorting.
3. Remove in-file duplicates and duplicates already in DB.
4. Attach normalized metadata: Year, sheet_name, sort keys.
5. Sort deterministically by roll_sort_key.
6. Insert cleaned records into MongoDB.
7. Build validation report: expected, actual, duplicates removed.

Output: Clean student dataset and validation metrics.

### 9.2 Timetable Mapping Algorithm
Input: timetable sheets and existing students.

Steps:
1. Read all timetable rows per year.
2. Normalize date and subject fields.
3. Maintain unique exam date list in static/dates.txt.
4. Use department-to-sheet mapping for section alignment.
5. Update student documents by Year + sheet_name.

Output: subject list attached to student documents.

### 9.3 Classroom Capacity Expansion Algorithm
Input: selected rooms, configured capacities, student count for date.

Steps:
1. Normalize each room capacity (even, range 2..60).
2. Compute total available capacity.
3. If insufficient, append rooms from predefined catalog.
4. Continue until enough or catalog exhausted.
5. Return effective rooms + auto-added list + remaining deficit.

Output: capacity-sufficient (or best effort) room list.

### 9.4 Seating Allocation Algorithm (Bench Model)
Input: date-wise candidates and effective classrooms.

Rules:
- Bench has two seats: Left and Right.
- Fill Left seats first, then Right seats.
- Right candidate must satisfy compatibility with left candidate:
  - department must differ
  - exam subject must differ

Steps:
1. Build StudentSeatCandidate objects for a date.
2. Group candidates by class_key (Year::Department).
3. Build class queues ordered by year priority and population.
4. Place left-seat students round-robin across queues.
5. For each left seat, search compatible right-seat candidate.
6. Save seat updates as B{bench}-L or B{bench}-R.
7. Record stats: seated/unseated/conflict attempts.
8. Validate final layout for any rule violations.

Output: classroom layouts, seat updates, allocation stats.

### 9.5 Teacher Assignment Algorithm
Input: date, room layouts, teachers.

Rules:
- Teacher must be available for date.
- Teacher should not repeat in same date session.
- Respect max_assignments_per_day.
- Prefer lower global load and avoid dominant room department match.

Steps:
1. Compute room priority by student count.
2. For each room, build candidate teachers with score tuple:
   (same_department_penalty, global_load, today_load, teacher_id)
3. Pick minimum score candidate.
4. If none available, mark UNASSIGNED and append warning.

Output: per-room teacher map + warnings + updated load state.

## 10) Function-by-Function Explanation

## 10.1 static/converter.py
- excel_to_json(excel_file)
  - Reads all sheets from workbook.
  - Uses first row as headers.
  - Converts each row into dictionary.
  - Normalizes sheet names to lowercase keys.
  - Ensures workbook is closed to avoid file locks.

## 10.2 data_ingestion.py
- normalize_rollnum(value)
  - Canonical roll format (uppercase trimmed string).
- parse_roll_components(rollnum)
  - Extracts numeric batch, alphabetic dept, numeric serial.
  - Fallback parsing if strict pattern is missing.
- roll_sort_key(rollnum)
  - Returns tuple used for deterministic roll ordering.
- department_key_from_roll_or_meta(rollnum, year_label, sheet_name)
  - Builds department identity for integrity reporting.
- clean_and_dedupe_students(records, year_label, sheet_name, existing_rolls)
  - Removes duplicates, enriches student fields, sorts output.
- build_validation_row(sample_rollnum, year_label, sheet_name, raw, clean, duplicate_existing)
  - Builds one validation summary row for reporting.

## 10.3 classroom_allocator.py
- classroom_catalog()
  - Provides predefined classroom inventory with default capacity.
- normalize_capacity(value, default_capacity)
  - Enforces valid/even capacity between 2 and 60.
- expand_classrooms_for_capacity(selected_classrooms, student_count, default_capacity)
  - Auto-adds rooms until capacity meets demand or catalog ends.

## 10.4 seating_engine.py
Data classes:
- StudentSeatCandidate
  - Immutable candidate model for seat assignment.
- AllocationStats
  - Metrics container for generation summary.

Helpers:
- _normalize_department(value)
- _normalize_year(value)
- _normalize_exam(value)
- _class_key(year, department)
- _year_rank(year)
- _is_compatible(left_student, right_student)
- _build_class_queues(candidates)
- _pop_next_from_order(class_order, queues, start_idx)
- _pop_compatible_from_queue(queue, left_student)
- _pop_compatible_from_any_class(class_order, queues, left_student, start_idx)
- _remaining_students(queues)

Public engine functions:
- build_candidates_for_date(students, exam_date)
  - Creates candidate list for one date from student subjects.
- allocate_classrooms(candidates, selected_classrooms)
  - Main seat allocation routine; produces layouts and updates.
- validate_layout(classroom_layouts)
  - Detects rule violations in generated bench pairs.

## 10.5 teacher_assignment.py
- _is_available_for_date(availability, date_value)
  - Checks date eligibility for teacher.
- _classroom_department_profile(classroom_layout)
  - Finds dominant department in a room.
- assign_teachers_for_date(date_value, classrooms, teachers, global_teacher_load)
  - Assigns one teacher per room with fairness and constraints.

## 10.6 pdf_export.py
- build_timetable_pdf(rows)
  - Builds admin timetable/invigilation PDF with styled table.
- build_student_seating_pdf(rollnum, rows)
  - Builds student-specific seating PDF.

## 10.7 invigilator_roster.py
- to_csv_bytes(rows)
  - Converts roster rows to downloadable UTF-8 CSV bytes.

## 10.8 app.py - Core Helpers
- create_default_admin()
  - Ensures default admin exists.
- detect_year_from_filename(filename)
  - Detects year type from uploaded filename keywords.
- insert_students_with_validation(year_data, year_label)
  - Drives per-sheet ingestion and validation summary.
- run_data_integrity_check(remove_duplicates=True)
  - Detects and optionally removes duplicate roll entries from DB.
- load_teachers_from_excel(file_path)
  - Parses teacher file, normalizes, deduplicates by teacher_id.
- _time_for_subject_date(date_value, subject_name)
  - Finds exam time for PDF roster row.
- _classroom_summary_rows(date_value, classroom_layouts)
  - Builds summary rows used in roster and timetable PDF.
- _load_exam_dates()
  - Reads static date list.
- _teachers_for_ui()
  - Returns teacher fields for UI/API use.
- _build_roster_rows()
  - Aggregates all date-wise roster rows from in-memory seating data.

## 10.9 app.py - Route Functions

Authentication and home:
- index()
- register()
- login()
- admin()

Generation navigation:
- generate_seating_options()
- automatic_allocation_page()

Student-facing:
- student()
- student_pdf()

Configuration and uploads:
- classchoose()
- uploadpage()
- teachers_upload()
- view_teachers()
- upload_file()
- display_data()
- timetable()

Data APIs:
- view_timetable()
- view_data()

Seating flow:
- details()
- seating()
- viewseating()
- viewseating1(name)

Exports:
- export_timetable_pdf()
- view_roster()
- export_roster_csv()

Maintenance/reset:
- reset()
- reset_collections()
- reset_users()
- reset_static()
- reset_uploads()
- reset_dates()

## 11) Business Rules Implemented
- Exactly 3 files required for year-wise student upload.
- Exactly 3 files required for year-wise timetable upload.
- No seating generation without students having subject data.
- Capacity must be even and within valid bounds.
- Bench pair cannot share department or exam.
- Teacher assignment respects availability and max/day.

## 12) State and Persistence (Important Viva Point)
- Persistent DB state:
  - Student/timetable/teacher data remains after app restart.
- Persistent static state:
  - static/stuarrange.txt (selected rooms and capacities)
  - static/dates.txt (exam dates)
- Runtime memory state:
  - seating_data, teacher_assignments_data, timetable_pdf_rows, filled

## 13) Error Handling and Safety Measures
- Flash-based user warnings for upload/seating failures.
- Dedup + integrity check before seat generation.
- Safe file naming via secure_filename.
- Workbook close in finally block to prevent locked file issues.
- ReportLab import guarded with explicit runtime message.

## 14) Performance Notes
- Bulk DB updates via UpdateOne + bulk_write for seat saves.
- Queue-based candidate management for scalable seat allocation.
- Deterministic sorting improves reproducibility and debugging.

## 15) Known Limitations (Good to mention in presentation)
- dates.txt and stuarrange.txt are file-based shared state.
- Teacher and seating outputs rely on successful generation sequence.
- No role-based multi-user admin authorization model.
- In-memory runtime flags reset when server restarts.

## 16) Suggested Future Enhancements
- Move static file state to DB transaction model.
- Add audit logs for who generated seating and when.
- Add room layout visualization export image/PDF.
- Add stronger conflict optimization for right-seat fill rate.
- Add retry/circuit handling for MongoDB connectivity issues.

## 17) Demo Script for Presentation (2-4 minutes)
1. Login to admin.
2. Upload students (3 files).
3. Upload timetable (3 files).
4. Upload teachers.
5. Configure classrooms and capacities.
6. Generate seating.
7. Show view seating by date.
8. Show roster and export CSV/PDF.
9. Open student portal and download individual PDF.

## 18) Q&A Ready Answers
- Why queue-based allocation?
  - It is efficient, deterministic, and supports compatibility filtering.
- How do you avoid malpractice?
  - Same-bench compatibility checks block same department and same exam pairs.
- How do you ensure data quality?
  - Ingestion normalization, deduplication, and pre-seating integrity checks.
- Can it scale?
  - Yes for institutional usage due to bulk updates and optimized candidate processing.
- Why classrooms auto-expand?
  - To avoid hard failure when selected capacity is below demand.

## 19) File Map for Faculty Review
- Main orchestrator: app.py
- Ingestion: data_ingestion.py
- Seating core: seating_engine.py
- Classroom planning: classroom_allocator.py
- Teacher planning: teacher_assignment.py
- Exports: pdf_export.py, invigilator_roster.py
- Excel conversion utility: static/converter.py
- UI templates: templates/*
- UI styling: static/admin_ui.css, static/public_ui.css

## 20) Final Summary
This project is a full-stack, workflow-oriented exam operations system. It combines validated data ingestion, constraint-based seating allocation, fairness-aware invigilation assignment, and report generation into a single platform suitable for real college exam administration.
