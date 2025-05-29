import os
import asyncio
import random
import logging
import nlpcloud
import pyodbc
from aiogram import types, F
from aiogram.exceptions import TelegramBadRequest
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup
)

# === Настройки ===
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

BOT_TOKEN = "8042649082:AAGLXq9BdVoqA7K9fuWxE6ZhChVA-yR34Jw"
NLP_CLOUD_API_KEY = "acb3d97c7de7455bbdaaaa1d8559b4b00a353076"

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Инициализация клиента NLP Cloud
client = nlpcloud.Client(
    "nllb-200-3-3b",
    NLP_CLOUD_API_KEY,
    gpu=False,
)

# === Клавиатура ===
main_kb = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="🔄 Перевести текст")],
    [KeyboardButton(text="❓ Викторина")],
    [KeyboardButton(text="📌 Мои закладки")],
    [KeyboardButton(text="📊 Моя статистика")]
], resize_keyboard=True)


# === Состояния бота ===
class BotStates(StatesGroup):
    translating = State()
    quiz = State()


# === Подключение к SQL Server ===
def get_sql_connection():
    try:
        conn = pyodbc.connect(
            'DRIVER={ODBC Driver 17 for SQL Server};'
            'SERVER=localhost;'
            'DATABASE=KamillaTranslateBot;'
            'UID=marco_user;'
            'PWD=Dabro158;'
            'Encrypt=no;'
        )
        logger.info("Успешное подключение к базе данных")
        return conn
    except pyodbc.Error as e:
        logger.error(f"Ошибка подключения к SQL Server: {str(e)}")
        raise


# === Получение или создание пользователя ===
def get_or_create_user(user_id: int, username: str) -> int:
    conn = None
    cursor = None
    try:
        conn = get_sql_connection()
        cursor = conn.cursor()
        telegram_id = str(user_id)
        safe_username = str(username) if username else f"user_{user_id}"
        logger.info(f"Проверка пользователя: TelegramID={telegram_id}")

        # Проверка существования пользователя
        cursor.execute("SELECT UserID FROM Users WHERE TelegramID = ?", (telegram_id,))
        result = cursor.fetchone()
        if result:
            logger.info(f"Найден существующий пользователь: {result[0]}")
            return result[0]

        # Создание нового пользователя
        cursor.execute(
            "INSERT INTO Users (TelegramID, Username) OUTPUT INSERTED.UserID VALUES (?, ?)",
            (telegram_id, safe_username)
        )
        user_id_db = cursor.fetchone()[0]
        conn.commit()
        logger.info(f"Создан новый пользователь: ID={user_id_db}")
        return user_id_db
    except pyodbc.Error as e:
        logger.error(f"Ошибка базы данных: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Общая ошибка: {str(e)}")
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# === Переводчик через NLP Cloud API ===
async def translate_text(text: str) -> str:
    try:
        logger.info(f"Перевод текста: {text}")
        response = client.translation(
            text,
            source="rus_Cyrl",
            target="eng_Latn"
        )
        translation = response.get("translation_text", "")
        logger.info(f"Успешный перевод: {translation}")
        return translation
    except Exception as e:
        logger.error(f"Ошибка перевода: {str(e)}")
        return "⚠️ Ошибка перевода"


# === Обработчики команд ===
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    try:
        user_id = message.from_user.id
        username = message.from_user.username or f"user_{user_id}"
        logger.info(f"Запуск команды /start для пользователя {user_id}")
        user_db_id = get_or_create_user(user_id, username)
        await state.update_data(user_db_id=user_db_id)
        await message.answer(
            "Добро пожаловать! Выберите действие:",
            reply_markup=main_kb
        )
        logger.info(f"Пользователь {user_db_id} успешно авторизован")
    except Exception as e:
        logger.error(f"Ошибка в /start: {str(e)}")
        await message.answer("⚠️ Произошла ошибка при инициализации")


