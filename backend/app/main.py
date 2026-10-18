from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core import db
from app.routes import jobs
from app.services.jobs.dispatcher import dispatcher


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.open_pool()
    dispatcher.start()
    yield
    dispatcher.stop()
    db.close_pool()


app = FastAPI(lifespan=lifespan)
app.include_router(jobs.router)
