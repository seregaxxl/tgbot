"""
Telegram-бот | «Квантовый разлом» — диагностический тест
ТЕСТОВАЯ ВЕРСИЯ: PDF без оплаты + кириллица + лоадер параллельно с генерацией
Стек: aiogram 3.x, FSM, aiohttp webhook, reportlab PDF
"""

import asyncio
import logging
import os
import io
import urllib.request

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiohttp import web
from yoomoney import Client, Quickpay

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ──────────────────────────────────────────────
# КОНФИГУРАЦИЯ
# ──────────────────────────────────────────────
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "REPLACE_WITH_NEW_TOKEN")
WEBHOOK_HOST: str = os.getenv("WEBHOOK_HOST", "https://yourdomain.com")
WEBHOOK_PATH: str = "/webhook/telegram"
WEBHOOK_URL: str = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
WEB_PORT: int = int(os.getenv("PORT", 8080))

YOOMONEY_TOKEN: str  = os.getenv("YOOMONEY_TOKEN", "YOUR_YOOMONEY_TOKEN")
YOOMONEY_WALLET: str = os.getenv("YOOMONEY_WALLET", "YOUR_WALLET_NUMBER")
YOOMONEY_SECRET: str = os.getenv("YOOMONEY_SECRET", "YOUR_YOOMONEY_SECRET")
YOOMONEY_NOTIFY_PATH: str = "/webhook/yoomoney"
PAYMENT_AMOUNT: float = 1.0          # ← 1 рубль для теста, потом сменить на 390.0
PAYMENT_LABEL_PREFIX: str = "report_"

# Пути к картинкам (положите файлы в assets/ рядом с bot.py)
# assets/start.jpg       — экран приветствия
# assets/q1.jpg          — вопрос 1
# assets/q2.jpg          — вопрос 2
# assets/q3.jpg          — вопрос 3
# assets/q4.jpg          — вопрос 4
# assets/q5.jpg          — вопрос 5
# assets/final.jpg       — экран после PDF
BASE_DIR: str = os.path.dirname(os.path.abspath(__file__))
IMG_START:  str = os.path.join(BASE_DIR, "assets", "start.jpg")
IMG_FINAL:  str = os.path.join(BASE_DIR, "assets", "final.png")
IMG_QUESTIONS: list[str] = [
    os.path.join(BASE_DIR, "assets", f"q{i}.png") for i in range(1, 6)
]

# ──────────────────────────────────────────────
# ЛОГИРОВАНИЕ
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("quantum_bot")

# ──────────────────────────────────────────────
# ШРИФТЫ — регистрируем кириллицу
# ──────────────────────────────────────────────
FONT_REGULAR = "CyrRegular"
FONT_BOLD    = "CyrBold"


def _register_fonts() -> None:
    global FONT_REGULAR, FONT_BOLD  # <-- объявляем первым делом

    candidates = [
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ("/usr/share/fonts/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
        ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
        ("/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
         "/usr/share/fonts/liberation/LiberationSans-Bold.ttf"),
        ("/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
         "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf"),
    ]

    for reg, bold in candidates:
        if os.path.exists(reg) and os.path.exists(bold):
            pdfmetrics.registerFont(TTFont(FONT_REGULAR, reg))
            pdfmetrics.registerFont(TTFont(FONT_BOLD, bold))
            logger.info("Fonts loaded: %s / %s", reg, bold)
            return

    # Fallback — скачиваем DejaVu
    logger.warning("System fonts not found, downloading DejaVu as fallback...")
    tmp = "/tmp"
    reg_url  = "https://github.com/dejavu-fonts/dejavu-fonts/raw/master/ttf/DejaVuSans.ttf"
    bold_url = "https://github.com/dejavu-fonts/dejavu-fonts/raw/master/ttf/DejaVuSans-Bold.ttf"
    reg_path  = os.path.join(tmp, "DejaVuSans.ttf")
    bold_path = os.path.join(tmp, "DejaVuSans-Bold.ttf")
    try:
        urllib.request.urlretrieve(reg_url, reg_path)
        urllib.request.urlretrieve(bold_url, bold_path)
        pdfmetrics.registerFont(TTFont(FONT_REGULAR, reg_path))
        pdfmetrics.registerFont(TTFont(FONT_BOLD, bold_path))
        logger.info("DejaVu fonts downloaded and registered.")
    except Exception as exc:
        logger.error("Font download failed: %s — PDF will have broken cyrillic!", exc)
        FONT_REGULAR = "Helvetica"
        FONT_BOLD    = "Helvetica-Bold"


_register_fonts()

pending_payments: dict[str, int] = {}
user_scores: dict[int, int] = {}

# ──────────────────────────────────────────────
# ВОПРОСЫ ТЕСТА
# ──────────────────────────────────────────────
QUESTIONS: list[dict] = [
    {
        "text": (
            "▌ ВОПРОС 1 / 5\n\n"
            "Замечаете ли вы, что после перемен в жизни "
            "(новая работа, новый партнёр, новое окружение) "
            "через несколько месяцев вы снова оказываетесь "
            "в тех же эмоциональных ситуациях, что и раньше?"
        ),
        "options": [
            ("А) Да, почти всегда. Сценарии повторяются почти одинаково.", 4),
            ("Б) Часто. Обстоятельства меняются, но итог похож.", 3),
            ("В) Иногда. Бывает ощущение дежавю, но не постоянно.", 2),
            ("Г) Нет. Каждый этап жизни ощущается по-разному.", 1),
        ],
    },
    {
        "text": (
            "▌ ВОПРОС 2 / 5\n\n"
            "Бывает ли, что как только вы принимаете важное решение, "
            "возникают неожиданные обстоятельства "
            "(болезнь, срочный звонок, поломка), "
            "которые буквально сбивают вас с курса?"
        ),
        "options": [
            ("А) Почти всегда. Каждое начинание встречает препятствия.", 4),
            ("Б) Довольно часто. Двигаюсь, но через постоянные сложности.", 3),
            ("В) Иногда. Мелкие помехи бывают, но не критично.", 2),
            ("Г) Практически нет. Всё складывается в пользу решений.", 1),
        ],
    },
    {
        "text": (
            "▌ ВОПРОС 3 / 5\n\n"
            "Если оглянуться на последние 2 года — "
            "ваш выбор в жизни расширился или сузился? "
            "Вы чаще выбираете из того, что хотите, "
            "или из того, что просто осталось доступным?"
        ),
        "options": [
            ("А) Выбираю из того, что осталось. Возможности сузились.", 4),
            ("Б) Варианты есть, но не хватает энергии воспользоваться.", 3),
            ("В) Выбор есть, но примерно один и тот же. Предсказуемо.", 2),
            ("Г) Возможностей стало больше. Горизонт расширяется.", 1),
        ],
    },
    {
        "text": (
            "▌ ВОПРОС 4 / 5\n\n"
            "Иногда кажется, что мысли других людей "
            "или навязчивые идеи забирают почти всё ваше внимание "
            "и мешают действовать?"
        ),
        "options": [
            ("А) Постоянно. Чужой шум полностью блокирует мои идеи.", 4),
            ("Б) Часто. Силы уходят на внутренние споры.", 3),
            ("В) Иногда. Замечаю шум, но умею отключаться.", 2),
            ("Г) Практически никогда. Фокус не нарушается.", 1),
        ],
    },
    {
        "text": (
            "▌ ВОПРОС 5 / 5\n\n"
            "При мысли о будущем иногда ощущаете тяжесть "
            "или упадок сил, даже если логически "
            "всё кажется в порядке?"
        ),
        "options": [
            ("А) Почти всегда. Тело сжимается, тяжесть физически ощутима.", 4),
            ("Б) Часто. Нет сил думать о будущем, хочется закрыться.", 3),
            ("В) Иногда. Лёгкая тревога, но контроль сохраняется.", 2),
            ("Г) Практически никогда. Будущее приносит прилив энергии.", 1),
        ],
    },
]

# ──────────────────────────────────────────────
# FSM
# ──────────────────────────────────────────────
class TestState(StatesGroup):
    answering        = State()
    generating       = State()
    awaiting_payment = State()


# ══════════════════════════════════════════════
# ЛОАДЕР
# ══════════════════════════════════════════════
LOADER_STEPS = [
    ("▓░░░░░░░░░  10%", "Считываю паттерны..."),
    ("▓▓▓░░░░░░░  30%", "Анализирую векторы нагрузки..."),
    ("▓▓▓▓▓░░░░░  50%", "Сопоставляю профиль..."),
    ("▓▓▓▓▓▓▓░░░  70%", "Формирую протокол..."),
    ("▓▓▓▓▓▓▓▓▓░  90%", "Генерирую отчёт..."),
    ("▓▓▓▓▓▓▓▓▓▓ 100%", "Готово."),
]


async def show_loader(message: Message, verdict_text: str) -> None:
    """Анимирует прогресс-бар, редактируя сообщение."""
    for bar, status in LOADER_STEPS:
        text = (
            f"{verdict_text}\n\n"
            "──────────────────────\n"
            f"<code>{bar}</code>\n"
            f"<i>{status}</i>"
        )
        try:
            await message.edit_text(text)
        except Exception:
            pass
        await asyncio.sleep(0.9)


# ══════════════════════════════════════════════
# PDF ГЕНЕРАТОР
# ══════════════════════════════════════════════
COLOR_ACCENT = colors.HexColor("#C8A96E")
COLOR_TEXT   = colors.HexColor("#1A1A1A")
COLOR_MUTED  = colors.HexColor("#666666")
COLOR_LINE   = colors.HexColor("#C8A96E")


def _styles() -> dict:
    return {
        "title": ParagraphStyle(
            "title", fontName=FONT_BOLD, fontSize=20,
            textColor=COLOR_ACCENT, alignment=TA_CENTER,
            spaceAfter=4, leading=26,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", fontName=FONT_REGULAR, fontSize=10,
            textColor=COLOR_MUTED, alignment=TA_CENTER, spaceAfter=4,
        ),
        "section": ParagraphStyle(
            "section", fontName=FONT_BOLD, fontSize=12,
            textColor=COLOR_ACCENT, spaceAfter=3, spaceBefore=10,
        ),
        "body": ParagraphStyle(
            "body", fontName=FONT_REGULAR, fontSize=10,
            textColor=COLOR_TEXT, alignment=TA_JUSTIFY,
            spaceAfter=5, leading=15,
        ),
        "bullet": ParagraphStyle(
            "bullet", fontName=FONT_REGULAR, fontSize=10,
            textColor=COLOR_TEXT, leftIndent=10,
            spaceAfter=3, leading=14,
        ),
        "footer": ParagraphStyle(
            "footer", fontName=FONT_REGULAR, fontSize=8,
            textColor=COLOR_MUTED, alignment=TA_CENTER,
        ),
    }


def _build_pdf(title: str, score: int, type_label: str, sections: list[dict]) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=22*mm, rightMargin=22*mm,
        topMargin=22*mm, bottomMargin=22*mm,
    )
    S = _styles()
    story = []

    story.append(Paragraph("КВАНТОВЫЙ РАЗЛОМ  |  ДИАГНОСТИЧЕСКИЙ МОДУЛЬ", S["subtitle"]))
    story.append(Spacer(1, 3))
    story.append(Paragraph(title, S["title"]))
    story.append(Spacer(1, 2))
    story.append(Paragraph(f"Индекс нагрузки: {score} / 20   |   {type_label}", S["subtitle"]))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=1, color=COLOR_LINE))
    story.append(Spacer(1, 10))

    for sec in sections:
        story.append(Paragraph(sec["heading"], S["section"]))
        story.append(HRFlowable(width="35%", thickness=0.5, color=COLOR_MUTED))
        story.append(Spacer(1, 4))
        for p in sec.get("paragraphs", []):
            story.append(Paragraph(p, S["body"]))
        for b in sec.get("bullets", []):
            story.append(Paragraph(f"  >  {b}", S["bullet"]))
        story.append(Spacer(1, 6))

    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", thickness=0.5, color=COLOR_MUTED))
    story.append(Spacer(1, 4))
    story.append(Paragraph("Квантовый Разлом  ·  @QuanTum_Break_bot", S["footer"]))

    doc.build(story)
    return buf.getvalue()


def generate_report(score: int) -> bytes:
    if score <= 8:
        return _build_pdf("ПРОФИЛЬ: УСТОЙЧИВОСТЬ", score, "Тип I — Интегрированный", [
            {"heading": "01 / ОБЩАЯ КАРТИНА", "paragraphs": [
                "Ваши показатели указывают на высокую степень психоэнергетической интеграции. "
                "Внутренние паттерны не конфликтуют с внешней реальностью. "
                "Система функционирует в режиме расширения, а не компенсации.",
                "Это редкий результат — не повод для самодовольства, "
                "но твёрдое основание для следующего уровня.",
            ]},
            {"heading": "02 / СИЛЬНЫЕ СТОРОНЫ", "bullets": [
                "Эмоциональная саморегуляция без подавления",
                "Устойчивость к внешним манипуляциям",
                "Способность удерживать долгосрочный фокус",
                "Доступ к внутреннему ресурсу в кризисных точках",
                "Низкий уровень когнитивного шума",
            ]},
            {"heading": "03 / ЗОНЫ РОСТА", "paragraphs": [
                "Устойчивость без вызова — это плато. "
                "Ваш профиль требует не исправления, а масштабирования.",
            ], "bullets": [
                "Проверьте: не превратилась ли стабильность в избегание риска",
                "Расширяйте порог неопределённости намеренно",
                "Введите практику ревизии ценностей раз в квартал",
            ]},
            {"heading": "04 / ПРОТОКОЛ НА 30 ДНЕЙ", "bullets": [
                "Нед. 1–2: Аудит энергетических утечек — запишите всё, что забирает силы без отдачи",
                "Нед. 3–4: Один намеренный дискомфорт в неделю (новая среда, новый навык)",
                "Ежедневно: 10 минут без задач — просто наблюдение за состоянием",
            ]},
            {"heading": "05 / КЛЮЧЕВОЙ ПРИНЦИП", "paragraphs": [
                "Интеграция — не финальная точка. Это платформа для следующего разрыва.",
            ]},
        ])

    elif score <= 12:
        return _build_pdf("ПРОФИЛЬ: ФОНОВОЕ НАПРЯЖЕНИЕ", score, "Тип II — Адаптирующийся", [
            {"heading": "01 / ОБЩАЯ КАРТИНА", "paragraphs": [
                "Система адаптирована — но адаптация стоит ресурса. "
                "Вы функционируете стабильно, однако часть энергии уходит "
                "на поддержание этой стабильности, а не на движение.",
                "Фоновое напряжение незаметно, пока не исчезает. "
                "Этот отчёт — карта его источников.",
            ]},
            {"heading": "02 / ВЫЯВЛЕННЫЕ ПАТТЕРНЫ", "bullets": [
                "Периодические циклы «старт — торможение — перезапуск»",
                "Чувствительность к внешним оценкам выше, чем кажется",
                "Решения принимаются, но реализация требует непропорциональных усилий",
                "Мысли о будущем нейтральны, но не вдохновляют",
            ]},
            {"heading": "03 / КОРНЕВЫЕ МЕХАНИЗМЫ", "paragraphs": [
                "Адаптационная усталость формируется не от событий, а от постоянной "
                "необходимости корректировать курс. Когда среда требует больше гибкости, "
                "чем вы восполняете — система начинает экономить на качестве присутствия.",
            ]},
            {"heading": "04 / ПРОТОКОЛ СНИЖЕНИЯ НАГРУЗКИ", "bullets": [
                "Определите три задачи, которые вы делаете из «надо», а не из выбора",
                "Введите один полный день без цифровых устройств раз в две недели",
                "Практика перед сном: три вещи, которые прошли сегодня без усилий",
                "Аудит обязательств: что можно делегировать или остановить прямо сейчас",
                "Физическая нагрузка средней интенсивности 3 раза в неделю — обязательно",
            ]},
            {"heading": "05 / ТОЧКА ПЕРЕЛОМА", "paragraphs": [
                "Следующие 60–90 дней определят, двинется ли профиль в сторону "
                "истощения или интеграции. Выбор делается не одним решением — "
                "серией небольших, но последовательных действий.",
            ]},
        ])

    elif score <= 15:
        return _build_pdf("ПРОФИЛЬ: СИСТЕМНАЯ ПЕРЕГРУЗКА", score, "Тип III — Компенсирующий", [
            {"heading": "01 / ОБЩАЯ КАРТИНА", "paragraphs": [
                "Вы работаете в режиме хронической компенсации. "
                "Внешне — стабильны. Внутри — система держится на волевом усилии, "
                "а не на ресурсе. Разрыв между тем, как вы выглядите, "
                "и тем, как вы себя чувствуете — значительный.",
            ]},
            {"heading": "02 / КРИТИЧЕСКИЕ ИНДИКАТОРЫ", "bullets": [
                "Повторяющиеся сценарии несмотря на смену обстоятельств",
                "Ощущение, что будущее — угроза, а не возможность",
                "Энергия есть, но она уходит на контроль, а не на действие",
                "Решения принимаются под давлением внешних факторов",
                "Внутренний диалог занимает больше места, чем реальность",
            ]},
            {"heading": "03 / МЕХАНИКА КОМПЕНСАЦИИ", "paragraphs": [
                "Компенсация — это когда система берёт ресурс из «завтра», "
                "чтобы функционировать сегодня. Этот долг накапливается незаметно "
                "и проявляется резко: срывом, болезнью, потерей мотивации.",
                "Ваш приоритет сейчас — не рост. Восстановление базового ресурса.",
            ]},
            {"heading": "04 / ЭКСТРЕННЫЙ ПРОТОКОЛ", "bullets": [
                "СТОП-анализ: составьте список всего, что делаете «потому что так надо»",
                "Минимум одно удовольствие в день без оправданий и пользы",
                "Сон: жёсткий режим засыпания / пробуждения — 21 день без исключений",
                "Ограничьте входящий поток информации на 50%",
                "Одна доверенная беседа в неделю — с кем-то, кто слушает без советов",
                "Телесные практики: дыхание, движение — не как спорт, а как контакт с собой",
            ]},
            {"heading": "05 / ВРЕМЕННАЯ РАМКА", "paragraphs": [
                "Первые результаты при соблюдении протокола — через 3–4 недели. "
                "Не форсируйте трансформацию. Сейчас задача — остановить утечку, "
                "а не немедленно перестроить систему.",
            ]},
        ])

    elif score <= 18:
        return _build_pdf("ПРОФИЛЬ: КРИТИЧЕСКОЕ ИСТОЩЕНИЕ", score, "Тип IV — Декомпенсирующий", [
            {"heading": "01 / ОБЩАЯ КАРТИНА", "paragraphs": [
                "Механизмы адаптации исчерпаны. Система держится на инерции "
                "и волевом контроле. Это не слабость — это результат длительной "
                "работы без достаточного восстановления.",
                "Продолжение в текущем режиме без вмешательства "
                "приведёт к системному срыву: физическому, эмоциональному или социальному.",
            ]},
            {"heading": "02 / СИГНАЛЫ СИСТЕМЫ", "bullets": [
                "Циклы повторяются — выход из них требует всё больше сил",
                "Будущее воспринимается как угроза или пустота",
                "Возможности сужены или кажутся недоступными",
                "Чужие голоса и паттерны занимают внутреннее пространство",
                "Физическое тело сигнализирует о перегрузке постоянно",
            ]},
            {"heading": "03 / ЧТО ПРОИСХОДИТ С СИСТЕМОЙ", "paragraphs": [
                "Декомпенсация — это не кризис. Это сигнал. "
                "Тело и психика прекращают притворяться, что всё в порядке. "
                "Это может ощущаться как коллапс — но это первый шаг к реальной перестройке.",
            ]},
            {"heading": "04 / ПРИОРИТЕТНЫЙ ПРОТОКОЛ", "bullets": [
                "НЕМЕДЛЕННО: уберите из расписания всё необязательное на 2 недели",
                "Обратитесь к специалисту — психолог, врач, нутрициолог",
                "Социальная нагрузка: сократить до минимума, только восполняющие контакты",
                "Сон — приоритет номер один. Всё остальное подождёт",
                "Не принимайте важных решений в ближайшие 10–14 дней",
                "Прогулка 20–30 минут ежедневно — без телефона, без задач",
            ]},
            {"heading": "05 / ПОСЛАНИЕ", "paragraphs": [
                "Вы дошли до этой точки не потому что слабы. "
                "А потому что долго держали то, что должны были разделить с кем-то. "
                "Следующий шаг — не героизм. Это просьба о помощи и согласие принять её.",
            ]},
        ])

    else:
        return _build_pdf("ПРОФИЛЬ: ТОЧКА РАЗРЫВА", score, "Тип V — Критический", [
            {"heading": "01 / ЭКСТРЕННАЯ ОЦЕНКА", "paragraphs": [
                "Максимальный индекс нагрузки по всем пяти векторам. "
                "Это не диагноз — это карта. И она показывает: "
                "система работает на пределе, который уже пройден.",
                "Вы здесь. Вы читаете это. Это уже действие.",
            ]},
            {"heading": "02 / ПОЛНЫЙ СПЕКТР НАГРУЗКИ", "bullets": [
                "Эмоциональные сценарии воспроизводятся без изменений",
                "Внешние препятствия блокируют каждое новое начало",
                "Выбор сведён к минимуму — ресурс на нуле",
                "Внутренний шум полностью вытесняет собственный голос",
                "Будущее вызывает физическую реакцию тревоги или оцепенения",
            ]},
            {"heading": "03 / ПОНИМАНИЕ ТОЧКИ РАЗРЫВА", "paragraphs": [
                "Точка разрыва — это не конец. Это место, где старая система "
                "больше не может функционировать. И именно здесь становится возможным "
                "то, что было невозможно раньше: радикальное изменение.",
                "Но сначала — безопасность. Трансформация не происходит в кризисе. "
                "Она происходит после того, как кризис стабилизирован.",
            ]},
            {"heading": "04 / ПРОТОКОЛ ПЕРВЫХ 72 ЧАСОВ", "bullets": [
                "Свяжитесь с одним человеком, которому вы доверяете — сегодня",
                "Уберите все несрочные задачи на 72 часа",
                "Не принимайте никаких решений о будущем",
                "Еда, вода, сон — три единственных приоритета прямо сейчас",
                "Если есть мысли, что вы не справляетесь — это симптом перегрузки, не правда о вас",
            ]},
            {"heading": "05 / ПРОТОКОЛ СЛЕДУЮЩИХ 30 ДНЕЙ", "bullets": [
                "Еженедельные сессии с психологом или коучем",
                "Полный аудит обязательств — что можно остановить прямо сейчас",
                "Физическое восстановление: сон, питание, движение — без исключений",
                "Медиадетокс: социальные сети и новости — строгий лимит",
                "Один маленький акт самоуважения ежедневно",
            ]},
            {"heading": "06 / ФИНАЛЬНОЕ СЛОВО", "paragraphs": [
                "Разрыв — это не провал системы. "
                "Это момент, когда система наконец говорит правду.",
                "Вы заслуживаете помощи. Не когда станет лучше. Сейчас.",
            ]},
        ])