@dp.message(lambda m: m.text == "🔄 Перевести текст")
async def start_translate(message: types.Message, state: FSMContext):
    await message.answer("✍️ Введите текст на русском для перевода:")
    await state.set_state(BotStates.translating)
    logger.info(f"Пользователь {message.from_user.id} начал перевод")


@dp.message(BotStates.translating)
async def process_translate(message: types.Message, state: FSMContext):
    try:
        user_id = message.from_user.id
        username = message.from_user.username or f"user_{user_id}"
        logger.info(f"Обработка перевода для пользователя {user_id}")

        # Получаем user_db_id напрямую из БД
        user_db_id = get_or_create_user(user_id, username)
        text = message.text.strip()
        translation = await translate_text(text)

        # Сохранение в БД
        with get_sql_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO Translations (UserID, OriginalText, TranslatedText) OUTPUT INSERTED.TranslationID VALUES (?, ?, ?)",
                (user_db_id, text, translation)
            )
            translation_id = cursor.fetchone()[0]
            conn.commit()

        # Кнопка сохранения
        save_button = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💾 Сохранить", callback_data=f"save:{translation_id}")]
        ])
        await message.answer(f"🔤 Перевод:\n{translation}", reply_markup=save_button)

        # Сброс состояния после завершения перевода
        await state.clear()

    except Exception as e:
        logger.error(f"Ошибка перевода: {str(e)}")
        await message.answer("⚠️ Произошла ошибка при переводе")


# === Кнопка сохранить в закладки ===
@dp.callback_query(lambda c: c.data.startswith("save:"))
async def save_to_bookmark(callback_query: types.CallbackQuery):
    translation_id = int(callback_query.data.split(":")[1])
    user_id = callback_query.from_user.id
    username = callback_query.from_user.username or f"user_{user_id}"

    try:
        user_db_id = get_or_create_user(user_id, username)

        with get_sql_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT OriginalText, TranslatedText FROM Translations WHERE TranslationID = ?",
                           (translation_id,))
            result = cursor.fetchone()
            if not result:
                await callback_query.answer("Перевод не найден")
                return

            word, translation = result
            cursor.execute(
                "INSERT INTO Bookmarks (UserID, Word, Translation) VALUES (?, ?, ?)",
                (user_db_id, word, translation)
            )
            conn.commit()
        await callback_query.answer("Сохранено в закладках!")
    except Exception as e:
        logger.error(f"Ошибка сохранения в закладки: {e}")
        await callback_query.answer("Ошибка сохранения")


