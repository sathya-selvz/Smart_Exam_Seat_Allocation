from flask import Flask, flash, render_template, request, redirect, url_for, jsonify, session, Markup, Response
from datetime import datetime
from static.converter import excel_to_json
import os
import time
import json
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from pymongo import MongoClient
from flask_bcrypt import Bcrypt
from pymongo import UpdateOne
from classroom_allocator import classroom_catalog, expand_classrooms_for_capacity, normalize_capacity
from invigilator_roster import to_csv_bytes
from data_ingestion import (
    build_validation_row,
    clean_and_dedupe_students,
    department_key_from_roll_or_meta,
    normalize_rollnum,
)
from teacher_assignment import assign_teachers_for_date
from pdf_export import build_student_seating_pdf, build_timetable_pdf
from seating_engine import (
    BENCHES_PER_CLASSROOM,
    CLASSROOM_CAPACITY,
    allocate_classrooms,
    build_candidates_for_date,
    validate_layout,
)

# Load environment variables from .env file
load_dotenv()

# configuring flask

app = Flask(__name__)
app.debug = True
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
bcrypt = Bcrypt(app)
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'uploads')

# Get the MongoDB connection string from the environment variable
MONGO_URI = os.getenv("MONGO_URI")

# Connect to MongoDB
client = MongoClient(MONGO_URI)

# client = pymongo.MongoClient(
#     "mongodb://localhost:27017")

db = client.Studetails
usercollections = db.users
stucollections = db.student
teachercollections = db.teachers

# Create default admin user if it doesn't exist
def create_default_admin():
    try:
        if not usercollections.find_one({'username': 'admin'}):
            hashed_password = bcrypt.generate_password_hash('admin').decode('utf-8')
            usercollections.insert_one({'username': 'admin', 'password': hashed_password})
            print("✅ Default admin account created (username: admin, password: admin)")
        else:
            print("✅ Admin account already exists")
    except Exception as e:
        print(f"⚠️ Could not create default admin: {e}")

# Initialize default admin
create_default_admin()

# Helper function to generate formatted roll numbers
def detect_year_from_filename(filename):
    name = (filename or "").lower()
    if "second" in name or "2nd" in name or "year2" in name:
        return "SecondYear"
    if "third" in name or "3rd" in name or "year3" in name:
        return "ThirdYear"
    if "fourth" in name or "4th" in name or "year4" in name:
        return "FourthYear"
    return None

def insert_students_with_validation(year_data, year_label):
    report_rows = []
    inserted_total = 0
    skipped_existing_total = 0
    dedup_in_file_total = 0

    if year_data is None:
        return report_rows, inserted_total, skipped_existing_total, dedup_in_file_total

    year_numeric_map = {
        "SecondYear": 2,
        "ThirdYear": 3,
        "FourthYear": 4,
    }
    year_number_default = year_numeric_map.get(year_label, 2)

    for sheet_name, sheet_data in year_data.items():
        raw_records = []
        for item in sheet_data:
            normalized_roll = normalize_rollnum(item.get("rollnum"))
            if not normalized_roll:
                continue

            try:
                year_number = int(item.get("year", year_number_default))
            except (TypeError, ValueError):
                year_number = year_number_default

            raw_records.append({
                **item,
                "original_rollnum": item.get("rollnum"),
                "rollnum": normalized_roll,
                "name": str(item.get("name", "")).strip(),
                "department": str(item.get("department", sheet_name)).strip().upper(),
                "year": year_number,
            })

        existing_rolls = set()
        if raw_records:
            rollnums = [normalize_rollnum(record.get("rollnum")) for record in raw_records if record.get("rollnum")]
            existing_rolls = set(
                stucollections.distinct("rollnum", {"rollnum": {"$in": rollnums}})
            )

        cleaned_records, stats = clean_and_dedupe_students(
            raw_records,
            year_label,
            sheet_name,
            existing_rolls=existing_rolls,
        )
        dedup_in_file_total += stats["duplicate_in_file"]
        skipped_existing = stats["duplicate_existing"]
        skipped_existing_total += skipped_existing
        inserted_total += len(cleaned_records)

        if cleaned_records:
            stucollections.insert_many(cleaned_records)

        sample_roll = cleaned_records[0]["rollnum"] if cleaned_records else ""
        report_rows.append(
            build_validation_row(
                sample_rollnum=sample_roll,
                year_label=year_label,
                sheet_name=sheet_name,
                raw=stats["raw"],
                clean=stats["clean"],
                duplicate_existing=stats["duplicate_existing"],
            )
        )

    return report_rows, inserted_total, skipped_existing_total, dedup_in_file_total


def run_data_integrity_check(remove_duplicates=True):
    docs = list(
        stucollections.find({}, {"_id": 1, "rollnum": 1, "Year": 1, "sheet_name": 1})
    )

    by_roll = {}
    dept_actual = {}
    dept_unique = {}

    for doc in docs:
        roll = normalize_rollnum(doc.get("rollnum"))
        if not roll:
            continue

        year_label = doc.get("Year")
        sheet_name = doc.get("sheet_name")
        dept_key = department_key_from_roll_or_meta(roll, year_label, sheet_name)

        dept_actual[dept_key] = dept_actual.get(dept_key, 0) + 1
        dept_unique.setdefault(dept_key, set()).add(roll)

        if roll not in by_roll:
            by_roll[roll] = []
        by_roll[roll].append(doc)

    duplicate_groups = {roll: rows for roll, rows in by_roll.items() if len(rows) > 1}
    removed_ids = []
    dept_duplicates_removed = {}

    for roll, rows in duplicate_groups.items():
        rows_sorted = sorted(rows, key=lambda row: str(row.get("_id")))
        keep = rows_sorted[0]
        extras = rows_sorted[1:]
        dept_key = department_key_from_roll_or_meta(
            keep.get("rollnum"), keep.get("Year"), keep.get("sheet_name")
        )
        dept_duplicates_removed[dept_key] = dept_duplicates_removed.get(dept_key, 0) + len(extras)
        removed_ids.extend([row["_id"] for row in extras])

    if remove_duplicates and removed_ids:
        stucollections.delete_many({"_id": {"$in": removed_ids}})

    report = []
    all_depts = sorted(set(dept_actual.keys()) | set(dept_unique.keys()) | set(dept_duplicates_removed.keys()))
    for dept in all_depts:
        actual = dept_actual.get(dept, 0)
        expected = len(dept_unique.get(dept, set()))
        duplicates_removed = dept_duplicates_removed.get(dept, 0)
        report.append(
            {
                "department": dept,
                "expected": expected,
                "actual": actual,
                "duplicates_removed": duplicates_removed,
            }
        )

    summary = {
        "total_rows": len(docs),
        "unique_rollnums": len(by_roll),
        "duplicate_rows_detected": len(removed_ids),
        "duplicate_roll_groups": len(duplicate_groups),
        "report": report,
    }
    return summary