# ──────────────────────────────────────────────
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ──────────────────────────────────────────────

def build_question_keyboard(q_index: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"ans:{q_index}:{score}")]
        for label, score in QUESTIONS[q_index]["options"]
    ])


def get_verdict(score: int) -> str:
    if score <= 8:
        label = "ТИП I — УСТОЙЧИВОСТЬ"
        desc  = "Система интегрирована. Паттерны осознаны. Ресурс доступен.\nВектор — расширение."
    elif score <= 12:
        label = "ТИП II — ФОНОВОЕ НАПРЯЖЕНИЕ"
        desc  = "Адаптация работает — но стоит ресурса.\nСистема стабильна, движение — затруднено."
    elif score <= 15:
        label = "ТИП III — СИСТЕМНАЯ ПЕРЕГРУЗКА"
        desc  = "Компенсация на пределе. Стабильность — иллюзия.\nРазрыв между внешним и внутренним — значительный."
    elif score <= 18:
        label = "ТИП IV — КРИТИЧЕСКОЕ ИСТОЩЕНИЕ"
        desc  = "Механизмы адаптации исчерпаны.\nСистема держится на инерции и волевом контроле."
    else:
        label = "ТИП V — ТОЧКА РАЗРЫВА"
        desc  = "Максимальный индекс нагрузки по всем векторам.\nСтарая система больше не функционирует."
    return f"░░ ВЕРДИКТ: {label} ░░\n\n{desc}"


