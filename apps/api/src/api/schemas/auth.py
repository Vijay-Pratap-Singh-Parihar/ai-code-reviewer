import uuid

from db.organization import UserRole
from pydantic import BaseModel, EmailStr, Field


class SignupRequest(BaseModel):
    org_name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    password: str = Field(min_length=8, max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=255)


class UserPublic(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    email: EmailStr
    role: UserRole

    model_config = {"from_attributes": True}


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserPublic
