from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator

from app.utils.validators import validate_password


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def validate_password_field(cls, v: str) -> str:
        error = validate_password(v)
        if error:
            raise ValueError(error)
        return v


class UserResponse(BaseModel):
    id: UUID
    email: EmailStr
