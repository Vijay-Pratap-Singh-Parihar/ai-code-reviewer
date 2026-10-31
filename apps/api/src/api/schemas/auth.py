import uuid
from typing import Annotated

from db.organization import UserRole
from pydantic import BaseModel, EmailStr, Field, StringConstraints

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class SignupRequest(BaseModel):
    org_name: str = Field(min_length=1, max_length=255)
    first_name: Name
    last_name: Name
    email: EmailStr
    password: str = Field(min_length=8, max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=255)


class UserPublic(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    email: EmailStr
    first_name: str | None = None
    last_name: str | None = None
    role: UserRole
    is_platform_admin: bool = False

    model_config = {"from_attributes": True}


class ProfileUpdate(BaseModel):
    first_name: Name
    last_name: Name


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserPublic
