from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from leadbox.config import get_settings
from leadbox.db import create_engine, create_sessionmaker


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # The engine connects lazily, so startup does not wake Neon; the first query does.
    engine = create_engine(get_settings().database_url)
    app.state.sessionmaker = create_sessionmaker(engine)
    yield
    await engine.dispose()


app = FastAPI(title="Leadbox", lifespan=lifespan)


# Pinged by UptimeRobot every 5 minutes to keep the Render free instance awake.
# Must not touch the database: Neon's compute would never suspend and burn the free CU-hours.
@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
