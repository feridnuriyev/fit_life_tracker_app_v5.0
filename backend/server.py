from fastapi import FastAPI, APIRouter, HTTPException, Depends, Header
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
import uuid
import httpx
import jwt
import bcrypt
from pathlib import Path
from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional
from datetime import datetime, timezone, timedelta, date

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

JWT_SECRET = os.environ['JWT_SECRET']
JWT_ALGO = "HS256"
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
ANTHROPIC_MODEL = os.environ.get('ANTHROPIC_MODEL', 'claude-sonnet-4-5-20250929')
GOOGLE_CLIENT_IDS = {
    value.strip()
    for value in os.environ.get('GOOGLE_CLIENT_IDS', '').split(',')
    if value.strip()
}
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')

app = FastAPI(title="Movena API")
api_router = APIRouter(prefix="/api")


# ============ Models ============
class UserRegister(BaseModel):
    email: EmailStr
    password: str
    name: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class GoogleAuthRequest(BaseModel):
    code: str
    code_verifier: str
    redirect_uri: str
    client_id: str

class UserOut(BaseModel):
    user_id: str
    email: str
    name: str
    picture: Optional[str] = None

class AuthResponse(BaseModel):
    token: str
    user: UserOut

class ExerciseSet(BaseModel):
    reps: int
    weight: float

class Exercise(BaseModel):
    name: str
    sets: List[ExerciseSet]

class WorkoutCreate(BaseModel):
    name: str
    exercises: List[Exercise]
    duration_minutes: int = 0
    calories_burned: int = 0
    notes: Optional[str] = ""

class Workout(WorkoutCreate):
    workout_id: str
    user_id: str
    date: str
    created_at: datetime

class DailyActivity(BaseModel):
    date: str
    steps: int = 0
    calories_burned: int = 0
    active_minutes: int = 0
    water_ml: int = 0
    distance_km: float = 0.0

class ActivityUpdate(BaseModel):
    steps: Optional[int] = None
    calories_burned: Optional[int] = None
    active_minutes: Optional[int] = None
    water_ml: Optional[int] = None
    distance_km: Optional[float] = None

class MealCreate(BaseModel):
    name: str
    meal_type: str  # breakfast, lunch, dinner, snack
    calories: int
    protein: float = 0.0
    carbs: float = 0.0
    fats: float = 0.0

class Meal(MealCreate):
    meal_id: str
    user_id: str
    date: str
    created_at: datetime

class ChatMessage(BaseModel):
    message: str

class ChatResponse(BaseModel):
    reply: str


# ============ Auth Helpers ============
def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()

def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except Exception:
        return False

