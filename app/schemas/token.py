"""Token schemas."""

from pydantic import BaseModel, ConfigDict


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TokenRefresh(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str
