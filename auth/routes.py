"""로그인 / 토큰 발급 (OAuth2 password)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordRequestForm

from .jwt_handler import create_access_token
from .dependencies import verify_dev_password

router = APIRouter()


@router.post("/token")
async def login(form: OAuth2PasswordRequestForm = Depends()) -> dict[str, str]:
    role = verify_dev_password(form.username, form.password)
    token = create_access_token(subject=form.username, role=role)
    return {"access_token": token, "token_type": "bearer"}