def create_jwt(user_id: str) -> str:
    payload = {
        "user_id": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


async def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization.split(" ", 1)[1]

    # Try JWT first
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        user_id = payload.get("user_id")
        if user_id:
            user = await db.users.find_one({"user_id": user_id}, {"_id": 0})
            if user:
                return user
    except jwt.PyJWTError:
        pass

    raise HTTPException(status_code=401, detail="Invalid or expired token")


def today_str() -> str:
    return datetime.now(timezone.utc).date().isoformat()


# ============ Auth Endpoints ============
@api_router.post("/auth/register", response_model=AuthResponse)
async def register(req: UserRegister):
    existing = await db.users.find_one({"email": req.email.lower()}, {"_id": 0})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    user_id = f"user_{uuid.uuid4().hex[:12]}"
    user_doc = {
        "user_id": user_id,
        "email": req.email.lower(),
        "name": req.name,
        "picture": None,
        "password_hash": hash_password(req.password),
        "auth_provider": "email",
        "created_at": datetime.now(timezone.utc),
    }
    await db.users.insert_one(user_doc)
    token = create_jwt(user_id)
    return AuthResponse(
        token=token,
        user=UserOut(user_id=user_id, email=req.email.lower(), name=req.name, picture=None),
    )


@api_router.post("/auth/login", response_model=AuthResponse)
async def login(req: UserLogin):
    user = await db.users.find_one({"email": req.email.lower()}, {"_id": 0})
    if not user or not user.get("password_hash"):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_jwt(user["user_id"])
    return AuthResponse(
        token=token,
        user=UserOut(user_id=user["user_id"], email=user["email"], name=user["name"], picture=user.get("picture")),
    )


@api_router.post("/auth/google", response_model=AuthResponse)
async def google_auth(req: GoogleAuthRequest):
    if not GOOGLE_CLIENT_IDS or req.client_id not in GOOGLE_CLIENT_IDS:
        raise HTTPException(status_code=503, detail="Google sign-in is not configured")

    token_request = {
        "code": req.code,
        "client_id": req.client_id,
        "code_verifier": req.code_verifier,
        "redirect_uri": req.redirect_uri,
        "grant_type": "authorization_code",
    }
    if GOOGLE_CLIENT_SECRET:
        token_request["client_secret"] = GOOGLE_CLIENT_SECRET

    async with httpx.AsyncClient(timeout=15.0) as hc:
        try:
            token_response = await hc.post(
                "https://oauth2.googleapis.com/token",
                data=token_request,
            )
            token_response.raise_for_status()
            id_token = token_response.json().get("id_token")
            if not id_token:
                raise HTTPException(status_code=401, detail="Google did not return an ID token")

            profile_response = await hc.get(
                "https://oauth2.googleapis.com/tokeninfo",
                params={"id_token": id_token},
            )
            profile_response.raise_for_status()
            data = profile_response.json()
        except httpx.HTTPStatusError:
            raise HTTPException(status_code=401, detail="Google sign-in failed")

    if data.get("aud") != req.client_id or data.get("email_verified") not in (True, "true"):
        raise HTTPException(status_code=401, detail="Google account could not be verified")

    email = data.get("email", "").lower()
    name = data.get("name", "")
    picture = data.get("picture")
    if not email:
        raise HTTPException(status_code=401, detail="Google account has no email address")

    existing = await db.users.find_one({"email": email}, {"_id": 0})
    if existing:
        user_id = existing["user_id"]
        name = name or existing.get("name", "")
        await db.users.update_one(
            {"user_id": user_id},
            {"$set": {"name": name, "picture": picture}},
        )
    else:
        user_id = f"user_{uuid.uuid4().hex[:12]}"
        await db.users.insert_one({
            "user_id": user_id,
            "email": email,
            "name": name,
            "picture": picture,
            "password_hash": None,
            "auth_provider": "google",
            "created_at": datetime.now(timezone.utc),
        })

    return AuthResponse(
        token=create_jwt(user_id),
        user=UserOut(user_id=user_id, email=email, name=name, picture=picture),
    )


@api_router.get("/auth/me", response_model=UserOut)
async def me(user: dict = Depends(get_current_user)):
    return UserOut(user_id=user["user_id"], email=user["email"], name=user["name"], picture=user.get("picture"))


@api_router.post("/auth/logout")
async def logout():
    return {"ok": True}


# ============ Workouts ============
@api_router.post("/workouts", response_model=Workout)
async def create_workout(w: WorkoutCreate, user: dict = Depends(get_current_user)):
    workout_id = f"w_{uuid.uuid4().hex[:12]}"
    doc = {
        "workout_id": workout_id,
        "user_id": user["user_id"],
        "name": w.name,
        "exercises": [e.dict() for e in w.exercises],
        "duration_minutes": w.duration_minutes,
        "calories_burned": w.calories_burned,
        "notes": w.notes or "",
        "date": today_str(),
        "created_at": datetime.now(timezone.utc),
    }
    await db.workouts.insert_one(doc.copy())

    # Auto-update daily activity
    if w.calories_burned or w.duration_minutes:
        await db.daily_activity.update_one(
            {"user_id": user["user_id"], "date": today_str()},
            {"$inc": {
                "calories_burned": w.calories_burned,
                "active_minutes": w.duration_minutes,
            }},
            upsert=True,
        )

    return Workout(**doc)


@api_router.get("/workouts", response_model=List[Workout])
async def list_workouts(user: dict = Depends(get_current_user)):
    docs = await db.workouts.find({"user_id": user["user_id"]}, {"_id": 0}).sort("created_at", -1).to_list(200)
    return [Workout(**d) for d in docs]


@api_router.delete("/workouts/{workout_id}")
async def delete_workout(workout_id: str, user: dict = Depends(get_current_user)):
    res = await db.workouts.delete_one({"workout_id": workout_id, "user_id": user["user_id"]})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Not found")
    return {"ok": True}


# ============ Activity ============
@api_router.get("/activity/today", response_model=DailyActivity)
async def get_today(user: dict = Depends(get_current_user)):
    doc = await db.daily_activity.find_one(
        {"user_id": user["user_id"], "date": today_str()}, {"_id": 0}
    )
    if not doc:
        return DailyActivity(date=today_str())
    return DailyActivity(**{k: v for k, v in doc.items() if k in DailyActivity.model_fields})


@api_router.post("/activity/today", response_model=DailyActivity)
async def update_today(upd: ActivityUpdate, user: dict = Depends(get_current_user)):
    changes = {k: v for k, v in upd.dict().items() if v is not None}
    if not changes:
        raise HTTPException(status_code=400, detail="No fields to update")
    await db.daily_activity.update_one(
        {"user_id": user["user_id"], "date": today_str()},
        {"$set": changes, "$setOnInsert": {"user_id": user["user_id"], "date": today_str()}},
        upsert=True,
    )
    doc = await db.daily_activity.find_one(
        {"user_id": user["user_id"], "date": today_str()}, {"_id": 0}
    )
    return DailyActivity(**{k: v for k, v in doc.items() if k in DailyActivity.model_fields})


@api_router.post("/activity/increment")
async def increment_activity(upd: ActivityUpdate, user: dict = Depends(get_current_user)):
    inc = {k: v for k, v in upd.dict().items() if v is not None}
    if not inc:
        raise HTTPException(status_code=400, detail="No fields to increment")
    await db.daily_activity.update_one(
        {"user_id": user["user_id"], "date": today_str()},
        {"$inc": inc, "$setOnInsert": {"user_id": user["user_id"], "date": today_str()}},
        upsert=True,
    )
    doc = await db.daily_activity.find_one(
        {"user_id": user["user_id"], "date": today_str()}, {"_id": 0}
    )
    return DailyActivity(**{k: v for k, v in doc.items() if k in DailyActivity.model_fields})


# ============ Meals / Nutrition ============
@api_router.post("/meals", response_model=Meal)
async def create_meal(m: MealCreate, user: dict = Depends(get_current_user)):
    meal_id = f"m_{uuid.uuid4().hex[:12]}"
    doc = {
        "meal_id": meal_id,
        "user_id": user["user_id"],
        "name": m.name,
        "meal_type": m.meal_type,
        "calories": m.calories,
        "protein": m.protein,
        "carbs": m.carbs,
        "fats": m.fats,
        "date": today_str(),
        "created_at": datetime.now(timezone.utc),
    }
    await db.meals.insert_one(doc.copy())
    return Meal(**doc)


@api_router.get("/meals/today", response_model=List[Meal])
async def list_meals_today(user: dict = Depends(get_current_user)):
    docs = await db.meals.find(
        {"user_id": user["user_id"], "date": today_str()}, {"_id": 0}
    ).sort("created_at", 1).to_list(100)
    return [Meal(**d) for d in docs]


@api_router.delete("/meals/{meal_id}")
async def delete_meal(meal_id: str, user: dict = Depends(get_current_user)):
    res = await db.meals.delete_one({"meal_id": meal_id, "user_id": user["user_id"]})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Not found")
    return {"ok": True}


# ============ Progress (weekly stats) ============
@api_router.get("/progress/weekly")
async def progress_weekly(user: dict = Depends(get_current_user)):
    today = datetime.now(timezone.utc).date()
    days = [(today - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]

    activity_docs = await db.daily_activity.find(
        {"user_id": user["user_id"], "date": {"$in": days}}, {"_id": 0}
    ).to_list(100)
    activity_map = {d["date"]: d for d in activity_docs}

    workout_docs = await db.workouts.find(
        {"user_id": user["user_id"], "date": {"$in": days}}, {"_id": 0}
    ).to_list(500)
    workout_count = {}
    for w in workout_docs:
        workout_count[w["date"]] = workout_count.get(w["date"], 0) + 1

    result = []
    for d in days:
        a = activity_map.get(d, {})
        result.append({
            "date": d,
            "steps": a.get("steps", 0),
            "calories_burned": a.get("calories_burned", 0),
            "active_minutes": a.get("active_minutes", 0),
            "workouts": workout_count.get(d, 0),
        })
    return {"days": result}


# ============ AI Coach ============
@api_router.post("/coach/chat", response_model=ChatResponse)
async def coach_chat(req: ChatMessage, user: dict = Depends(get_current_user)):
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=503, detail="Coach is not configured")

    # Get user context
    activity = await db.daily_activity.find_one(
        {"user_id": user["user_id"], "date": today_str()}, {"_id": 0}
    ) or {}
    workouts_today = await db.workouts.count_documents(
        {"user_id": user["user_id"], "date": today_str()}
    )

    system_msg = (
        f"You are a friendly, knowledgeable AI fitness coach for {user.get('name', 'the user')}. "
        f"Today's stats — Steps: {activity.get('steps', 0)}, "
        f"Calories burned: {activity.get('calories_burned', 0)}, "
        f"Active minutes: {activity.get('active_minutes', 0)}, "
        f"Workouts logged today: {workouts_today}. "
        "Give concise, motivating, evidence-based advice. Keep responses under 120 words. "
        "Suggest specific workouts (sets/reps) when asked. Be encouraging but realistic."
    )

    previous_messages = await db.coach_messages.find(
        {"user_id": user["user_id"]}, {"_id": 0}
    ).sort("created_at", -1).to_list(10)
    conversation = []
    for message in reversed(previous_messages):
        conversation.extend([
            {"role": "user", "content": message["user_message"]},
            {"role": "assistant", "content": message["ai_reply"]},
        ])
    conversation.append({"role": "user", "content": req.message})

    try:
        async with httpx.AsyncClient(timeout=30.0) as hc:
            response = await hc.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": ANTHROPIC_MODEL,
                    "max_tokens": 300,
                    "system": system_msg,
                    "messages": conversation,
                },
            )
            response.raise_for_status()
            content = response.json().get("content", [])
            reply = "".join(
                block.get("text", "")
                for block in content
                if block.get("type") == "text"
            ).strip()
            if not reply:
                raise ValueError("Empty response")
    except (httpx.HTTPError, ValueError):
        logger.exception("AI coach error")
        raise HTTPException(status_code=502, detail="Coach is temporarily unavailable")

    # Save chat history
    await db.coach_messages.insert_one({
        "user_id": user["user_id"],
        "user_message": req.message,
        "ai_reply": reply,
        "created_at": datetime.now(timezone.utc),
    })

    return ChatResponse(reply=reply)


@api_router.get("/coach/history")
async def coach_history(user: dict = Depends(get_current_user)):
    docs = await db.coach_messages.find(
        {"user_id": user["user_id"]}, {"_id": 0}
    ).sort("created_at", 1).to_list(100)
    return {"messages": [
        {"user_message": d["user_message"], "ai_reply": d["ai_reply"]} for d in docs
    ]}


@api_router.get("/")
async def root():
    return {"message": "Movena API", "status": "ok"}


# Register router
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=[
        origin.strip()
        for origin in os.environ.get("CORS_ORIGINS", "http://localhost:8081").split(",")
        if origin.strip()
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