# === Мои закладки ===
@dp.message(lambda m: m.text == "📌 Мои закладки")
async def show_bookmarks(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    username = message.from_user.username or f"user_{user_id}"

    try:
        user_db_id = get_or_create_user(user_id, username)

        with get_sql_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT Word, Translation FROM Bookmarks WHERE UserID = ?", user_db_id)
            bookmarks = cursor.fetchall()

            if not bookmarks:
                await message.answer("У вас нет закладок.")
                return

            text = "\n".join([f"{w} → {t}" for w, t in bookmarks])
            await message.answer(f"Ваши закладки:\n{text}")
    except Exception as e:
        logger.error(f"Ошибка при загрузке закладок: {e}")
        await message.answer("⚠️ Произошла ошибка при загрузке закладок")


# === Моя статистика ===
@dp.message(lambda m: m.text == "📊 Моя статистика")
async def show_stats(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    username = message.from_user.username or f"user_{user_id}"

    try:
        user_db_id = get_or_create_user(user_id, username)

        with get_sql_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM Translations WHERE UserID = ?", user_db_id)
            total_translations = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM Bookmarks WHERE UserID = ?", user_db_id)
            total_bookmarks = cursor.fetchone()[0]

            cursor.execute("""
                SELECT AVG(CAST(CorrectAnswers AS FLOAT)/TotalQuestions)*100 
                FROM QuizResults WHERE UserID = ?
            """, user_db_id)
            accuracy = cursor.fetchone()[0] or 0

        await message.answer(
            f"📈 Ваша статистика:\n"
            f"🌐 Переводов: {total_translations}\n"
            f"🔖 Закладок: {total_bookmarks}\n"
            f"🧠 Точность викторин: {accuracy:.1f}%"
        )
    except Exception as e:
        logger.error(f"Ошибка при загрузке статистики: {e}")
        await message.answer("⚠️ Произошла ошибка при загрузке статистики")


@dp.message(F.text == "❓ Викторина")
async def quiz_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    username = message.from_user.username or f"user_{user_id}"

    try:
        user_db_id = get_or_create_user(user_id, username)

        with get_sql_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT TOP 10 EnWord, RuWord, Option1, Option2 
                FROM QuizWords 
                ORDER BY NEWID()
            """)
            questions = cursor.fetchall()

            if len(questions) != 10:  # Строгая проверка на 10 вопросов
                await message.answer(f"Ошибка: получено {len(questions)} вопросов (нужно 10)")
                return

            await state.update_data(
                quiz_questions=questions.copy(),
                correct_answers=0,
                total_questions=0,  # Начальное значение счетчика
                user_db_id=user_db_id
            )
            await ask_next_question(message, state)
    except Exception as e:
        logger.error(f"Ошибка при запуске викторины: {e}")
        await message.answer("⚠️ Произошла ошибка при запуске викторины")


async def ask_next_question(message: types.Message, state: FSMContext):
    data = await state.get_data()
    questions = data["quiz_questions"]

    if not questions:
        # Финализация результатов
        result_text = (
            f"🎉 Викторина окончена!\n"
            f"Правильных ответов: {data['correct_answers']} из {data['total_questions']}"
        )
        await message.answer(result_text)

        # Асинхронное сохранение результатов
        await asyncio.to_thread(save_quiz_result, data)
        await state.clear()
        return

    en, ru, opt1, opt2 = questions.pop(0)  # Берем первый вопрос из списка
    options = [ru, opt1, opt2]
    random.shuffle(options)

    # Обновляем состояние ПЕРЕД отправкой вопроса
    await state.update_data(
        quiz_questions=questions,
        total_questions=data["total_questions"] + 1,  # Увеличиваем счетчик только здесь
        current_question=(en, ru)
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=opt, callback_data=f"quiz:{en}:{opt}")]
        for opt in options
    ])

    await message.answer(f"❓ Как переводится '{en}'?", reply_markup=keyboard)


@dp.callback_query(F.data.startswith("quiz:"))
async def handle_quiz_answer(callback: types.CallbackQuery, state: FSMContext):
    _, en, selected = callback.data.split(":", 2)
    data = await state.get_data()
    correct_answer = data["current_question"][1]
    is_correct = (selected == correct_answer)

    # Обновляем ТОЛЬКО счетчик правильных ответов
    await state.update_data(
        correct_answers=data["correct_answers"] + (1 if is_correct else 0)
    )

    # Редактируем сообщение
    try:
        await callback.message.edit_text(
            f"{'✅ Правильно!' if is_correct else '❌ Неправильно'} "
            f"Правильный ответ: {correct_answer}",
            reply_markup=None
        )
    except TelegramBadRequest as e:
        logger.warning(f"Ошибка редактирования: {e}")

    # Задержка и удаление сообщения
    await asyncio.sleep(1.5)
    try:
        await callback.message.delete()
    except TelegramBadRequest as e:
        logger.warning(f"Ошибка удаления: {e}")

    # Переход к следующему вопросу
    await ask_next_question(callback.message, state)


def save_quiz_result(data):
    """Синхронная функция для сохранения в БД"""
    conn = get_sql_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO QuizResults (UserID, CorrectAnswers, TotalQuestions) VALUES (?, ?, ?)",
            (data["user_db_id"], data["correct_answers"], data["total_questions"])
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Ошибка сохранения результатов: {e}")
    finally:
        conn.close()


# === Запуск бота ===
async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())