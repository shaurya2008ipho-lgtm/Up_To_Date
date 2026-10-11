from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Annotated
import re
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session
from .config import TOKEN_TTL_SECONDS
from .database import Base, SessionLocal, engine, get_db
from .importer import SUBJECT_FILES, import_roster_and_attendance, normalise_name
from .models import Assignment, AssignmentSubmission, AttendanceRecord, Grade, InboxMessage, Student, Teacher
from .schemas import (FeedbackCreate, MentalHealthNote, PasswordSetup, StudentIdentity, StudentLogin,
                      TeacherIdentity, TeacherPasswordSetup, TeacherLogin, AttendanceMarkBatch,
                      GradeUpsert, AssignmentCreate, SubmissionStatusUpdate, InboxStatusUpdate)
from .security import hash_password, issue_token, verify_password, verify_token

SUBJECTS = list(SUBJECT_FILES.keys())
ROLL_RE = re.compile(r"^26(BPH|BME)(\d{3})$")


def valid_roll_number(roll_no: str) -> bool:
    match = ROLL_RE.fullmatch((roll_no or "").strip().upper())
    if not match:
        return False
    group, digits = match.groups()
    number = int(digits)
    return (group == "BPH" and 1 <= number <= 50) or (group == "BME" and 91 <= number <= 125)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        import_roster_and_attendance(db)
    yield


app = FastAPI(
    title="ACRM | Anti Class Representative Motion API",
    description="Connected Student and Teacher attendance, grading, assignment, feedback and wellbeing API for Section C.",
    version="1.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

bearer = HTTPBearer(auto_error=False)


def resolve_student(db: Session, identity: StudentIdentity) -> Student:
    roll_no = identity.roll_no.strip().upper()
    if not valid_roll_number(roll_no):
        raise HTTPException(status_code=422, detail="Invalid roll number. Use 26BPH001–26BPH050 or 26BME091–26BME125.")
    student = db.get(Student, roll_no)
    if not student:
        raise HTTPException(status_code=404, detail="This roll number is not present in the Section C roster.")
    if normalise_name(student.name) != normalise_name(identity.name):
        raise HTTPException(status_code=401, detail="The name and roll number do not match the Section C roster.")
    return student


def current_student(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    db: Session = Depends(get_db),
) -> Student:
    if not credentials:
        raise HTTPException(status_code=401, detail="Please log in to continue.")
    roll_no = verify_token(credentials.credentials)
    student = db.get(Student, roll_no)
    if not student:
        raise HTTPException(status_code=401, detail="This student account is no longer available.")
    return student


def subject_or_400(subject: str) -> str:
    if subject not in SUBJECTS:
        raise HTTPException(status_code=400, detail=f"Unknown subject. Choose one of: {', '.join(SUBJECTS)}")
    return subject


def student_payload(student: Student) -> dict:
    return {"roll_no": student.roll_no, "name": student.name, "section": "C"}


def attendance_stats(db: Session, roll_no: str, subject: str | None = None) -> dict:
    query = select(
        func.count(AttendanceRecord.id),
        func.sum(func.case((AttendanceRecord.status == "present", 1), else_=0)),
    ) if False else None
    base = select(AttendanceRecord.status).where(AttendanceRecord.roll_no == roll_no)
    if subject:
        base = base.where(AttendanceRecord.subject == subject)
    statuses = list(db.scalars(base).all())
    total = len(statuses)
    present = sum(1 for value in statuses if value == "present")
    percentage = round((present / total * 100), 1) if total else 0.0
    return {
        "classes_held": total,
        "classes_present": present,
        "classes_absent": total - present,
        "percentage": percentage,
        "shortage": percentage < 75,
    }


@app.get("/api/health")
def health():
    return {"status": "ok", "application": "ACRM", "api_version": app.version}


@app.get("/api/subjects")
def get_subjects():
    return {"subjects": SUBJECTS}


@app.post("/api/auth/student/identify")
def identify_student(identity: StudentIdentity, db: Session = Depends(get_db)):
    student = resolve_student(db, identity)
    return {
        "setup_required": student.password_hash is None,
        "student": student_payload(student),
        "message": "Create a password for your first login." if student.password_hash is None else "Enter your password to continue.",
    }


@app.post("/api/auth/student/setup-password")
def setup_password(payload: PasswordSetup, db: Session = Depends(get_db)):
    student = resolve_student(db, payload)
    if student.password_hash is not None:
        raise HTTPException(status_code=409, detail="A password has already been created for this account. Please log in.")
    student.password_hash = hash_password(payload.password)
    db.commit()
    return {
        "access_token": issue_token(student.roll_no),
        "token_type": "bearer",
        "expires_in": TOKEN_TTL_SECONDS,
        "student": student_payload(student),
    }


@app.post("/api/auth/student/login")
def login_student(payload: StudentLogin, db: Session = Depends(get_db)):
    student = resolve_student(db, payload)
    if student.password_hash is None:
        raise HTTPException(status_code=409, detail="PASSWORD_SETUP_REQUIRED: create your password first.")
    if not verify_password(payload.password, student.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect password. Please try again.")
    return {
        "access_token": issue_token(student.roll_no),
        "token_type": "bearer",
        "expires_in": TOKEN_TTL_SECONDS,
        "student": student_payload(student),
    }


@app.get("/api/student/me")
def get_me(student: Student = Depends(current_student)):
    return student_payload(student)


@app.get("/api/student/overview")
def get_overview(student: Student = Depends(current_student), db: Session = Depends(get_db)):
    subject_stats = []
    all_present = 0
    all_held = 0
    for subject in SUBJECTS:
        stats = attendance_stats(db, student.roll_no, subject)
        all_present += stats["classes_present"]
        all_held += stats["classes_held"]
        subject_stats.append({"subject": subject, **stats})
    overall = round(all_present / all_held * 100, 1) if all_held else 0.0
    return {
        "student": student_payload(student),
        "overall": {"classes_held": all_held, "classes_present": all_present, "percentage": overall, "shortage": overall < 75},
        "subjects": subject_stats,
        "attendance_window": {"from": "2026-08-20", "through": "2026-10-10"},
    }


@app.get("/api/student/attendance")
def get_attendance(
    subject: str = Query(...),
    year: int = Query(2026, ge=2020, le=2100),
    month: int = Query(10, ge=1, le=12),
    student: Student = Depends(current_student),
    db: Session = Depends(get_db),
):
    subject = subject_or_400(subject)
    from calendar import monthrange
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])
    rows = db.scalars(select(AttendanceRecord).where(
        AttendanceRecord.roll_no == student.roll_no,
        AttendanceRecord.subject == subject,
        AttendanceRecord.session_date >= start,
        AttendanceRecord.session_date <= end,
    ).order_by(AttendanceRecord.session_date)).all()
    stats = attendance_stats(db, student.roll_no, subject)
    return {
        "subject": subject,
        "year": year,
        "month": month,
        "classes_held": stats["classes_held"],
        "classes_present": stats["classes_present"],
        "classes_absent": stats["classes_absent"],
        "percentage": stats["percentage"],
        "shortage": stats["shortage"],
        "days": [{"date": row.session_date.isoformat(), "status": row.status} for row in rows],
    }


