# Movena

Movena is a mobile fitness tracker for recording daily activity, workouts, meals, and weekly progress. It includes a personal coaching chat that can use the current day's activity data when answering.

## Features

- Email/password and Google sign-in
- Daily steps, calories, active minutes, water, and distance
- Workout logging with exercises, sets, duration, and calories
- Meal logging with calories and macronutrients
- Seven-day activity and workout summaries
- Context-aware fitness coaching

## Stack

- Expo SDK 54, React Native 0.81, Expo Router, and TypeScript
- FastAPI and Pydantic
- MongoDB with the asynchronous Motor driver
- JWT authentication and Google OAuth 2.0
- Anthropic Messages API for coaching responses

## Project layout

```text
backend/
  server.py
  requirements.txt
frontend/
  app/
  assets/
  src/AuthContext.tsx
  app.json
  package.json
```

## Local setup

Requirements:

- Node.js 22 or newer and pnpm 11
- Python 3.11 or newer
- MongoDB

Start the API:

```bash
cd backend
python -m venv .venv
python -m pip install -r requirements.txt
copy .env.example .env
uvicorn server:app --reload --port 8001
```

Start the app in another terminal:

```bash
cd frontend
pnpm install --frozen-lockfile
copy .env.example .env
pnpm start
```

For a physical device, set `EXPO_PUBLIC_BACKEND_URL` to an address the device can reach instead of `localhost`.

## Google sign-in

Create an OAuth client in Google Cloud and register the redirect URI printed by Expo for the app. Then:

1. Set `EXPO_PUBLIC_GOOGLE_CLIENT_ID` in `frontend/.env`.
2. Add the same client ID to `GOOGLE_CLIENT_IDS` in `backend/.env`.
3. If the OAuth client requires a secret, set `GOOGLE_CLIENT_SECRET` only in the backend environment.

The app uses the authorization-code flow with PKCE. The backend exchanges the code with Google, verifies the returned identity, and issues the same application JWT used by email/password accounts.

## API

All routes use the `/api` prefix. Protected routes expect `Authorization: Bearer <token>`.

| Method | Route | Purpose |
| --- | --- | --- |
| POST | `/auth/register` | Create an account |
| POST | `/auth/login` | Sign in with email and password |
| POST | `/auth/google` | Complete Google OAuth sign-in |
| GET | `/auth/me` | Return the current user |
| GET, POST | `/activity/today` | Read or update today's activity |
| POST | `/activity/increment` | Add to today's activity totals |
| GET, POST | `/workouts` | List or create workouts |
| DELETE | `/workouts/{id}` | Delete a workout |
| GET | `/meals/today` | List today's meals |
| POST | `/meals` | Create a meal |
| DELETE | `/meals/{id}` | Delete a meal |
| GET | `/progress/weekly` | Return the last seven days |
| POST | `/coach/chat` | Send a message to the coach |
| GET | `/coach/history` | Return coaching history |

## Configuration

Do not commit `.env` files or credentials. Copy the included examples and replace their placeholder values locally.

## Upgrading an existing installation

Keep your existing `MONGO_URL`, `DB_NAME`, and `JWT_SECRET`; renaming the app does not require moving user data. The database name in `.env.example` is for new installations only.

Google sign-in now requires your own OAuth configuration. Existing provider sessions may require signing in again. Register and verify the redirect URI for each target platform before release; native Google sign-in has not been device-tested. The app's URL scheme is now `movena`.

Coaching requires a server-side `ANTHROPIC_API_KEY`. Email/password sign-in and tracking do not require the coaching service.
