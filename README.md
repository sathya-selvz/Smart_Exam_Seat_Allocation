# Exam Seat Arrangement

## Overview

The application helps in arranging seats for students taking exams, ensuring that no two students with the same subject are seated nearby removing malpractice, and optimally arranging the seats for the students

You can access the live demo of the application [here](https://exam-seat-arrangement.onrender.com/).

## Repository Contents

These are the files and folders that should be committed to GitHub:

- Application code: `app.py`, `classroom_allocator.py`, `data_ingestion.py`, `invigilator_roster.py`, `pdf_export.py`, `seating_engine.py`, `teacher_assignment.py`
- UI and templates: `templates/`, `static/`, `Screenshots/`
- Deployment and dependency files: `Procfile`, `requirements.txt`
- Project docs: `README.md`, `PROJECT_PRESENTATION_MASTER_GUIDE.md`
- Git settings: `.gitignore`

Keep these files local only and do not upload them to GitHub:

- Environment and virtualenv files: `.env`, `.venv/`, `venv/`, `env/`
- Runtime uploads: `uploads/`
- Local datasets: `Student Data/`, `Student Timetable/`, `Teacher Data/`
- Temporary editor artifacts: `tempCodeRunnerFile.py`, `__pycache__/`

## Features

- Generate seating arrangements for exams.
- Choose the corresponding classes to be seated
- Prevent students with the same subject from sitting close to each other.
- Choose the particular year or branch. Upload timetable, student details
- Reset data, including student information and seating arrangements.
- Cleaned student ingestion pipeline (dedupe by roll number + roll-order normalization).
- Teacher dataset upload and invigilator assignment (1 teacher per room per slot).
- Timetable PDF export including teacher and student counts.
- Generate Seating with automatic allocation and configurable classroom capacities.
- Invigilator duty roster view and CSV export.

## Phase 2 Architecture

- Data ingestion module: `data_ingestion.py`
- Seating allocation module: `seating_engine.py`
- Teacher assignment module: `teacher_assignment.py`
- PDF export module: `pdf_export.py`
- Classroom allocation module: `classroom_allocator.py`
- Invigilator roster export module: `invigilator_roster.py`

Student ingestion now overwrites old student records with a cleaned primary dataset during import.

## Technologies Used
- Python
- Flask
- MongoDB
- HTML
- CSS
- Bootstrap

## Screenshots
   
### Admin Page
![Admin Page](./Screenshots/admin1.png)

### Student Details
![Student Details](./Screenshots/studetails.png)

More screenshots in screenshots folder


## Usage

To use the Exam Seat Arrangement web application, follow these steps:

1. Clone this repository to your local machine.

   ```
   git clone https://github.com/afreenpoly/exam-seat-arrangement.git
   ```

2. Install the required dependencies.
   ```
    pip install -r requirements.txt
   ```

4. Upload datasets from admin dashboard in this order:
   - Student data (`/uploaddata`) - cleans, deduplicates, stores as primary dataset
   - Timetable data (`/timetable`)
   - Teacher data (`/teachers`)

5. Generate seating (`/seating`) and view seating (`/viewseating`).

6. Export timetable PDF from admin dashboard or `/export/timetable.pdf`.

## New Routes (Generate Seating)

- `/generate-seating`: module chooser page
- `/generate-seating/automatic`: automatic allocation flow
- `/roster`: invigilator duty roster view
- `/roster/export.csv`: invigilator duty roster export
3. Change MongoDB
   To use your own mongodb Atlas, Copy the string from your Atlas Mongodb ,similar to which i have shown
   ```
   mongodb+srv://afreenpoly:<password>@studetails.ebwix9o.mongodb.net/
   ```
   Replace the username and password.
   In this system password is passed as an env variable, therefore create an .env file and set MONGO_PASSWORD as your password
   ```
   MONGO_URI = f"mongodb+srv://afreenpoly:{MONGO_PASSWORD}@studetails.ebwix9o.mongodb.net/"
   ```
3. Run the Flask application.
   ```
    python app.py
   ```
   or
   ```
    Flask run
5. Access the web application by navigating to http://localhost:5000 in your web browser.
6. Follow the on-screen instructions to generate exam seating arrangements, view existing seating plans, and perform other related tasks.

## License

This project is licensed under the MIT License.