@app.get("/api/student/grades")
def get_grades(
    subject: str | None = Query(default=None),
    student: Student = Depends(current_student),
    db: Session = Depends(get_db),
):
    query = select(Grade).where(Grade.roll_no == student.roll_no)
    if subject:
        query = query.where(Grade.subject == subject_or_400(subject))
    rows = db.scalars(query.order_by(Grade.subject)).all()
    return {"grades": [{
        "id": row.id, "subject": row.subject, "assessment": row.assessment,
        "score": row.score, "max_score": row.max_score, "is_demo": row.is_demo,
    } for row in rows], "is_demo": any(row.is_demo for row in rows)}


@app.get("/api/student/assignments")
def get_assignments(
    subject: str | None = Query(default=None),
    student: Student = Depends(current_student),
    db: Session = Depends(get_db),
):
    query = select(Assignment, AssignmentSubmission.status).join(
        AssignmentSubmission, AssignmentSubmission.assignment_id == Assignment.id
    ).where(AssignmentSubmission.roll_no == student.roll_no)
    if subject:
        query = query.where(Assignment.subject == subject_or_400(subject))
    rows = db.execute(query.order_by(Assignment.due_date, Assignment.subject)).all()
    return {"assignments": [{
        "id": assignment.id,
        "subject": assignment.subject,
        "title": assignment.title,
        "description": assignment.description,
        "due_date": assignment.due_date.isoformat(),
        "status": submission_status,
        "is_demo": assignment.is_demo,
    } for assignment, submission_status in rows]}


