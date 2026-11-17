import base64
import binascii
import os
import secrets

from fastapi import FastAPI, Request
from fastapi.responses import Response
from sqladmin import Admin, ModelView

from app.models.diet_log import DietLog
from app.models.exercise_log import WorkoutLog
from app.models.user import User

ADMIN_PATH = "/admin"


class UserAdmin(ModelView, model=User):
    column_list = [
        "id", "username", "gender", "height", "weight",
        "lifestyle", "goal", "created_at",
    ]
    name = "회원 관리"
    icon = "fa-solid fa-user"


class DietAdmin(ModelView, model=DietLog):
    column_list = [
        "id", "meal_type", "food_name", "calories",
        "carbs", "protein", "fat", "date",
    ]
    name = "식단 관리"
    icon = "fa-solid fa-utensils"


class WorkoutLogAdmin(ModelView, model=WorkoutLog):
    column_list = ["id", "exercise_name", "counter", "score", "created_at"]
    name = "운동 기록"
    icon = "fa-solid fa-dumbbell"


def _credentials_match(header: str | None, username: str, password: str) -> bool:
    if not header or not header.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False
    given_user, sep, given_password = decoded.partition(":")
    if not sep:
        return False
    user_ok = secrets.compare_digest(given_user.encode(), username.encode())
    password_ok = secrets.compare_digest(given_password.encode(), password.encode())
    return user_ok and password_ok


def mount_admin(app: FastAPI, engine) -> bool:
    """관리자 페이지는 FITCOACH_ADMIN_USER / FITCOACH_ADMIN_PASSWORD 가 둘 다 설정된
    경우에만 붙는다. 회원 DB 를 그대로 보여주는 화면이라 기본값은 '없음'이다."""
    username = os.getenv("FITCOACH_ADMIN_USER")
    password = os.getenv("FITCOACH_ADMIN_PASSWORD")
    if not (username and password):
        return False

    @app.middleware("http")
    async def require_admin_credentials(request: Request, call_next):
        path = request.url.path
        if path == ADMIN_PATH or path.startswith(ADMIN_PATH + "/"):
            if not _credentials_match(request.headers.get("Authorization"), username, password):
                return Response(
                    status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="FitCoach Admin"'},
                )
        return await call_next(request)

    admin = Admin(app, engine, base_url=ADMIN_PATH)
    admin.add_view(UserAdmin)
    admin.add_view(DietAdmin)
    admin.add_view(WorkoutLogAdmin)
    return True
