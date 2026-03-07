from fastapi import FastAPI, Request, HTTPException
from aiogram import Bot, Dispatcher
from aiogram.types import Update

from .config import settings
from .db import init_db
from .handlers import router
from .admin import router as admin_router

app = FastAPI()

bot = Bot(token=settings.BOT_TOKEN)
dp = Dispatcher()

dp.include_router(admin_router)
dp.include_router(router)


@app.on_event("startup")
async def on_startup():
    await init_db()
    url = settings.webhook_url
    if url.startswith("https://"):
        await bot.set_webhook(
            url=url,
            secret_token=settings.WEBHOOK_SECRET,
            drop_pending_updates=True,
        )


@app.on_event("shutdown")
async def on_shutdown():
    url = settings.webhook_url
    if url.startswith("https://"):
        await bot.delete_webhook(drop_pending_updates=True)
    await bot.session.close()


@app.post(settings.WEBHOOK_PATH)
async def telegram_webhook(req: Request):
    secret = req.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if secret != settings.WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    data = await req.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}

@app.get("/")
async def root():
    return {"ok": True, "service": "moslav-shop-bot"}

@app.get("/health")
async def health():
    return {"ok": True}

