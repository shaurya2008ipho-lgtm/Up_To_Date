# ACRM — Anti Class Representative Motion

**Section C attendance, academics and wellbeing system** — a full-stack project built with React, Vite, Three.js / React Three Fiber, FastAPI, SQLAlchemy, and SQLite. The package now includes connected Student and Teacher portals.

This bundle imports the six attendance CSVs supplied with the project, validates Section C roll numbers against the roster, and generates deterministic date-level attendance entries for the period **20 August–10 October 2026** using each CSV's `Total Classes Held` and `Classes Present` counts.

## Download and run on Windows

### Recommended: one-click launcher

1. Install **Python 3.11 or newer** and **Node.js 20 or newer**.
2. Extract the ZIP to a normal folder (not directly inside the ZIP viewer).
3. Double-click `START_ACRM.bat` (it starts both the student and teacher portals through the same app).
4. On first launch the script creates a Python virtual environment, installs backend dependencies, installs frontend packages, starts both services, and opens the website at <http://127.0.0.1:5173>.
5. API documentation is at <http://127.0.0.1:8000/docs> while the backend is running.

First launch needs internet access to download Python and npm dependencies. Later launches reuse the installed dependencies.

### Manual startup

Open two terminals in the extracted project folder.

**Terminal 1 — backend**

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

**Terminal 2 — frontend**

```powershell
cd frontend
npm install
npm run dev
```

Then open <http://127.0.0.1:5173>.

## First-time student access

Use a student name exactly as represented in the roster (case is ignored) and one of these roll-number formats:

- `26BPH001` through `26BPH050`
- `26BME091` through `26BME125`

The student name and roll number are checked against the imported Section C roster. On first use, the student creates a password of at least eight characters. The backend stores a salted scrypt password hash in SQLite; it does not store the plain-text password. Later logins require that password.

To reset a local demo account, stop the backend and remove `backend/acrm.sqlite3`, then restart the application. This deletes all locally stored passwords, messages, and demo records, and the roster will be imported again on startup.

## First-time teacher access

1. On the ACRM login page, select **Teacher**.
2. Enter the teacher name and one subject code:
   - `PH101` — Physics
   - `MA101` — Mathematics
   - `HS101` — Communication Skills
   - `HS103` — Ethics and Human Mindfulness
   - `CS111` — Computer Science (shown as CSE in the student portal)
   - `BME101` — Basics of Mechanical Engineering
3. On first use for that name + subject code, set a password of at least eight characters. Later visits require the saved password.
4. The dashboard is limited to that subject for attendance, grade entry, assignment publishing, assignment submission status and simple feedback. The **Critical alerts** inbox intentionally shows wellbeing check-ins from every subject so they can receive broader faculty attention.

**Prototype security note:** teacher first-use registration is currently open to support local testing. It lets a person who enters a name and subject code create that identity. Before real institutional use, replace this with a faculty allow-list, administrator-issued invitation, or institutional single sign-on. Do not use this local self-registration flow as proof of faculty identity.

## Included features

- ACRM landing/login experience with student/teacher role selector and an animated Three.js scene.
- FastAPI backend and persistent SQLite database shared by both portals.
- Roster imported from all six supplied subject CSVs (85 students; the importer checks roster name consistency across files).
- Student and teacher first-login password setup, salted scrypt password hashes, signed expiring tokens, and subsequent password login.
- Student subject-wise attendance calendar and statistics; green present dates and red absent dates. Demo historical dates cover 20 August–10 October 2026.
- Teacher date-based attendance register for all 85 students with individual Present/Absent controls, bulk marking, and database upsert.
- Teacher attendance lookup by roll number, class counts, attendance percentage, record history and below-75% warning.
- Teacher grade entry by roll number, assessment name, score and maximum marks; saved grades appear in the connected student grade page.
- Teacher assignment publishing with due date and description. Teachers can update each student's submission state to Submitted, Late or Missing; changes appear in the student dashboard.
- Two-part teacher inbox: subject-specific student feedback and critical wellbeing alerts shared across all teachers. Messages can be marked reviewed/resolved or reopened.
- Student feedback and mental-health notes persist in SQLite and are connected to the corresponding teacher inbox APIs.
- Interactive charts and responsive layouts for laptop and smaller screens.

