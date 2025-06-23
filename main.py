import os
import secrets
import logging

from fastapi import FastAPI, Request, Form, Depends, HTTPException, status, Response
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.routing import APIRoute
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.webhook import webhook_router
from app.api import api_router
from app.config_server import load_config, save_config, hash_value
from app.auth import verify_session

# --- Initialize App ---
app = FastAPI(strict_slashes=False)

# Templates & Static Files
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# --- Session Secret Setup ---
SECRET_FILE = os.path.join(os.getenv("CONFIG_PATH", "/config"), ".session_secret")

def get_or_create_secret_key() -> str:
    if os.path.exists(SECRET_FILE):
        return open(SECRET_FILE).read().strip()
    secret = secrets.token_hex(32)
    os.makedirs(os.path.dirname(SECRET_FILE), exist_ok=True)
    with open(SECRET_FILE, "w") as f:
        f.write(secret)
    return secret

SESSION_SECRET_KEY = get_or_create_secret_key()

# Important: middleware must be added before routes for it to wrap correctly :contentReference[oaicite:1]{index=1}
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET_KEY,
    max_age=3600,
    same_site="lax",
    https_only=False
)

# --- Unprotected Routed ---
app.include_router(webhook_router)

# --- Include Protected API Routes ---
app.include_router(api_router, dependencies=[Depends(verify_session)])

# --- Root & Page Routes ---
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    if not request.session.get("user"):
        return templates.TemplateResponse("login.html", {"request": request, "error": False})
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    config = load_config()
    success = username == config.get("username") and hash_value(password) == config.get("password")

    logger.info(f"[AUDIT] Login attempt by '{username}' - {'SUCCESS' if success else 'FAILURE'}")

    if success:
        request.session["user"] = username
        if config.get("require_password_change", True):
            return RedirectResponse("/change-password", status_code=302)
        return RedirectResponse("/", status_code=302)

    # Render login page with error flag if login failed
    return templates.TemplateResponse("login.html", {"request": request, "error": True})


@app.get("/change-password", response_class=HTMLResponse)
async def change_password_page(request: Request):
    return templates.TemplateResponse("change_password.html", {"request": request})

@app.post("/change-password")
async def change_password(request: Request, username: str = Form(...), password: str = Form(...)):
    logger.info(f"[AUDIT] Password change initiated for user '{username}'")

    config = load_config()
    config.update({
        "username": username,
        "password": hash_value(password),
        "require_password_change": False
    })
    save_config(config)
    request.session.clear()
    return RedirectResponse("/", status_code=302)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=302)

# --- Route Logging (Debug) ---
logger = logging.getLogger("availarr")
logging.basicConfig(level=logging.INFO)
for route in app.routes:
    if isinstance(route, APIRoute):
        logger.info(f"Registered route: {route.path} - Methods: {', '.join(route.methods)}")
