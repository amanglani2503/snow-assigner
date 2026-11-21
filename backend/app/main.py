import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.agent.dispatcher import dispatch
from app.api.routes import router
from app.snow.mock_api import router as mock_snow_router
from app.state import store
import time
from app import config
from app.snow.sync import sync_members, sync_tickets, flush_outbox

log = logging.getLogger("snow-assigner")
TICK_SECONDS = 60

async def _assign_loop():
    last_members = last_tickets = 0.0
    while True:
        t = time.monotonic()
        if config.SNOW_LIVE:
            if t - last_members > config.MEMBERS_SYNC_SECONDS or not store.members:
                last_members = t
                try:
                    await sync_members(store)
                except Exception:
                    log.exception("member sync failed")
            if t - last_tickets > config.SYNC_SECONDS:
                last_tickets = t
                try:
                    await sync_tickets(store)
                except Exception:
                    log.exception("ticket sync failed")
        try:
            await dispatch(store)
        except Exception:
            log.exception("dispatch failed")
        if config.SNOW_LIVE:
            try:
                await flush_outbox(store)
            except Exception:
                log.exception("outbox flush failed")
        await asyncio.sleep(3)

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_assign_loop())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="ServiceNow Auto-Assigner (POC)", lifespan=lifespan)

# Lets the React dev server (Vite) call the API during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)                                            # Swagger at /docs
app.include_router(router, prefix="/api", include_in_schema=False)    # what the React app calls
app.include_router(mock_snow_router, prefix="/mock-snow")

# In the container the built React app is copied to /app/static. Locally this folder
# doesn't exist and the Vite dev server (port 5173) is used instead.
STATIC_DIR = Path(os.getenv("STATIC_DIR", str(Path(__file__).resolve().parent.parent / "static")))
if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="web")