# ──────────────────────────────────────────────
# РОУТЕР И ХЭНДЛЕРЫ
# ──────────────────────────────────────────────
def create_payment_link(user_id: int, score: int) -> tuple[str, str]:
    import uuid
    label = f"{PAYMENT_LABEL_PREFIX}{user_id}_{uuid.uuid4().hex[:8]}"
    quickpay = Quickpay(
        receiver=YOOMONEY_WALLET,
        quickpay_form="shop",
        targets=f"Доступ к отчёту QuanTum Break (score={score})",
        paymentType="SB",
        sum=PAYMENT_AMOUNT,
        label=label,
    )
    return quickpay.base_url, label


def verify_payment(label: str) -> bool:
    try:
        client = Client(YOOMONEY_TOKEN)
        history = client.operation_history(label=label)
        for op in history.operations:
            if op.label == label and op.status == "success" and float(op.amount) >= PAYMENT_AMOUNT:
                return True
    except Exception as exc:
        logger.error("YooMoney history check failed: %s", exc)
    return False


def build_payment_keyboard(pay_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"💳 Оплатить {int(PAYMENT_AMOUNT)} руб  [тест]" if PAYMENT_AMOUNT <= 10
                 else f"💳 Оплатить доступ  {int(PAYMENT_AMOUNT)} руб  [было 690 руб]",
            url=pay_url,
        )],
        [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data="check_payment")],
    ])


