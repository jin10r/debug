from aiogram import Router
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.filters import Command
from core.settings import settings
import logging
from typing import Optional

logger = logging.getLogger(__name__)

basic_router_aiogram = Router()

def create_webapp_button() -> Optional[InlineKeyboardMarkup]:
    """Создает кнопку веб-приложения если настроен URL"""
    if not settings.bot.webapp_url:
        logger.warning("Webapp URL not configured")
        return None
        
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="🌐 Открыть приложение",
                web_app=WebAppInfo(url=settings.bot.webapp_url))
        ]]
    )

@basic_router_aiogram.message(Command("start"))
async def handle_start(message: Message):
    """Обработчик команды /start"""
    user = message.from_user
    logger.info(f"Start command from user {user.id}, username={user.username}, first_name={user.first_name}")

    try:
        # Regular user greeting
        greeting = (
            "👋 Добро пожаловать! Мы рады что Вас заинтересовал наш сервис\n\n"
            "📝Не стоит воспринимать определение локаций за истину, ИИ может ошибаться.\n\n"
            "📍Данный сервис поддерживается силами комъюнити\n"
            "📍Бот не имеет обратной связи\n"
            "📍Входящие сообщения не обрабатываются.\n\n"
            "🙈Если параноите по поводу своей анонимности, то рекомендуем использовать это приложение с 'левого' аккаунта.\n\n"
            "Берегите себя!"
        )

        reply_markup = create_webapp_button()
        logger.info(f"Created webapp button for user {user.id}: {reply_markup is not None}")

        response = f"{greeting}\n\nИспользуйте кнопку ниже для доступа к приложению:"

        logger.info(f"Sending greeting to user {user.id}")
        await message.answer(response, reply_markup=reply_markup)
        logger.info(f"Successfully sent start message to user {user.id}")

    except Exception as e:
        logger.critical(f"Critical error in start handler: {e}", exc_info=True)
        await message.answer("⛔ Произошла внутренняя ошибка.")