def load_teachers_from_excel(file_path):
    payload = excel_to_json(file_path)
    rows = []
    for _, teachers in payload.items():
        rows.extend(teachers)

    cleaned = []
    seen = set()
    for row in rows:
        teacher_id = str(row.get("teacher_id", "")).strip().upper()
        if not teacher_id or teacher_id in seen:
            continue
        seen.add(teacher_id)

        cleaned.append(
            {
                "teacher_id": teacher_id,
                "name": str(row.get("name", "Unknown")).strip() or "Unknown",
                "department": str(row.get("department", "")).strip().upper() or "GEN",
                "availability": str(row.get("availability", "all")).strip() or "all",
                "max_assignments_per_day": int(row.get("max_assignments_per_day", 1) or 1),
            }
        )

    cleaned.sort(key=lambda item: item["teacher_id"])
    return cleaned


def _time_for_subject_date(date_value, subject_name):
    students = stucollections.find(
        {"subject": {"$elemMatch": {"date": date_value}}},
        {"subject": 1},
    )
    for student in students:
        for subject in student.get("subject", []):
            if str(subject.get("date", "")).strip() != str(date_value).strip():
                continue
            if str(subject.get("subject", "")).strip() != str(subject_name).strip():
                continue
            if subject.get("time") is not None:
                text = str(subject.get("time")).strip()
                if text:
                    return text
    return "N/A"


def _classroom_summary_rows(date_value, classroom_layouts):
    rows = []
    for classroom in classroom_layouts:
        subjects = set()
        student_count = 0
        for bench in classroom.get("benches", []):
            left = bench.get("left")
            right = bench.get("right")
            if left:
                student_count += 1
                subjects.add(str(left.get("exam", "Unknown")))
            if right:
                student_count += 1
                subjects.add(str(right.get("exam", "Unknown")))

        teacher = classroom.get("teacher") or {"teacher_id": "UNASSIGNED", "name": "Unassigned"}
        teacher_text = f"{teacher.get('teacher_id', 'UNASSIGNED')} - {teacher.get('name', 'Unassigned')}"

        if not subjects:
            rows.append(
                {
                    "date": date_value,
                    "time": "N/A",
                    "subject": "N/A",
                    "classroom": classroom.get("class_name"),
                    "teacher": teacher_text,
                    "student_count": student_count,
                }
            )
            continue

        for subject_name in sorted(subjects):
            rows.append(
                {
                    "date": date_value,
                    "time": _time_for_subject_date(date_value, subject_name),
                    "subject": subject_name,
                    "classroom": classroom.get("class_name"),
                    "teacher": teacher_text,
                    "student_count": student_count,
                }
            )
    return rows


def _load_exam_dates():
    with open('static/dates.txt', 'r') as file:
        return json.load(file)


def _teachers_for_ui():
    return list(
        teachercollections.find(
            {},
            {"_id": 0, "teacher_id": 1, "name": 1, "department": 1},
        ).sort("teacher_id", 1)
    )


def _build_roster_rows():
    rows = []
    for date_value, layouts in seating_data.items():
        rows.extend(_classroom_summary_rows(date_value, layouts))
    rows.sort(key=lambda row: (row.get("date", ""), row.get("time", ""), row.get("classroom", "")))
    return rows


CLASSROOM_CATALOG = classroom_catalog()
CLASSROOM_NAMES = list(CLASSROOM_CATALOG.keys())

# global variables
listy = []
filled = False
seating_data = {}  # Store seating results for immediate display
teacher_assignments_data = {}
timetable_pdf_rows = []
with open('static/dates.txt', 'r') as datefiles:
    dates = json.load(datefiles)

# routes
# homepage
@app.route('/')
def index():
    return render_template('home.html')