## Data accuracy and prototype boundaries

- **Attendance counts** come from the provided CSV files. The CSVs contain counts, not the date of each individual lecture, so the importer distributes each student's present/absent statuses deterministically across 36 weekday sessions in the requested date range. This makes the calendar repeatable, but a specific red/green date should not be treated as the official historical date record.
- **Grades, assignments, and the initial assignment submission statuses are sample/demo data**, because those were not included in the CSV bundle. Once a teacher updates a grade or submission status, that record is saved in SQLite and reflected in the student portal.
- Student feedback and wellbeing alerts are stored in this computer's SQLite database and can be reviewed through the teacher inbox. The app does not send email, push notifications or phone alerts. For this local prototype, a teacher may need to press Refresh to see records submitted by another browser session.
- The wellbeing check-in is a support-request mechanism, not an emergency service. Students in immediate danger should contact local emergency services or a trusted person nearby.
- This is a local development prototype, not a production deployment. Before hosting it for real student use, replace open teacher self-registration with faculty provisioning/SSO, add HTTPS, secure secret management, backups, privacy/retention controls, access logging, rate limiting, and an institution-approved wellbeing response workflow. Do not expose the local API or database publicly as-is.

## Project layout

```text
ACRM_FullStack/
├── START_ACRM.bat
├── START_BACKEND.bat
├── START_FRONTEND.bat
├── README.md
├── backend/
│   ├── app/                  # FastAPI routes, models, import logic and security
│   ├── data/csv/             # Six provided subject attendance CSVs
│   ├── requirements.txt
│   └── .env.example
└── frontend/
    ├── src/                  # React app, styles and Three.js scene
    ├── package.json
    └── vite.config.js
```

## Technology stack

- **Frontend:** React 18, Vite 6, Three.js via React Three Fiber, Recharts, Lucide icons.
- **Backend:** FastAPI, Uvicorn, Pydantic, SQLAlchemy.
- **Persistence:** SQLite file at `backend/acrm.sqlite3` by default.
- **Authentication:** salted scrypt password hashes and signed, expiring bearer tokens.

## Helpful endpoints

- `GET /api/health` — backend health check
- `GET /api/subjects` — canonical subject names
- `POST /api/auth/student/identify` — verify name/roll and determine if password setup is required
- `POST /api/auth/student/setup-password` — one-time password setup
- `POST /api/auth/student/login` — student login
- `GET /api/student/overview` — overall and subject attendance statistics
- `GET /api/student/attendance?subject=Mathematics&year=2026&month=10` — calendar records and statistics
- `GET /api/student/grades` — demo grades
- `GET /api/student/assignments` — demo assignments/statuses
- `POST /api/student/feedback` — queue a teacher message
- `POST /api/student/mental-health/alert` — create a high-priority wellbeing alert
- `POST /api/student/mental-health/{alert_id}/note` — attach the student's optional note
- `POST /api/auth/teacher/identify` and `POST /api/auth/teacher/setup-password` — teacher first-use setup
- `POST /api/auth/teacher/login` and `GET /api/teacher/me` — teacher authentication
- `GET /api/teacher/students` — Section C roster with subject attendance
- `GET /api/teacher/attendance/for-date` and `POST /api/teacher/attendance/mark` — date register and save
- `GET /api/teacher/attendance/stats?roll_no=26BPH001` — individual attendance statistics
- `POST /api/teacher/grades` and `GET /api/teacher/students/{roll_no}/grades` — subject gradebook
- `GET/POST /api/teacher/assignments` — assignment list and publishing
- `PATCH /api/teacher/assignments/{assignment_id}/submissions/{roll_no}` — set submission status
- `GET /api/teacher/inbox?box=simple|critical` — subject feedback or shared wellbeing alerts
- `PATCH /api/teacher/inbox/{message_id}/status` — review, resolve or reopen a message
