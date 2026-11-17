# backend/app/core/security.py
import os
import secrets

import bcrypt
from datetime import datetime, timedelta
from jose import jwt

# 개발 환경에서는 키가 없으면 실행마다 무작위 키를 쓴다(재시작 시 기존 토큰 무효).
# 운영(FITCOACH_ENV=production)에서는 그 동작이 전원 로그아웃 장애가 되므로 기동을 막는다.
SECRET_KEY = os.getenv("FITCOACH_SECRET_KEY")
if not SECRET_KEY:
    if os.getenv("FITCOACH_ENV") == "production":
        raise RuntimeError("FITCOACH_ENV=production 에서는 FITCOACH_SECRET_KEY 환경변수가 필요합니다.")
    SECRET_KEY = secrets.token_hex(32)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 1주일


def create_access_token(data: dict):
    """로그인 증표(JWT 토큰)를 생성합니다."""
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


# passlib(2020년 이후 미관리)이 bcrypt 4+ 내부 API 변경을 못 따라가 깨지는 사례가
# 많아 bcrypt 라이브러리를 직접 호출하도록 단순화했다.
# bcrypt 알고리즘 자체는 비밀번호 길이를 72바이트로 제한하므로 그 위는 잘라낸다.

def get_password_hash(password: str) -> str:
    pw_bytes = password.encode("utf-8")[:72]
    return bcrypt.hashpw(pw_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    if not hashed_password:
        return False
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8")[:72],
            hashed_password.encode("utf-8"),
        )
    except (ValueError, TypeError):
        return False