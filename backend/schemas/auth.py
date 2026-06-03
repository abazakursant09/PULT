import uuid
import re
from datetime import datetime
from pydantic import BaseModel, EmailStr, field_validator


class UserRegister(BaseModel):
    email:    EmailStr
    name:     str
    password: str
    ref_code: str | None = None   # optional referral code from the inviter

    @field_validator("password")
    @classmethod
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError("Пароль должен содержать минимум 8 символов")
        if not re.search(r"[A-Za-z]", v):
            raise ValueError("Пароль должен содержать хотя бы одну букву")
        if not re.search(r"[A-Z]", v):
            raise ValueError("Пароль должен содержать хотя бы одну заглавную букву")
        if not re.search(r"\d", v):
            raise ValueError("Пароль должен содержать хотя бы одну цифру")
        return v


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id:               uuid.UUID
    email:            str
    name:             str
    plan:             str
    chat_violations:  int
    chat_blocked:     bool
    is_verified:      bool
    created_at:       datetime

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type:   str = "bearer"
    user:         UserResponse


class RegisterResponse(BaseModel):
    message:            str
    verification_token: str


class ForgotPasswordResponse(BaseModel):
    message:     str
    reset_token: str | None = None
