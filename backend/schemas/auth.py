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


class SessionResponse(BaseModel):
    # SECURITY-2B-2 — login / verify-email / MFA return ONLY the safe user profile. The session JWT is
    # delivered exclusively in the HttpOnly cookie and never appears in any JSON body.
    user: UserResponse


class RegisterResponse(BaseModel):
    # P7.1 — verification link is delivered by email, never returned in the response.
    message: str
    # Whether the verification mail was actually handed to SMTP. Registration used to return the
    # same cheerful "Проверьте почту" whether the mail was sent, merely logged, or failed outright
    # — so a seller whose mail never left sat waiting for a letter that did not exist, unable to
    # log in (verification is required) and unable to reset their way in either.
    #
    # This is a DELIVERY fact, not a token: it says nothing about the link itself. Safe to return
    # here because the caller just created this account — there is no one else's existence to leak.
    verification_email_sent: bool = True


class ForgotPasswordResponse(BaseModel):
    # P7.1 — reset link is delivered by email, never returned in the response.
    message: str
