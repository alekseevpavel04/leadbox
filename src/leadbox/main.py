import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from leadbox.bot.runtime import start_bot, stop_bot
from leadbox.bot.webhook import router as telegram_router
from leadbox.config import get_settings
from leadbox.crm import install_crm
from leadbox.db import create_engine, create_sessionmaker

# uvicorn configures only its own loggers; without this the app's info lines never reach Render's log.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    # The engine connects lazily, so startup does not wake Neon; the first query does.
    engine = create_engine(settings.database_url)
    app.state.sessionmaker = create_sessionmaker(engine)
    await start_bot(app, settings)
    yield
    await stop_bot(app)
    await engine.dispose()


# No /docs, /redoc or /openapi.json: everything but login, healthz and the webhook sits behind the CRM login.
app = FastAPI(title="Leadbox", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(telegram_router)
install_crm(app, get_settings())


# Pinged by UptimeRobot every 5 minutes to keep the Render free instance awake.
# Must not touch the database: Neon's compute would never suspend and burn the free CU-hours.
# HEAD as well: UptimeRobot checks with HEAD by default.
@app.api_route("/healthz", methods=["GET", "HEAD"])
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
