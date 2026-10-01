from fastapi import FastAPI

app = FastAPI(title="Leadbox")


# Pinged by UptimeRobot every 5 minutes to keep the Render free instance awake.
# Must not touch the database: Neon's compute would never suspend and burn the free CU-hours.
@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
