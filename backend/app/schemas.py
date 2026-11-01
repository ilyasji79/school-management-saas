"""Request schemas shared by registration and (later) timing editing."""
import re
from datetime import date, time
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.security import validate_password_policy

INDIAN_STATES_AND_UTS = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat", "Haryana",
    "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya",
    "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura",
    "Uttar Pradesh", "Uttarakhand", "West Bengal", "Andaman and Nicobar Islands", "Chandigarh",
    "Dadra and Nagar Haveli and Daman and Diu", "Delhi", "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry",
]
BOARDS = ["CBSE", "ICSE/ISC", "State Board", "Kendriya Vidyalaya", "IB", "Cambridge (IGCSE)", "Other"]
RESERVED_CODES = {"www", "api", "app", "admin", "platform", "static", "assets", "mail", "support", "login", "register"}
CODE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,28}[a-z0-9])$")


class Interval(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    start: time
    end: time

    @model_validator(mode="after")
    def _order(self):
        if self.end <= self.start:
            raise ValueError(f"'{self.name}': end must be after start")
        return self


def _check_intervals(items: list[Interval], label: str, day_start: time, day_end: time):
    ordered = sorted(items, key=lambda i: i.start)
    for i, item in enumerate(ordered):
        if item.start < day_start or item.end > day_end:
            raise ValueError(f"{label} '{item.name}' must fall inside school hours")
        if i and item.start < ordered[i - 1].end:
            raise ValueError(f"{label} '{ordered[i - 1].name}' and '{item.name}' overlap")


class TimingIn(BaseModel):
    """One school-timing profile. Nothing here is assumed by the platform; the school supplies it."""
    name: str = Field(default="Standard timing", min_length=1, max_length=80)
    class_group: str | None = Field(default=None, max_length=60)
    effective_from: date | None = None
    effective_to: date | None = None
    working_days: list[int] = Field(min_length=1, max_length=7)          # 0 = Monday ... 6 = Sunday
    saturdays_off: list[int] = Field(default_factory=list)               # which Saturdays of the month are off
    start_time: time
    end_time: time
    session_mode: Literal["single", "two"]
    session_split_time: time | None = None
    late_grace_minutes: int = Field(ge=0, le=120)
    half_day_after: time | None = None
    breaks: list[Interval] = Field(default_factory=list, max_length=10)
    periods: list[Interval] = Field(default_factory=list, max_length=15)

    @model_validator(mode="after")
    def _validate(self):
        if len(set(self.working_days)) != len(self.working_days) or any(d < 0 or d > 6 for d in self.working_days):
            raise ValueError("working_days must be unique numbers from 0 (Monday) to 6 (Sunday)")
        if self.saturdays_off:
            if 5 not in self.working_days:
                raise ValueError("saturdays_off needs Saturday (5) in working_days")
            if len(set(self.saturdays_off)) != len(self.saturdays_off) or any(n < 1 or n > 5 for n in self.saturdays_off):
                raise ValueError("saturdays_off must be unique numbers from 1 to 5")
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        if self.session_mode == "two":
            if not self.session_split_time or not (self.start_time < self.session_split_time < self.end_time):
                raise ValueError("two sessions need a session_split_time between start and end")
        elif self.session_split_time:
            raise ValueError("session_split_time is only for two sessions")
        if self.half_day_after and not (self.start_time < self.half_day_after <= self.end_time):
            raise ValueError("half_day_after must fall inside school hours")
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("effective_to is before effective_from")
        _check_intervals(self.breaks, "break", self.start_time, self.end_time)
        _check_intervals(self.periods, "period", self.start_time, self.end_time)
        return self


class AdminIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def _pw(cls, v):
        validate_password_policy(v)
        return v


class SchoolIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    code: str
    state: str
    board: str
    timezone: str = "Asia/Kolkata"

    @field_validator("code")
    @classmethod
    def _code(cls, v):
        v = v.strip().lower()
        if not CODE_RE.match(v) or v in RESERVED_CODES:
            raise ValueError("code must be 3-30 characters: lower-case letters, digits and hyphens, and not reserved")
        return v

    @field_validator("state")
    @classmethod
    def _state(cls, v):
        if v not in INDIAN_STATES_AND_UTS:
            raise ValueError("state must be an Indian state or union territory from the list")
        return v

    @field_validator("board")
    @classmethod
    def _board(cls, v):
        if v not in BOARDS:
            raise ValueError("board must be one of the listed boards")
        return v

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v):
        try:
            ZoneInfo(v)
        except Exception:
            raise ValueError("unknown timezone")
        return v


class RegistrationIn(BaseModel):
    school: SchoolIn
    admin: AdminIn
    plan_code: str
    timing: TimingIn