router = Router()


async def send_question(target: Message, q_index: int, prev_message: Message | None = None) -> Message:
    """Отправляет вопрос новым сообщением, предыдущее удаляет.
    Возвращает новое сообщение для последующего удаления."""
    text = QUESTIONS[q_index]["text"]
    kb   = build_question_keyboard(q_index)
    img  = IMG_QUESTIONS[q_index]

    # Всегда удаляем предыдущее сообщение
    if prev_message:
        try:
            await prev_message.delete()
        except Exception:
            pass

    # Отправляем новое — с картинкой или без
    if os.path.exists(img):
        return await target.answer_photo(photo=FSInputFile(img), caption=text, reply_markup=kb)
    else:
        return await target.answer(text, reply_markup=kb)


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    text = (
        "█ СИСТЕМА ИНИЦИАЛИЗИРОВАНА █\n\n"
        "Добро пожаловать в диагностический модуль <b>Квантового Разлома</b>.\n\n"
        "Перед вами — 5 вопросов.\n"
        "Нет правильных ответов. Есть только ваши.\n\n"
        "Результат: персональный отчёт с профилем и протоколом действий.\n\n"
        "Готовы к диагнозу?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶ Начать тест", callback_data="start_test")]
    ])
    if os.path.exists(IMG_START):
        await message.answer_photo(photo=FSInputFile(IMG_START), caption=text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "start_test")