# signup page for admin
@app.route('/admin/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        # Check if the username already exists in the database
        if usercollections.find_one({'username': username}):
            flash('Username already exists', 'registration-error')
            return redirect(url_for('register'))
        
        else:
            # Hash the password before storing
            hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
            # If the username is unique, insert the new user into the database
            usercollections.insert_one(
                {'username': username, 'password': hashed_password})
            flash('Registration successful!', 'registration-success')
            return redirect(url_for('login'))
    else:
        return render_template('adminlogin.html')


# login page for admin
@app.route('/admin/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        # Retrieve the username and password from the form
        username = request.form['username']
        password = request.form['password']
        
        # Check if the username exists in the database
        user = usercollections.find_one({'username': username})
        
        if user and bcrypt.check_password_hash(user['password'], password):
            # If the user exists and password matches, store the username in the session
            session['username'] = username
            return redirect(url_for('admin'))
        else:
            flash('Invalid username or password', 'login-error')
            return redirect(url_for('login'))
    else:
        return render_template('adminlogin.html')


# main page of admin where he can choose the classes
@app.route('/admin')
def admin():
    return render_template('adminhome.html')


@app.route('/generate-seating', methods=['GET'])
def generate_seating_options():
    return render_template('generate_seating_options.html')


@app.route('/generate-seating/automatic', methods=['GET'])
def automatic_allocation_page():
    return redirect(url_for('classchoose'))


# When student enters their rollnumber
    # their corresponding seating is displayed
@app.route('/student', methods=['GET', 'POST'])
def student():
    if request.method == 'POST':
        roll = request.form['roll_num'].strip()
        
        # Try to find student by formatted rollnum (string) or original rollnum (int)
        student_data = stucollections.find_one({'rollnum': roll})
        if not student_data:
            # Try as integer if it's numeric
            try:
                student_data = stucollections.find_one({'rollnum': int(roll)})
            except (ValueError, TypeError):
                pass
        
        # Retrieve the seat number for the student
        seatnum = []
        if student_data is not None and 'seatnum' in student_data and student_data['seatnum'] is not None:
            seatnum = student_data['seatnum']
        return render_template('studentpage.html', roll_num=roll, seat_num=seatnum)
    else:
        return render_template('studentpage.html')


@app.route('/student/pdf', methods=['GET'])
def student_pdf():
    roll = request.args.get('roll', '').strip()
    if not roll:
        flash('Roll number is required for PDF download.', 'error')
        return redirect(url_for('student'))

    student_data = stucollections.find_one({'rollnum': roll})
    if not student_data:
        try:
            student_data = stucollections.find_one({'rollnum': int(roll)})
        except (ValueError, TypeError):
            student_data = None

    if not student_data or not student_data.get('seatnum'):
        flash('No seat assignment found for this roll number.', 'error')
        return redirect(url_for('student'))

    seat_rows = list(student_data.get('seatnum', []))
    seat_rows.sort(key=lambda row: (str(row.get('date', '')), str(row.get('seatnum', ''))))

    try:
        pdf_bytes = build_student_seating_pdf(str(student_data.get('rollnum', roll)), seat_rows)
    except RuntimeError as exc:
        flash(str(exc), 'error')
        return redirect(url_for('student'))

    filename_roll = str(student_data.get('rollnum', roll)).replace(' ', '_')
    return Response(
        pdf_bytes,
        mimetype='application/pdf',
        headers={
            'Content-Disposition': f'attachment; filename=student_seating_{filename_roll}.pdf'
        },
    )


@app.route('/class', methods=['GET'])
def classchoose():
    return render_template('classavailable.html')


# page for uploading student details
@app.route('/uploaddata', methods=['GET'])
def uploadpage():
    return render_template('studentdataupload.html')


@app.route('/teachers', methods=['GET', 'POST'])
def teachers_upload():
    if request.method == 'GET':
        return render_template('teacherupload.html')

    file = request.files.get('file')
    if not file or not file.filename:
        flash('Please select a teacher dataset file.', 'error')
        return render_template('teacherupload.html')

    filename = secure_filename(file.filename)
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(file_path)

    teachers = load_teachers_from_excel(file_path)
    teachercollections.delete_many({})
    if teachers:
        teachercollections.insert_many(teachers)

    flash(f'Teacher dataset uploaded. {len(teachers)} unique teachers available for assignment.', 'success')
    return render_template('teacherupload.html')


@app.route('/viewteachers', methods=['GET'])
def view_teachers():
    teachers = list(
        teachercollections.find(
            {},
            {"_id": 0, "teacher_id": 1, "name": 1, "department": 1, "availability": 1, "max_assignments_per_day": 1},
        ).sort("teacher_id", 1)
    )
    return jsonify(teachers)

# when the data is submitted from /uploaddata or studentdataupload.html the data is processed here
# Here the data is checked and uploaded to the database
    # with sheetname as classname,year,classroom:which is the class they are going to be seated
# the data is also passed to "listy" for later usage in /seating
# finally the uploaded data is displayed in uploadeddata.html


@app.route('/upload', methods=['POST'])
def upload_file():
    files = request.files.getlist('files')

    if not files:
        flash('No files uploaded', 'error')
        return render_template('studentdataupload.html')

    if len(files) != 3:
        flash('Please select exactly 3 files (Second, Third, Fourth Year).', 'error')
        return render_template('studentdataupload.html')

    year_files = {"SecondYear": None, "ThirdYear": None, "FourthYear": None}
    unassigned = []
    for file in files:
        year_label = detect_year_from_filename(file.filename)
        if year_label and year_files[year_label] is None:
            year_files[year_label] = file
        else:
            unassigned.append(file)
    for year_label in ("SecondYear", "ThirdYear", "FourthYear"):
        if year_files[year_label] is None and unassigned:
            year_files[year_label] = unassigned.pop(0)
    if any(year_files[label] is None for label in year_files):
        flash('Could not determine all years from filenames. Please include Second/Third/Fourth in names.', 'error')
        return render_template('studentdataupload.html')

    # Phase 2 ingestion: replace prior student dataset with newly cleaned import.
    stucollections.delete_many({})
    global filled, seating_data, timetable_pdf_rows, teacher_assignments_data
    filled = False
    seating_data = {}
    timetable_pdf_rows = []
    teacher_assignments_data = {}

    file2 = year_files["SecondYear"]
    file3 = year_files["ThirdYear"]
    file4 = year_files["FourthYear"]

    if file2 and file2.filename:
        filename2 = secure_filename(file2.filename)
        file2.save(os.path.join(app.config['UPLOAD_FOLDER'], filename2))
        global data2
        data2 = excel_to_json(os.path.join(
            app.config['UPLOAD_FOLDER'], filename2))
    else:
        data2 = None

    if file3 and file3.filename:
        filename3 = secure_filename(file3.filename)
        file3.save(os.path.join(app.config['UPLOAD_FOLDER'], filename3))
        global data3
        data3 = excel_to_json(os.path.join(
            app.config['UPLOAD_FOLDER'], filename3))
    else:
        data3 = None

    if file4 and file4.filename:
        filename4 = secure_filename(file4.filename)
        file4.save(os.path.join(app.config['UPLOAD_FOLDER'], filename4))
        global data4
        data4 = excel_to_json(os.path.join(
            app.config['UPLOAD_FOLDER'], filename4))
    else:
        data4 = None

    validation_report = []
    inserted_total = 0
    skipped_existing_total = 0
    dedup_in_file_total = 0

    second_report, second_inserted, second_skipped_existing, second_dedup = insert_students_with_validation(data2, "SecondYear")
    third_report, third_inserted, third_skipped_existing, third_dedup = insert_students_with_validation(data3, "ThirdYear")
    fourth_report, fourth_inserted, fourth_skipped_existing, fourth_dedup = insert_students_with_validation(data4, "FourthYear")

    validation_report.extend(second_report)
    validation_report.extend(third_report)
    validation_report.extend(fourth_report)

    inserted_total = second_inserted + third_inserted + fourth_inserted
    skipped_existing_total = second_skipped_existing + third_skipped_existing + fourth_skipped_existing
    dedup_in_file_total = second_dedup + third_dedup + fourth_dedup

    # Clean up any existing duplicates already present in DB from previous uploads.
    integrity_summary = run_data_integrity_check(remove_duplicates=True)
    duplicate_rows_detected = integrity_summary.get("duplicate_rows_detected", 0)

    print("\n=== Dataset Validation Report ===")
    print("Department | Expected | Actual | Duplicates Removed")
    for row in sorted(validation_report, key=lambda item: item["department"]):
        print(
            f"{row['department']} | {row['expected']} | {row['actual']} | {row['duplicates_removed']}"
        )
    print("=================================")

    if dedup_in_file_total > 0 or skipped_existing_total > 0 or duplicate_rows_detected > 0:
        flash(
            f"Upload validated: inserted={inserted_total}, in-file duplicates removed={dedup_in_file_total}, "
            f"already-existing duplicates skipped={skipped_existing_total}, DB duplicates cleaned={duplicate_rows_detected}.",
            'warning',
        )
    else:
        flash(f"Upload validated: inserted={inserted_total}. No duplicates detected.", 'success')

    global listy
    listy = []
    details = []
    details = stucollections.aggregate(
        [{"$group": {"_id": "$subject", "ro": {"$push": "$rollnum"}}}])
    for i in details:
        listy.append(i)

    return render_template('uploadeddata.html', data2=data2, data3=data3, data4=data4)

# page for displaying the data via "GET" method
@app.route('/displaydata', methods=['GET'])
def display_data():
    return render_template('displaydata.html', data2=data2, data3=data3, data4=data4)

# here the timetable is uploaded via timetableupload.html
# the filename is checked
@app.route('/timetable', methods=['GET', 'POST'])
def timetable():
    if request.method == 'POST':
        # Retrieve uploaded files
        files = request.files.getlist('files')

        if not files:
            flash('No files uploaded', 'error')
            return render_template('timetableupload.html')

        if len(files) != 3:
            flash('Please select exactly 3 files (Second, Third, Fourth Year).', 'error')
            return render_template('timetableupload.html')

        year_files = {"SecondYear": None, "ThirdYear": None, "FourthYear": None}
        unassigned = []
        for file in files:
            year_label = detect_year_from_filename(file.filename)
            if year_label and year_files[year_label] is None:
                year_files[year_label] = file
            else:
                unassigned.append(file)
        for year_label in ("SecondYear", "ThirdYear", "FourthYear"):
            if year_files[year_label] is None and unassigned:
                year_files[year_label] = unassigned.pop(0)
        if any(year_files[label] is None for label in year_files):
            flash('Could not determine all years from filenames. Please include Second/Third/Fourth in names.', 'error')
            return render_template('timetableupload.html')

        file2 = year_files["SecondYear"]
        file3 = year_files["ThirdYear"]
        file4 = year_files["FourthYear"]

        # Check if file2 is uploaded
        if file2 and file2.filename:
            filename2 = secure_filename(file2.filename)
            file2.save(os.path.join(app.config['UPLOAD_FOLDER'], filename2))
            global timetable2
            timetable2 = excel_to_json(os.path.join(
                app.config['UPLOAD_FOLDER'], filename2))
        else:
            timetable2 = None

        # Check if file3 is uploaded
        if file3 and file3.filename:
            filename3 = secure_filename(file3.filename)
            file3.save(os.path.join(app.config['UPLOAD_FOLDER'], filename3))
            global timetable3
            timetable3 = excel_to_json(os.path.join(
                app.config['UPLOAD_FOLDER'], filename3))
        else:
            timetable3 = None

        # Check if file4 is uploaded
        if file4 and file4.filename:
            filename4 = secure_filename(file4.filename)
            file4.save(os.path.join(app.config['UPLOAD_FOLDER'], filename4))
            global timetable4
            timetable4 = excel_to_json(os.path.join(
                app.config['UPLOAD_FOLDER'], filename4))
        else:
            timetable4 = None

        # "Year" field is set to "SecondYear"
        # creates a list of the "_id" field values for those documents.
        # It then repeats this process for students in their third and fourth year of study,
        # Fetch student IDs for each year level

        second_year_students = stucollections.find({"Year": "SecondYear"})
        second_year_student_ids = [student["_id"]
                                   for student in second_year_students]
        third_year_students = stucollections.find({"Year": "ThirdYear"})
        third_year_student_ids = [student["_id"]
                                  for student in third_year_students]
        fourth_year_students = stucollections.find({"Year": "FourthYear"})
        fourth_year_student_ids = [student["_id"]
                                   for student in fourth_year_students]

        def get_subject_date(subject):
            date_value = subject.get("date")
            if date_value is not None:
                return date_value
            for key, value in subject.items():
                if isinstance(key, str) and key.strip().lower() == "date":
                    return value
            return None

        def get_subject_name(subject):
            subject_value = subject.get("subject")
            if subject_value is not None:
                return subject_value
            for key, value in subject.items():
                if isinstance(key, str) and key.strip().lower() == "subject":
                    return value
            return None

        # The code first checks if the timetable exists by checking if "timetable2" is not None.
        # If it does exist, the code iterates over the sheets in the timetable ("timetable2.items()"),
        # and for each subject in each sheet, it converts the "date" field to a string in the format '%d-%m-%Y'
        # using the "datetime.fromtimestamp()" and "strftime()" functions.
        # It then checks if the subject date is already in the "dates" list, and if not , adds it to the list.
        # The code then updates the "subject" field for each sheet in the "stucollections"
        # collection based on the sheet name, year level, and student IDs.
        # For each sheet, it uses the "update_many()" method to update the "subject" field of all documents in the collection
        # where the "sheet_name" field is equal to the current sheet, the "Year" field is equal to "SecondYear",
        # and the "_id" field is in the list of second-year student IDs retrieved earlier.

        # The updated "subject" field is set to the contents of the corresponding sheet in the "timetable2" dictionary,
        # which is accessed using the sheet name as the key(e.g., "timetable2["csa"]").
        # Update subjects in the "stucollections" collection based on the uploaded timetables

        if timetable2 is not None:
            for sheet_name, subjects in timetable2.items():
                for subject in subjects:
                    date_value = get_subject_date(subject)
                    if date_value is None:
                        continue
                    subject_name = get_subject_name(subject) or "Unknown"
                    # Handle date from openpyxl (returns datetime object)
                    if isinstance(date_value, datetime):
                        subject_date = date_value.strftime('%d-%m-%Y')
                    else:
                        # Fallback for other formats
                        subject_date = str(date_value)
                    subject['date'] = subject_date
                    subject['subject'] = subject_name
                    if subject_date not in dates:
                        dates.append(subject_date)
            
            # Department to timetable sheet mapping (handles section divisions)
            dept_timetable_mapping = {
                "csa": "cs", "csb": "cs",
                "ec": "ec", "ee": "ee", "ad": "ad", "ce": "ce",
                "me": "me", "mea": "me", "meb": "me",
                "mr": "mr", "rb": "rb",
                "mca": "mca"
            }

            # Update subjects for second year students using actual sheet names
            second_year_sheets = stucollections.distinct("sheet_name", {"Year": "SecondYear"})
            for dept_sheet in second_year_sheets:
                timetable_sheet = dept_timetable_mapping.get(dept_sheet, dept_sheet)
                if timetable_sheet in timetable2:
                    result = stucollections.update_many(
                        {"sheet_name": dept_sheet, "Year": "SecondYear",
                            "_id": {"$in": second_year_student_ids}},
                        {"$set": {"subject": timetable2[timetable_sheet]}}
                    )
                    print(f"Updated {result.modified_count} {dept_sheet} second year students with {timetable_sheet} subjects")

        if timetable3 is not None:
            for sheet_name, subjects in timetable3.items():
                for subject in subjects:
                    date_value = get_subject_date(subject)
                    if date_value is None:
                        continue
                    subject_name = get_subject_name(subject) or "Unknown"
                    # Handle date from openpyxl (returns datetime object)
                    if isinstance(date_value, datetime):
                        subject_date = date_value.strftime('%d-%m-%Y')
                    else:
                        # Fallback for other formats
                        subject_date = str(date_value)
                    subject['date'] = subject_date
                    subject['subject'] = subject_name
                    if subject_date not in dates:
                        dates.append(subject_date)
            
            # Department to timetable sheet mapping
            dept_timetable_mapping = {
                "csa": "cs", "csb": "cs",
                "ec": "ec", "ee": "ee", "ce": "ce",
                "mea": "me", "meb": "me", "me": "me",
                "mr": "mr", "ad": "ad", "rb": "rb",
                "mca": "mca"
            }

            # Update subjects for third year students using actual sheet names
            third_year_sheets = stucollections.distinct("sheet_name", {"Year": "ThirdYear"})
            for dept_sheet in third_year_sheets:
                timetable_sheet = dept_timetable_mapping.get(dept_sheet, dept_sheet)
                if timetable_sheet in timetable3:
                    result = stucollections.update_many(
                        {"sheet_name": dept_sheet, "Year": "ThirdYear",
                            "_id": {"$in": third_year_student_ids}},
                        {"$set": {"subject": timetable3[timetable_sheet]}}
                    )
                    print(f"Updated {result.modified_count} {dept_sheet} third year students with {timetable_sheet} subjects")

        if timetable4 is not None:
            for sheet_name, subjects in timetable4.items():
                for subject in subjects:
                    date_value = get_subject_date(subject)
                    if date_value is None:
                        continue
                    subject_name = get_subject_name(subject) or "Unknown"
                    # Handle date from openpyxl (returns datetime object)
                    if isinstance(date_value, datetime):
                        subject_date = date_value.strftime('%d-%m-%Y')
                    else:
                        # Fallback for other formats
                        subject_date = str(date_value)
                    subject['date'] = subject_date
                    subject['subject'] = subject_name
                    if subject_date not in dates:
                        dates.append(subject_date)
            
            # Department to timetable sheet mapping
            dept_timetable_mapping = {
                "csa": "cs", "csb": "cs",
                "ec": "ec", "ce": "ce", "ee": "ee",
                "me": "me", "mea": "me", "meb": "me",
                "mr": "mr",
                "mca": "mca"
            }

            # Update subjects for fourth year students using actual sheet names
            fourth_year_sheets = stucollections.distinct("sheet_name", {"Year": "FourthYear"})
            for dept_sheet in fourth_year_sheets:
                timetable_sheet = dept_timetable_mapping.get(dept_sheet, dept_sheet)
                if timetable_sheet in timetable4:
                    result = stucollections.update_many(
                        {"sheet_name": dept_sheet, "Year": "FourthYear",
                            "_id": {"$in": fourth_year_student_ids}},
                        {"$set": {"subject": timetable4[timetable_sheet]}}
                    )
                    print(f"Updated {result.modified_count} {dept_sheet} fourth year students with {timetable_sheet} subjects")

        with open('static/dates.txt', 'w') as f:
            json.dump(dates, f)
            flash('Upload successful', 'success')
        return render_template('timetableupload.html')
    else:
        flash('Upload failed', 'danger')
    return render_template('timetableupload.html')

# the timetable is fetched and displayed here
@app.route('/viewtimetable', methods=['GET'])
def view_timetable():
    # Fetch the documents from the MongoDB collection (only those with subject field)
    documents = stucollections.find(
        {'subject': {'$exists': True}}, {'sheet_name': 1, 'subject': 1, 'Year': 1})
    
    # Dictionary to store the timetable data
    timetables = {}
    for doc in documents:
        year = doc.get('Year')
        sheet_name = doc.get('sheet_name')
        subject = doc.get('subject')
        
        # Skip documents missing required fields
        if not year or not sheet_name or not subject:
            continue

        if year not in timetables:
            timetables[year] = {}

        if sheet_name not in timetables[year]:
            timetables[year][sheet_name] = []
        # Append the subject to the corresponding year and sheet_name in the timetable dictionary
        timetables[year][sheet_name].append(subject)
        
    # Convert the timetable dictionary to JSON format
    timetables = jsonify(timetables)
    return timetables


# unlike the /displaydata which displays the uploaded data
# this route fetches the uploaded data from the mongodb
@app.route('/viewdata', methods=['GET'])
def view_data():
    # Fetch the documents from the MongoDB collection
    documents = stucollections.find(
        {}, {'name': 1, 'rollnum': 1, 'sheet_name': 1, 'Year': 1})
    
    # List to store the retrieved data
    data = []
    
    for doc in documents:
        # Extract the relevant fields from each document, skip if missing required fields
        name = doc.get('name')
        rollnum = doc.get('rollnum')
        sheet_name = doc.get('sheet_name')
        year = doc.get('Year')
        
        # Only add documents that have all required fields
        if name and rollnum and sheet_name and year:
            data.append({
                'name': name,
                'rollnum': rollnum,
                'sheet_name': sheet_name,
                'Year': year
            })
        
    # Convert the data list to JSON format
    data = jsonify(data)
    return data


# here we are assigning the classname and seat num for each class
@app.route('/details', methods=['POST'])
def details():
    if request.method == 'POST':
        # Get the list of selected items from the form
        items = request.form.getlist('item[]')

        # List to store the details of selected classes
        class_data = []

        for item in items:
            if item in CLASSROOM_CATALOG:
                room_payload = dict(CLASSROOM_CATALOG[item])
                configured_capacity = request.form.get(f'capacity::{item}', room_payload.get('capacity', 60))
                room_payload['capacity'] = normalize_capacity(configured_capacity, default_capacity=60)
                room_payload['rows'] = room_payload['capacity'] // 2
                class_data.append(room_payload)

        if not class_data:
            flash('Select at least one classroom.', 'error')
            return redirect(url_for('classchoose'))
        # Write the class_data list to 'static/stuarrange.txt' file as JSON (compact format)
        with open('static/stuarrange.txt', 'w') as f:
            json.dump(class_data, f)

        global filled
        filled = False
        return render_template('classdetails.html', class_data=class_data)


# here the seating is done
# only two students can sit one bench but with different subjects as exam
# -issue-:this issue may arise when there is limited class and students with same subject maybe seated nearby
# using the skeleton file stuarrange.txt the students are seated into the classroom
# the timetable/date is noted . stuarrange.txt files which is the seating arrangement is generated for each day in the timetable

@app.route('/seating', methods=['GET'])
def seating():
    global filled
    
    # Check if 'stuarrange.txt' file doesn't exist, flash an error message and redirect to 'admin' route
    if not os.path.exists('static/stuarrange.txt'):
        flash('Choose Class', 'error')
        return redirect(url_for('admin'))
    
    if filled:
        with open('static/stuarrange.txt', 'r') as stufiles:
            stulist = json.load(stufiles)  # Load the JSON data from the file
        flash('Already generated', 'error')
        return redirect(url_for('admin'))
    
    else:
        # Check if students have subjects assigned (timetable uploaded)
        student_with_subject = stucollections.find_one({"subject": {"$exists": True, "$ne": None}})
        if not student_with_subject:
            flash('Please upload timetable first before generating seating!', 'error')
            return redirect(url_for('admin'))

        # Data integrity check before seating generation.
        integrity_summary = run_data_integrity_check(remove_duplicates=True)
        duplicate_rows_detected = integrity_summary.get("duplicate_rows_detected", 0)
        if duplicate_rows_detected > 0:
            flash(
                f"Data integrity warning: removed {duplicate_rows_detected} duplicate student records by roll number before seating.",
                'warning',
            )

        print("\n=== Pre-Seating Integrity Report ===")
        print("Department | Expected | Actual | Duplicates Removed")
        for row in integrity_summary.get("report", []):
            print(
                f"{row['department']} | {row['expected']} | {row['actual']} | {row['duplicates_removed']}"
            )
        print("===================================")

        with open('static/dates.txt', 'r') as datefiles:
            dates = json.load(datefiles)
        with open('static/stuarrange.txt', 'r') as stufiles:
            selected_classrooms = json.load(stufiles)

        if not selected_classrooms:
            flash('Choose at least one classroom before generating seating.', 'error')
            return redirect(url_for('classchoose'))

        stucollections.update_many({}, {"$unset": {"seatnum": ""}})

        global seating_data, teacher_assignments_data, timetable_pdf_rows
        seating_data = {}
        teacher_assignments_data = {}
        timetable_pdf_rows = []
        total_unseated = 0
        total_conflicts = 0
        total_validation_issues = 0
        teacher_warnings_total = 0
        auto_added_rooms_total = 0
        capacity_deficit_total = 0

        teachers = list(
            teachercollections.find(
                {},
                {"_id": 0, "teacher_id": 1, "name": 1, "department": 1, "availability": 1, "max_assignments_per_day": 1},
            )
        )
        global_teacher_load = {}

        for date in dates:
            students_for_date = list(
                stucollections.find(
                    {"subject": {"$elemMatch": {"date": date}}},
                    {"rollnum": 1, "sheet_name": 1, "Year": 1, "subject": 1},
                ).sort([
                    ("roll_batch", 1),
                    ("roll_dept", 1),
                    ("roll_serial", 1),
                    ("rollnum", 1),
                ])
            )
            candidates = build_candidates_for_date(students_for_date, date)

            effective_classrooms, auto_added_rooms, capacity_deficit = expand_classrooms_for_capacity(
                selected_classrooms,
                len(candidates),
            )
            auto_added_rooms_total += len(auto_added_rooms)
            capacity_deficit_total += capacity_deficit

            layouts, seat_updates, stats = allocate_classrooms(candidates, effective_classrooms)
            validation_issues = validate_layout(layouts)

            teacher_assignments, teacher_warnings, global_teacher_load = assign_teachers_for_date(
                date,
                layouts,
                teachers,
                global_teacher_load=global_teacher_load,
            )
            teacher_assignments_data[date] = teacher_assignments
            teacher_warnings_total += len(teacher_warnings)

            for layout in layouts:
                room_name = layout.get("class_name")
                layout["teacher"] = teacher_assignments.get(
                    room_name,
                    {
                        "teacher_id": "UNASSIGNED",
                        "name": "Unassigned",
                        "department": "N/A",
                    },
                )

            timetable_pdf_rows.extend(_classroom_summary_rows(date, layouts))

            if seat_updates:
                bulk_operations = []
                for rollnum, seats in seat_updates.items():
                    seats_with_date = []
                    for seat in seats:
                        seatinfo = dict(seat)
                        seatinfo["date"] = date
                        seats_with_date.append(seatinfo)
                    bulk_operations.append(
                        UpdateOne(
                            {"rollnum": rollnum},
                            {"$addToSet": {"seatnum": {"$each": seats_with_date}}},
                        )
                    )
                if bulk_operations:
                    stucollections.bulk_write(bulk_operations, ordered=False)

            seating_data[date] = layouts
            total_unseated += stats.unseated_students
            total_conflicts += stats.conflict_attempts_without_fit
            total_validation_issues += len(validation_issues)

            print(
                f"[{date}] total={stats.total_students}, seated={stats.seated_students}, "
                f"unseated={stats.unseated_students}, two_per_bench={stats.benches_with_two_students}, "
                f"single_per_bench={stats.benches_with_single_student}, validation_issues={len(validation_issues)}, "
                f"auto_added_rooms={len(auto_added_rooms)}, capacity_deficit={capacity_deficit}"
            )

        filled = True

        total_capacity = sum(int(room.get('capacity', CLASSROOM_CAPACITY) or CLASSROOM_CAPACITY) for room in selected_classrooms)
        if total_unseated > 0:
            flash(
                f'Generated with warnings: {total_unseated} students unseated across dates. '
                f'Configured capacity per date is {total_capacity}.',
                'danger',
            )
        elif capacity_deficit_total > 0:
            flash(
                f'Generated with warnings: classroom deficit detected ({capacity_deficit_total} extra room slots still needed).',
                'danger',
            )
        elif total_validation_issues > 0:
            flash(
                f'Generated with warnings: {total_validation_issues} bench pairing validation issues detected.',
                'warning',
            )
        elif total_conflicts > 0 or teacher_warnings_total > 0:
            flash(
                f'Generated with warnings: half-filled benches={total_conflicts}, teacher assignment warnings={teacher_warnings_total}.',
                'warning',
            )
        elif auto_added_rooms_total > 0:
            flash(
                f'Generated successfully with automatic classroom expansion ({auto_added_rooms_total} room additions across dates).',
                'success',
            )
        else:
            flash('Generated', 'success')

        if seating_data:
            first_date = dates[0] if dates else None
            class_list = seating_data.get(first_date, []) if first_date else []
            return render_template('viewseating.html', dates=dates, date_exams={}, initial_data=class_list)
        return render_template("adminhome.html")


@app.route('/viewseating', methods=['GET'])
def viewseating():
    global filled
    if not filled:
        flash('Firstly generate seating', 'error')
        return render_template("adminhome.html")
    
    # Load dates
    with open('static/dates.txt', 'r') as file:
        dates = json.load(file)
    
    # Get exam subjects for each date
    date_exams = {}
    for date in dates:
        # Query students who have exams on this date
        students_with_exams = stucollections.find(
            {"subject": {"$elemMatch": {"date": date}}},
            {"subject": 1}
        )
        
        # Collect unique subjects for this date
        subjects_set = set()
        for student in students_with_exams:
            if student.get('subject'):
                for subj in student['subject']:
                    if subj.get('date') == date:
                        subjects_set.add(subj.get('subject', 'Unknown'))
        
        date_exams[date] = list(subjects_set)
    
    return render_template('viewseating.html', dates=dates, date_exams=date_exams)
# Render the 'viewseating.html' template, passing the content of 'dates.txt' as the 'dates' variable
# Markup is used to mark the content as safe to render HTML tags, assuming the content contains HTML


@app.route('/export/timetable.pdf', methods=['GET'])
def export_timetable_pdf():
    global timetable_pdf_rows
    if not timetable_pdf_rows:
        flash('Generate seating first to export timetable PDF.', 'error')
        return redirect(url_for('admin'))

    try:
        pdf_bytes = build_timetable_pdf(timetable_pdf_rows)
    except RuntimeError as exc:
        flash(str(exc), 'error')
        return redirect(url_for('admin'))

    return Response(
        pdf_bytes,
        mimetype='application/pdf',
        headers={
            'Content-Disposition': 'attachment; filename=exam_timetable.pdf'
        },
    )


@app.route('/roster', methods=['GET'])
def view_roster():
    rows = _build_roster_rows()
    if not rows:
        flash('Generate seating first to view invigilator roster.', 'error')
        return redirect(url_for('admin'))
    return render_template('roster.html', rows=rows)


@app.route('/roster/export.csv', methods=['GET'])
def export_roster_csv():
    rows = _build_roster_rows()
    if not rows:
        flash('Generate seating first to export invigilator roster.', 'error')
        return redirect(url_for('admin'))

    csv_bytes = to_csv_bytes(rows)
    return Response(
        csv_bytes,
        mimetype='text/csv',
        headers={
            'Content-Disposition': 'attachment; filename=invigilator_roster.csv'
        },
    )


@app.route('/viewseating/<path:name>', methods=['GET'])
def viewseating1(name):
    global filled, seating_data, teacher_assignments_data
    if filled:
        # First try to use in-memory seating data from recent generation
        if name in seating_data:
            class_list = seating_data[name]
            return json.dumps(class_list)
        
        # Fallback to database if data not in memory
        def normalize_dept(dept):
            dept_mapping = {
                "EE": "MEE",
                "EC": "ECE",
                "CE": "CIV",
                "ME": "MEC",
                "RB": "RBE",
                "AD": "ADE",
                "MR": "MRS",
            }
            return dept_mapping.get(dept, dept)

        def extract_dept(rollnum):
            if not rollnum or len(rollnum) < 4:
                return ""
            dept = ""
            for char in rollnum[2:]:
                if char.isalpha():
                    dept += char
                else:
                    break
            return normalize_dept(dept) if dept else ""

        class_map = {}
        students_with_seats = stucollections.find(
            {"seatnum": {"$elemMatch": {"date": name}}},
            {"rollnum": 1, "seatnum": 1}
        )

        for student in students_with_seats:
            rollnum = student.get("rollnum")
            dept = extract_dept(rollnum)
            for seatinfo in student.get("seatnum", []):
                if seatinfo.get("date") != name:
                    continue
                classroom = seatinfo.get("classroom")
                seatnum = str(seatinfo.get("seatnum", "")).strip().upper()
                subject = seatinfo.get("subject", "Unknown")
                if not classroom or not seatnum:
                    continue

                if '-' not in seatnum or not seatnum.startswith('B'):
                    continue
                bench_token, side_token = seatnum.split('-', 1)
                try:
                    bench_no = int(bench_token[1:])
                except ValueError:
                    continue
                if bench_no < 1 or bench_no > BENCHES_PER_CLASSROOM:
                    continue
                if side_token not in ("L", "R"):
                    continue

                if classroom not in class_map:
                    teacher_payload = teacher_assignments_data.get(name, {}).get(
                        classroom,
                        {
                            "teacher_id": "UNASSIGNED",
                            "name": "Unassigned",
                            "department": "N/A",
                        },
                    )
                    class_map[classroom] = {
                        "class_name": classroom,
                        "teacher": teacher_payload,
                        "bench_count": BENCHES_PER_CLASSROOM,
                        "capacity": CLASSROOM_CAPACITY,
                        "benches": [
                            {"bench": i, "left": None, "right": None}
                            for i in range(1, BENCHES_PER_CLASSROOM + 1)
                        ]
                    }

                seat_payload = {
                    "rollnum": rollnum,
                    "dept": dept,
                    "exam": subject
                }
                bench = class_map[classroom]["benches"][bench_no - 1]
                if side_token == "L":
                    bench["left"] = seat_payload
                else:
                    bench["right"] = seat_payload

        class_list = sorted(class_map.values(), key=lambda c: c["class_name"])
        return json.dumps(class_list)
    else:
        flash('Firstly generate seating', 'error')
        return render_template("adminhome.html")
    


# Resetting everything out
@app.route('/reset', methods=['GET'])
def reset():
    return render_template('reset.html')


@app.route('/reset/collections', methods=['GET'])
def reset_collections():
    global filled
    filled = False
    stucollections.drop()  # Drop the 'student' collection
    message = "Student data has been deleted."
    return render_template('reset.html', message=message)


@app.route('/reset/users', methods=['GET'])
def reset_users():
    usercollections.drop()  # Drop the 'users' collection
    message = "Users has been deleted."
    return render_template('reset.html', message=message)


@app.route('/reset/static', methods=['GET'])
def reset_static():
    folder_path = 'static'
    files = os.listdir(folder_path)  # Get a list of all files in the folder
    for file in files:
        if file.startswith("stuarrange"):
            # Get the full path of the file
            file_path = os.path.join(folder_path, file)
            os.remove(file_path)  # Remove the file from the folder
    message = "Static files have been reset."
    global filled
    filled = False
    return render_template('reset.html', message=message)


@app.route('/reset/uploads', methods=['GET'])
def reset_uploads():
    folder_path = 'uploads'
    try:
        files = os.listdir(folder_path)
        for file in files:
            file_path = os.path.join(folder_path, file)
            try:
                # Try to remove the file
                os.remove(file_path)
            except PermissionError:
                # If file is locked, wait and retry
                time.sleep(0.1)
                try:
                    os.remove(file_path)
                except Exception as e:
                    flash(f'Could not delete {file}: {str(e)}', 'warning')
                    continue
        message = "Uploads have been reset."
        return render_template('reset.html', message=message)
    except Exception as e:
        flash(f'Error during reset: {str(e)}', 'error')
        return render_template('reset.html', message=f'Error: {str(e)}')


@app.route('/reset/dates', methods=['GET'])
def reset_dates():
    folder_path = 'static'
    file_path = os.path.join(folder_path, 'dates.txt')
    with open(file_path, 'w') as file:
        file.write('[]')
    message = "Dates have been reset."
    return render_template('reset.html', message=message)

# main function
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')