@app.post("/api/student/feedback", status_code=201)
def submit_feedback(
    payload: FeedbackCreate,
    student: Student = Depends(current_student),
    db: Session = Depends(get_db),
):
    subject = subject_or_400(payload.subject)
    message = InboxMessage(
        roll_no=student.roll_no,
        student_name=student.name,
        subject=subject,
        category="feedback",
        message=f"Type: {payload.category.replace('_', ' ').title()}\n\n{payload.message}",
        priority="normal",
        status="new",
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return {"ok": True, "message_id": message.id, "message": "Your message has been saved to the ACRM teacher-inbox queue."}


@app.post("/api/student/mental-health/alert", status_code=201)
def create_mental_health_alert(student: Student = Depends(current_student), db: Session = Depends(get_db)):
    message = InboxMessage(
        roll_no=student.roll_no,
        student_name=student.name,
        subject=None,
        category="mental_health_alert",
        message="CHECK-IN REQUESTED — the student has asked for a wellbeing follow-up. Student note is pending.",
        priority="high",
        status="new",
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return {"ok": True, "alert_id": message.id, "message": "Your check-in alert has been recorded for the teacher inbox."}


@app.post("/api/student/mental-health/{alert_id}/note")
def add_mental_health_note(
    alert_id: int,
    payload: MentalHealthNote,
    student: Student = Depends(current_student),
    db: Session = Depends(get_db),
):
    message = db.get(InboxMessage, alert_id)
    if not message or message.roll_no != student.roll_no or message.category != "mental_health_alert":
        raise HTTPException(status_code=404, detail="The wellbeing check-in could not be found.")
    message.message += f"\n\nStudent note: {payload.message}"
    message.status = "new"
    db.commit()
    return {"ok": True, "message": "Your note has been added to the existing teacher alert."}

# ----------------------------- TEACHER PORTAL -----------------------------

TEACHER_SUBJECT_CODES = {
    "PH101": "Physics",
    "MA101": "Mathematics",
    "HS101": "Communication Skills",
    "HS103": "Ethics and Human Mindfulness",
    "CS111": "CSE",
    "BME101": "Basics of Mechanical Engineering",
}


def teacher_payload(teacher: Teacher) -> dict:
    return {
        "id": teacher.id,
        "name": teacher.name,
        "subject_code": teacher.subject_code,
        "subject": teacher.subject,
        "role": "teacher",
    }


def resolve_teacher_identity(db: Session, identity: TeacherIdentity, create: bool = False) -> Teacher:
    code = identity.subject_code.strip().upper()
    subject = TEACHER_SUBJECT_CODES.get(code)
    if not subject:
        raise HTTPException(status_code=422, detail=f"Unknown subject code. Choose one of: {', '.join(TEACHER_SUBJECT_CODES)}")
    name_key = normalise_name(identity.name)
    teacher = db.scalar(select(Teacher).where(Teacher.name_key == name_key, Teacher.subject_code == code))
    if teacher is None and create:
        teacher = Teacher(name=identity.name.strip(), name_key=name_key, subject_code=code, subject=subject)
        db.add(teacher)
        try:
            db.commit()
            db.refresh(teacher)
        except Exception:
            db.rollback()
            teacher = db.scalar(select(Teacher).where(Teacher.name_key == name_key, Teacher.subject_code == code))
            if teacher is None:
                raise HTTPException(status_code=409, detail="Could not create the teacher profile. Please retry.")
    if teacher is None:
        raise HTTPException(status_code=404, detail="No teacher account exists for this name and subject code. Start with first-time registration.")
    return teacher


def current_teacher(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    db: Session = Depends(get_db),
) -> Teacher:
    if not credentials:
        raise HTTPException(status_code=401, detail="Please log in to continue.")
    identity = verify_token(credentials.credentials)
    if not identity.startswith("TCH-"):
        raise HTTPException(status_code=403, detail="A teacher account is required for this page.")
    try:
        teacher_id = int(identity[4:])
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid teacher session.") from exc
    teacher = db.get(Teacher, teacher_id)
    if teacher is None:
        raise HTTPException(status_code=401, detail="This teacher account is no longer available.")
    return teacher


def teacher_student_or_404(db: Session, roll_no: str) -> Student:
    roll = roll_no.strip().upper()
    if not valid_roll_number(roll):
        raise HTTPException(status_code=422, detail="Invalid roll number. Use 26BPH001–26BPH050 or 26BME091–26BME125.")
    student = db.get(Student, roll)
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found in the Section C roster.")
    return student


def teacher_subject_stats(db: Session, roll_no: str, subject: str) -> dict:
    return attendance_stats(db, roll_no, subject)


@app.get("/api/teacher/subject-codes")
def teacher_subject_codes():
    return {"subject_codes": [{"code": code, "subject": subject} for code, subject in TEACHER_SUBJECT_CODES.items()]}


@app.post("/api/auth/teacher/identify")
def identify_teacher(identity: TeacherIdentity, db: Session = Depends(get_db)):
    # This project starts with first-use self-registration. Production deployments should
    # replace this with institution-approved teacher provisioning or SSO.
    teacher = resolve_teacher_identity(db, identity, create=True)
    return {
        "setup_required": teacher.password_hash is None,
        "teacher": teacher_payload(teacher),
        "message": "Create a password for your first login." if teacher.password_hash is None else "Enter your password to continue.",
    }


@app.post("/api/auth/teacher/setup-password")
def setup_teacher_password(payload: TeacherPasswordSetup, db: Session = Depends(get_db)):
    teacher = resolve_teacher_identity(db, payload, create=True)
    if teacher.password_hash is not None:
        raise HTTPException(status_code=409, detail="A password has already been created. Please sign in.")
    teacher.password_hash = hash_password(payload.password)
    db.commit()
    return {
        "access_token": issue_token(f"TCH-{teacher.id}"),
        "token_type": "bearer",
        "expires_in": TOKEN_TTL_SECONDS,
        "teacher": teacher_payload(teacher),
        "role": "teacher",
    }


@app.post("/api/auth/teacher/login")
def login_teacher(payload: TeacherLogin, db: Session = Depends(get_db)):
    teacher = resolve_teacher_identity(db, payload, create=False)
    if teacher.password_hash is None:
        raise HTTPException(status_code=409, detail="PASSWORD_SETUP_REQUIRED: create your password first.")
    if not verify_password(payload.password, teacher.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect password. Please try again.")
    return {
        "access_token": issue_token(f"TCH-{teacher.id}"),
        "token_type": "bearer",
        "expires_in": TOKEN_TTL_SECONDS,
        "teacher": teacher_payload(teacher),
        "role": "teacher",
    }


@app.get("/api/teacher/me")
def teacher_me(teacher: Teacher = Depends(current_teacher)):
    return teacher_payload(teacher)


@app.get("/api/teacher/overview")
def teacher_overview(teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    students = db.scalars(select(Student).order_by(Student.roll_no)).all()
    stats = [teacher_subject_stats(db, student.roll_no, teacher.subject) for student in students]
    alert_count = db.scalar(select(func.count(InboxMessage.id)).where(
        InboxMessage.category == "mental_health_alert", InboxMessage.status == "new"
    )) or 0
    feedback_count = db.scalar(select(func.count(InboxMessage.id)).where(
        InboxMessage.category == "feedback", InboxMessage.subject == teacher.subject, InboxMessage.status == "new"
    )) or 0
    return {
        "teacher": teacher_payload(teacher),
        "student_count": len(students),
        "attendance_shortage_count": sum(1 for row in stats if row["shortage"]),
        "simple_inbox_unread": feedback_count,
        "critical_alerts_unread": alert_count,
    }


@app.get("/api/teacher/students")
def teacher_students(teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    rows = db.scalars(select(Student).order_by(Student.roll_no)).all()
    result = []
    for student in rows:
        stats = teacher_subject_stats(db, student.roll_no, teacher.subject)
        today_record = db.scalar(select(AttendanceRecord).where(
            AttendanceRecord.roll_no == student.roll_no,
            AttendanceRecord.subject == teacher.subject,
            AttendanceRecord.session_date == date.today(),
        ))
        result.append({
            "roll_no": student.roll_no, "name": student.name,
            "classes_held": stats["classes_held"], "classes_present": stats["classes_present"],
            "percentage": stats["percentage"], "shortage": stats["shortage"],
            "today_status": today_record.status if today_record else None,
        })
    return {"subject": teacher.subject, "subject_code": teacher.subject_code, "students": result}


@app.post("/api/teacher/attendance/mark")
def mark_teacher_attendance(
    payload: AttendanceMarkBatch,
    teacher: Teacher = Depends(current_teacher),
    db: Session = Depends(get_db),
):
    try:
        session_date = date.fromisoformat(payload.session_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Use a valid date in YYYY-MM-DD format.") from exc
    if session_date > date.today():
        raise HTTPException(status_code=422, detail="Attendance cannot be marked for a future date.")
    students = {s.roll_no: s for s in db.scalars(select(Student)).all()}
    seen: set[str] = set()
    for item in payload.records:
        if item.roll_no in seen:
            raise HTTPException(status_code=422, detail=f"Duplicate roll number in attendance form: {item.roll_no}")
        seen.add(item.roll_no)
        if item.roll_no not in students:
            raise HTTPException(status_code=404, detail=f"Student {item.roll_no} is not in the roster.")
    now = datetime.now()
    for item in payload.records:
        record = db.scalar(select(AttendanceRecord).where(
            AttendanceRecord.roll_no == item.roll_no,
            AttendanceRecord.subject == teacher.subject,
            AttendanceRecord.session_date == session_date,
        ))
        if record is None:
            db.add(AttendanceRecord(roll_no=item.roll_no, subject=teacher.subject, session_date=session_date, status=item.status))
        else:
            record.status = item.status
    db.commit()
    return {"ok": True, "subject": teacher.subject, "session_date": session_date.isoformat(), "marked": len(payload.records), "message": f"Attendance saved for {len(payload.records)} students."}


@app.get("/api/teacher/attendance/stats")
def teacher_attendance_stats(
    roll_no: str = Query(..., min_length=6, max_length=9),
    teacher: Teacher = Depends(current_teacher),
    db: Session = Depends(get_db),
):
    student = teacher_student_or_404(db, roll_no)
    stats = teacher_subject_stats(db, student.roll_no, teacher.subject)
    records = db.scalars(select(AttendanceRecord).where(
        AttendanceRecord.roll_no == student.roll_no,
        AttendanceRecord.subject == teacher.subject,
    ).order_by(AttendanceRecord.session_date.desc())).all()
    return {
        "student": {"roll_no": student.roll_no, "name": student.name},
        "subject": teacher.subject,
        "stats": stats,
        "records": [{"date": item.session_date.isoformat(), "status": item.status} for item in records],
    }


@app.get("/api/teacher/students/{roll_no}/grades")
def teacher_student_grades(roll_no: str, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    student = teacher_student_or_404(db, roll_no)
    rows = db.scalars(select(Grade).where(Grade.roll_no == student.roll_no, Grade.subject == teacher.subject).order_by(Grade.assessment)).all()
    return {"student": {"roll_no": student.roll_no, "name": student.name}, "subject": teacher.subject, "grades": [
        {"id": row.id, "assessment": row.assessment, "score": row.score, "max_score": row.max_score, "is_demo": row.is_demo}
        for row in rows
    ]}


@app.post("/api/teacher/grades", status_code=201)
def upsert_teacher_grade(payload: GradeUpsert, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    student = teacher_student_or_404(db, payload.roll_no)
    if payload.score > payload.max_score:
        raise HTTPException(status_code=422, detail="Score cannot exceed the maximum score.")
    grade = db.scalar(select(Grade).where(
        Grade.roll_no == student.roll_no,
        Grade.subject == teacher.subject,
        Grade.assessment == payload.assessment,
    ))
    if grade is None:
        grade = Grade(roll_no=student.roll_no, subject=teacher.subject, assessment=payload.assessment,
                      score=payload.score, max_score=payload.max_score, is_demo=False)
        db.add(grade)
    else:
        grade.score = payload.score
        grade.max_score = payload.max_score
        grade.is_demo = False
    db.commit()
    db.refresh(grade)
    return {"ok": True, "id": grade.id, "student": {"roll_no": student.roll_no, "name": student.name},
            "subject": teacher.subject, "assessment": grade.assessment, "score": grade.score, "max_score": grade.max_score}


@app.get("/api/teacher/assignments")
def teacher_assignments(teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    assignments = db.scalars(select(Assignment).where(Assignment.subject == teacher.subject).order_by(Assignment.due_date, Assignment.id.desc())).all()
    students = db.scalars(select(Student).order_by(Student.roll_no)).all()
    payload = []
    for assignment in assignments:
        submissions = db.execute(
            select(AssignmentSubmission, Student).join(Student, Student.roll_no == AssignmentSubmission.roll_no)
            .where(AssignmentSubmission.assignment_id == assignment.id).order_by(Student.roll_no)
        ).all()
        sub_rows = [{"roll_no": s.roll_no, "name": s.name, "status": sub.status} for sub, s in submissions]
        payload.append({
            "id": assignment.id, "subject": assignment.subject, "title": assignment.title,
            "description": assignment.description, "due_date": assignment.due_date.isoformat(),
            "created_by": assignment.created_by, "is_demo": assignment.is_demo,
            "counts": {key: sum(1 for s in sub_rows if s["status"] == key) for key in ("submitted", "late", "missing")},
            "submissions": sub_rows,
        })
    return {"subject": teacher.subject, "assignments": payload, "student_count": len(students)}


@app.post("/api/teacher/assignments", status_code=201)
def create_teacher_assignment(payload: AssignmentCreate, teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db)):
    duplicate = db.scalar(select(Assignment).where(Assignment.subject == teacher.subject, Assignment.title == payload.title))
    if duplicate:
        raise HTTPException(status_code=409, detail="An assignment with this title already exists for this subject.")
    assignment = Assignment(subject=teacher.subject, title=payload.title, description=payload.description,
                            due_date=payload.due_date, created_by=teacher.name, is_demo=False)
    db.add(assignment)
    db.flush()
    students = db.scalars(select(Student)).all()
    for student in students:
        db.add(AssignmentSubmission(roll_no=student.roll_no, assignment_id=assignment.id, status="missing"))
    db.commit()
    db.refresh(assignment)
    return {"ok": True, "assignment_id": assignment.id, "message": "Assignment created and published to the student portal."}


@app.patch("/api/teacher/assignments/{assignment_id}/submissions/{roll_no}")
def update_assignment_submission(
    assignment_id: int, roll_no: str, payload: SubmissionStatusUpdate,
    teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db),
):
    assignment = db.get(Assignment, assignment_id)
    if not assignment or assignment.subject != teacher.subject:
        raise HTTPException(status_code=404, detail="Assignment not found for your subject.")
    student = teacher_student_or_404(db, roll_no)
    submission = db.scalar(select(AssignmentSubmission).where(
        AssignmentSubmission.assignment_id == assignment.id, AssignmentSubmission.roll_no == student.roll_no
    ))
    if submission is None:
        submission = AssignmentSubmission(assignment_id=assignment.id, roll_no=student.roll_no, status=payload.status)
        db.add(submission)
    else:
        submission.status = payload.status
        submission.updated_at = datetime.now()
    db.commit()
    return {"ok": True, "assignment_id": assignment.id, "roll_no": student.roll_no, "status": payload.status}


@app.get("/api/teacher/inbox")
def teacher_inbox(
    box: str = Query("simple", pattern="^(simple|critical)$"),
    teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db),
):
    query = select(InboxMessage)
    if box == "critical":
        query = query.where(InboxMessage.category == "mental_health_alert")
    else:
        query = query.where(InboxMessage.category == "feedback", InboxMessage.subject == teacher.subject)
    rows = db.scalars(query.order_by(InboxMessage.created_at.desc())).all()
    return {"box": box, "messages": [{
        "id": row.id, "roll_no": row.roll_no, "student_name": row.student_name,
        "subject": row.subject, "category": row.category, "message": row.message,
        "priority": row.priority, "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    } for row in rows]}


@app.patch("/api/teacher/inbox/{message_id}/status")
def update_inbox_status(
    message_id: int, payload: InboxStatusUpdate,
    teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db),
):
    message = db.get(InboxMessage, message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Inbox item not found.")
    if message.category == "feedback" and message.subject != teacher.subject:
        raise HTTPException(status_code=403, detail="This message belongs to another subject's inbox.")
    if message.category not in {"feedback", "mental_health_alert"}:
        raise HTTPException(status_code=400, detail="Unsupported inbox item.")
    message.status = payload.status
    db.commit()
    return {"ok": True, "id": message.id, "status": message.status}

@app.get("/api/teacher/attendance/for-date")
def teacher_attendance_for_date(
    session_date: date = Query(...),
    teacher: Teacher = Depends(current_teacher), db: Session = Depends(get_db),
):
    if session_date > date.today():
        raise HTTPException(status_code=422, detail="Attendance cannot be marked for a future date.")
    students = db.scalars(select(Student).order_by(Student.roll_no)).all()
    records = db.scalars(select(AttendanceRecord).where(
        AttendanceRecord.subject == teacher.subject,
        AttendanceRecord.session_date == session_date,
    )).all()
    status_by_roll = {row.roll_no: row.status for row in records}
    return {
        "subject": teacher.subject,
        "session_date": session_date.isoformat(),
        "students": [{"roll_no": s.roll_no, "name": s.name, "status": status_by_roll.get(s.roll_no)} for s in students],
    }