async def cb_start_test(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TestState.answering)
    await state.update_data(q_index=0, score=0)
    await callback.answer()
    new_msg = await send_question(callback.message, 0, prev_message=callback.message)
    await state.update_data(last_msg_id=new_msg.message_id)


@router.callback_query(StateFilter(TestState.answering), F.data.startswith("ans:"))
async def cb_answer(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        _, q_str, score_str = callback.data.split(":")
        q_index, score_delta = int(q_str), int(score_str)
    except ValueError:
        logger.warning("Malformed callback data: %s", callback.data)
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    data = await state.get_data()
    if q_index != data.get("q_index", 0):
        await callback.answer("Вопрос уже засчитан.")
        return

    new_score = data.get("score", 0) + score_delta
    next_q = q_index + 1

    if next_q < len(QUESTIONS):
        await state.update_data(q_index=next_q, score=new_score)
        await callback.answer()
        new_msg = await send_question(callback.message, next_q, prev_message=callback.message)
        await state.update_data(last_msg_id=new_msg.message_id)
        return

    # ── Последний вопрос ──────────────────────
    await callback.answer()
    await state.set_state(TestState.generating)
    await state.update_data(score=new_score)

    verdict = get_verdict(new_score)

    # ШАГ 1: удаляем сообщение с вопросом, отправляем лоадер новым сообщением
    try:
        await callback.message.delete()
    except Exception:
        pass
    loader_msg = await callback.message.answer(
        f"{verdict}\n\n──────────────────────\n"
        f"<code>░░░░░░░░░░   0%</code>\n<i>Инициализация...</i>"
    )

    # ШАГ 2: только ПОСЛЕ того как экран отрисован — запускаем генерацию PDF в фоне
    loop = asyncio.get_event_loop()
    pdf_future = loop.run_in_executor(None, generate_report, new_score)

    # ШАГ 3: анимируем лоадер (параллельно с генерацией в фоне)
    await show_loader(loader_msg, verdict)

    # ШАГ 4: лоадер закончился — ждём PDF (уже готов в фоне)
    await pdf_future  # убеждаемся что генерация завершена без ошибок

    user_id = callback.from_user.id
    user_scores[user_id] = new_score

    try:
        pay_url, label = create_payment_link(user_id, new_score)
        pending_payments[label] = user_id
        await state.update_data(payment_label=label)
        await state.set_state(TestState.awaiting_payment)

        # Финальная картинка + кнопка оплаты
        verdict_short = get_verdict(new_score)
        pay_caption = (
            f"{verdict_short}\n\n"
            "──────────────────────\n"
            "Полный отчёт с протоколом действий <b>заблокирован</b>.\n"
            "Для разблокировки — произведите оплату."
        )
        if os.path.exists(IMG_FINAL):
            await loader_msg.answer_photo(
                photo=FSInputFile(IMG_FINAL),
                caption=pay_caption,
                reply_markup=build_payment_keyboard(pay_url),
            )
        else:
            await loader_msg.answer(pay_caption, reply_markup=build_payment_keyboard(pay_url))

    except Exception as exc:
        logger.error("Payment link creation failed for user %s: %s", callback.from_user.id, exc)
        await loader_msg.answer("Система временно недоступна. Попробуйте /start")


@router.callback_query(StateFilter(TestState.awaiting_payment), F.data == "check_payment")
async def cb_check_payment(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    label: str | None = data.get("payment_label")
    score: int = data.get("score", 0)

    if not label:
        await callback.answer("Данные платежа не найдены. Пройдите тест заново.", show_alert=True)
        return

    await callback.answer("Проверяю платёж…")
    paid = await asyncio.get_event_loop().run_in_executor(None, verify_payment, label)
    if paid:
        await deliver_report(callback.message, score, state)
    else:
        await callback.message.answer(
            "░ Оплата пока не зафиксирована.\nПосле оплаты нажмите «Проверить оплату» повторно."
        )


async def deliver_report(message: Message, score: int, state: FSMContext) -> None:
    try:
        pdf_bytes = await asyncio.get_event_loop().run_in_executor(None, generate_report, score)
        await message.answer_document(
            BufferedInputFile(pdf_bytes, filename="quantum_break_report.pdf"),
            caption=(
                "█ ОТЧЁТ РАЗБЛОКИРОВАН █\n\n"
                "Изучите протокол. Следуйте инструкциям.\n"
                "Система не ждёт — но и не торопит."
            ),
        )
        logger.info("Report delivered after payment. Score=%d", score)
        await state.clear()
    except Exception as exc:
        logger.error("Failed to generate/send PDF: %s", exc)
        await message.answer("Ошибка при генерации отчёта. Свяжитесь с поддержкой.")


# ──────────────────────────────────────────────
# TELEGRAM WEBHOOK
# ──────────────────────────────────────────────
async def telegram_webhook_handler(request: web.Request) -> web.Response:
    from aiogram.types import Update
    bot: Bot = request.app["bot"]
    dp: Dispatcher = request.app["dp"]
    try:
        update = Update(**(await request.json()))
        await dp.feed_update(bot, update)
    except Exception as exc:
        logger.error("Telegram webhook error: %s", exc)
    return web.Response(status=200)


import hashlib

async def yoomoney_notify_handler(request: web.Request) -> web.Response:
    try:
        data = await request.post()
        logger.info("YooMoney notify: %s", dict(data))

        check_str = "&".join([
            data.get("notification_type", ""), data.get("operation_id", ""),
            data.get("amount", ""),            data.get("currency", ""),
            data.get("datetime", ""),          data.get("sender", ""),
            data.get("codepro", ""),           YOOMONEY_SECRET,
            data.get("label", ""),
        ])
        expected = hashlib.sha1(check_str.encode()).hexdigest()
        if expected != data.get("sha1_hash", ""):
            logger.warning("YooMoney: invalid SHA1 for label=%s", data.get("label"))
            return web.Response(status=400, text="bad signature")

        label = data.get("label", "")
        if label in pending_payments:
            user_id = pending_payments.pop(label)
            score   = user_scores.get(user_id, 0)
            bot: Bot = request.app["bot"]
            pdf_bytes = generate_report(score)
            await bot.send_document(
                user_id,
                BufferedInputFile(pdf_bytes, filename="quantum_break_report.pdf"),
                caption="█ ОПЛАТА ПОДТВЕРЖДЕНА █\n\nВаш отчёт — ниже.\nДействуйте по протоколу.",
            )
            logger.info("Auto-delivered via webhook. user_id=%s score=%d", user_id, score)
    except Exception as exc:
        logger.error("YooMoney notify handler error: %s", exc)
    return web.Response(status=200, text="ok")


async def on_startup(app: web.Application) -> None:
    await app["bot"].set_webhook(WEBHOOK_URL)
    logger.info("Webhook set: %s", WEBHOOK_URL)


async def on_shutdown(app: web.Application) -> None:
    await app["bot"].delete_webhook()
    await app["bot"].session.close()
    logger.info("Bot stopped.")


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────
def main() -> None:
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    app = web.Application()
    app["bot"] = bot
    app["dp"] = dp
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.router.add_post(WEBHOOK_PATH, telegram_webhook_handler)
    app.router.add_post(YOOMONEY_NOTIFY_PATH, yoomoney_notify_handler)

    logger.info("Starting on port %d", WEB_PORT)
    web.run_app(app, host="0.0.0.0", port=WEB_PORT)


if __name__ == "__main__":
    main()