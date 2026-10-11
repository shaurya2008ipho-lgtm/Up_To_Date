from pydantic import BaseModel, Field, field_validator
from datetime import date
from typing import Literal

class StudentIdentity(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    roll_no: str = Field(min_length=6, max_length=9)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return " ".join(value.strip().split())

    @field_validator("roll_no")
    @classmethod
    def clean_roll(cls, value: str) -> str:
        return value.strip().upper()

class PasswordSetup(StudentIdentity):
    password: str = Field(min_length=8, max_length=128)

class StudentLogin(StudentIdentity):
    password: str = Field(min_length=1, max_length=128)

class FeedbackCreate(BaseModel):
    subject: str
    category: Literal["academic", "non_academic"]
    message: str = Field(min_length=3, max_length=5000)

    @field_validator("message")
    @classmethod
    def clean_message(cls, value: str) -> str:
        return value.strip()

class MentalHealthNote(BaseModel):
    message: str = Field(min_length=1, max_length=5000)

    @field_validator("message")
    @classmethod
    def clean_message(cls, value: str) -> str:
        return value.strip()

class TeacherIdentity(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    subject_code: str = Field(min_length=3, max_length=10)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return " ".join(value.strip().split())

    @field_validator("subject_code")
    @classmethod
    def clean_code(cls, value: str) -> str:
        return value.strip().upper()

class TeacherPasswordSetup(TeacherIdentity):
    password: str = Field(min_length=8, max_length=128)

class TeacherLogin(TeacherIdentity):
    password: str = Field(min_length=1, max_length=128)

class AttendanceMark(BaseModel):
    roll_no: str = Field(min_length=6, max_length=9)
    status: Literal["present", "absent"]

    @field_validator("roll_no")
    @classmethod
    def clean_roll(cls, value: str) -> str:
        return value.strip().upper()

class AttendanceMarkBatch(BaseModel):
    session_date: str
    records: list[AttendanceMark] = Field(min_length=1, max_length=200)

class GradeUpsert(BaseModel):
    roll_no: str = Field(min_length=6, max_length=9)
    assessment: str = Field(min_length=2, max_length=120)
    score: float = Field(ge=0)
    max_score: float = Field(gt=0)

    @field_validator("roll_no")
    @classmethod
    def clean_roll(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("assessment")
    @classmethod
    def clean_assessment(cls, value: str) -> str:
        return " ".join(value.strip().split())

class AssignmentCreate(BaseModel):
    title: str = Field(min_length=3, max_length=180)
    description: str = Field(min_length=1, max_length=5000)
    due_date: date

    @field_validator("title", "description")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return value.strip()

class SubmissionStatusUpdate(BaseModel):
    status: Literal["submitted", "late", "missing"]

class InboxStatusUpdate(BaseModel):
    status: Literal["new", "reviewed", "resolved"]
