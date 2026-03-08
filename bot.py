"""
Telegram-бот | «Квантовый разлом» — диагностический тест
Стек: aiogram 3.x, FSM, YooMoney, aiohttp webhook, reportlab PDF
"""

import asyncio
import hashlib
import logging
import os
import uuid
import io
from typing import Optional

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

# ──────────────────────────────────────────────
# КОНФИГУРАЦИЯ
# ──────────────────────────────────────────────
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "REPLACE_WITH_NEW_TOKEN")
YOOMONEY_TOKEN: str = os.getenv("YOOMONEY_TOKEN", "YOUR_YOOMONEY_TOKEN")
YOOMONEY_WALLET: str = os.getenv("YOOMONEY_WALLET", "YOUR_WALLET_NUMBER")
YOOMONEY_SECRET: str = os.getenv("YOOMONEY_SECRET", "YOUR_YOOMONEY_SECRET")
WEBHOOK_HOST: str = os.getenv("WEBHOOK_HOST", "https://yourdomain.com")
WEBHOOK_PATH: str = "/webhook/telegram"
WEBHOOK_URL: str = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
YOOMONEY_NOTIFY_PATH: str = "/webhook/yoomoney"
WEB_PORT: int = int(os.getenv("PORT", 8080))

PAYMENT_AMOUNT: float = 390.0
PAYMENT_LABEL_PREFIX: str = "report_"

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
# ВОПРОСЫ ТЕСТА
# Баллы: А=4, Б=3, В=2, Г=1  |  Итого: 5–20
# 5–8   → Тип I    (Устойчивость)
# 9–12  → Тип II   (Фоновое напряжение)
# 13–15 → Тип III  (Системная перегрузка)
# 16–18 → Тип IV   (Критическое истощение)
# 19–20 → Тип V    (Точка разрыва)
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
    answering = State()
    awaiting_payment = State()


pending_payments: dict[str, int] = {}
user_scores: dict[int, int] = {}


# ══════════════════════════════════════════════
# PDF ГЕНЕРАТОР — 5 вариантов отчётов
# ══════════════════════════════════════════════

COLOR_ACCENT = colors.HexColor("#C8A96E")
COLOR_TEXT   = colors.HexColor("#1A1A1A")
COLOR_MUTED  = colors.HexColor("#666666")
COLOR_LINE   = colors.HexColor("#C8A96E")


def _styles() -> dict:
    return {
        "title": ParagraphStyle(
            "title", fontName="Helvetica-Bold", fontSize=20,
            textColor=COLOR_ACCENT, alignment=TA_CENTER,
            spaceAfter=4, leading=26,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", fontName="Helvetica", fontSize=10,
            textColor=COLOR_MUTED, alignment=TA_CENTER, spaceAfter=4,
        ),
        "section": ParagraphStyle(
            "section", fontName="Helvetica-Bold", fontSize=12,
            textColor=COLOR_ACCENT, spaceAfter=3, spaceBefore=10,
        ),
        "body": ParagraphStyle(
            "body", fontName="Helvetica", fontSize=10,
            textColor=COLOR_TEXT, alignment=TA_JUSTIFY,
            spaceAfter=5, leading=15,
        ),
        "bullet": ParagraphStyle(
            "bullet", fontName="Helvetica", fontSize=10,
            textColor=COLOR_TEXT, leftIndent=10,
            spaceAfter=3, leading=14,
        ),
        "footer": ParagraphStyle(
            "footer", fontName="Helvetica", fontSize=8,
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
    """Генерирует один из 5 PDF-отчётов в зависимости от балла."""

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


def build_payment_keyboard(pay_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="💳 Оплатить доступ к отчёту  390 руб  [было 690 руб]",
            url=pay_url,
        )],
        [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data="check_payment")],
    ])


def get_verdict(score: int) -> str:
    if score <= 8:
        label, desc = "ТИП I — УСТОЙЧИВОСТЬ", (
            "Система интегрирована. Паттерны осознаны. Ресурс доступен.\n"
            "Вектор — расширение.\n\n"
            "Отчёт содержит карту роста и протокол следующего уровня."
        )
    elif score <= 12:
        label, desc = "ТИП II — ФОНОВОЕ НАПРЯЖЕНИЕ", (
            "Адаптация работает — но стоит ресурса.\n"
            "Система стабильна, движение — затруднено.\n\n"
            "Отчёт содержит источники утечки и протокол восстановления баланса."
        )
    elif score <= 15:
        label, desc = "ТИП III — СИСТЕМНАЯ ПЕРЕГРУЗКА", (
            "Компенсация на пределе. Стабильность — иллюзия.\n"
            "Разрыв между внешним и внутренним — значительный.\n\n"
            "Отчёт содержит механику перегрузки и экстренный протокол."
        )
    elif score <= 18:
        label, desc = "ТИП IV — КРИТИЧЕСКОЕ ИСТОЩЕНИЕ", (
            "Механизмы адаптации исчерпаны.\n"
            "Система держится на инерции и волевом контроле.\n\n"
            "Отчёт содержит приоритетный протокол и карту выхода."
        )
    else:
        label, desc = "ТИП V — ТОЧКА РАЗРЫВА", (
            "Максимальный индекс нагрузки по всем векторам.\n"
            "Старая система больше не функционирует.\n\n"
            "Отчёт содержит протокол первых 72 часов и план на 30 дней."
        )
    return f"░░ ВЕРДИКТ: {label} ░░\n\n{desc}"


def create_payment_link(user_id: int, score: int) -> tuple[str, str]:
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


def verify_payment_by_history(label: str) -> bool:
    try:
        client = Client(YOOMONEY_TOKEN)
        history = client.operation_history(label=label)
        for op in history.operations:
            if op.label == label and op.status == "success" and float(op.amount) >= PAYMENT_AMOUNT:
                return True
    except Exception as exc:
        logger.error("YooMoney history check failed: %s", exc)
    return False


# ──────────────────────────────────────────────
# РОУТЕР И ХЭНДЛЕРЫ
# ──────────────────────────────────────────────
router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "█ СИСТЕМА ИНИЦИАЛИЗИРОВАНА █\n\n"
        "Добро пожаловать в диагностический модуль <b>Квантового Разлома</b>.\n\n"
        "Перед вами — 5 вопросов.\n"
        "Нет правильных ответов. Есть только ваши.\n\n"
        "Результат: персональный отчёт с профилем и протоколом действий.\n\n"
        "Готовы к диагнозу?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="▶ Начать тест", callback_data="start_test")]
        ]),
    )


@router.callback_query(F.data == "start_test")
async def cb_start_test(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TestState.answering)
    await state.update_data(q_index=0, score=0)
    await callback.message.edit_text(
        QUESTIONS[0]["text"],
        reply_markup=build_question_keyboard(0),
    )
    await callback.answer()


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
        await callback.message.edit_text(
            QUESTIONS[next_q]["text"],
            reply_markup=build_question_keyboard(next_q),
        )
    else:
        user_id = callback.from_user.id
        user_scores[user_id] = new_score
        await state.update_data(score=new_score)

        try:
            pay_url, label = create_payment_link(user_id, new_score)
            pending_payments[label] = user_id
            await state.update_data(payment_label=label)
            await state.set_state(TestState.awaiting_payment)
            await callback.message.edit_text(
                f"{get_verdict(new_score)}\n\n"
                "──────────────────────\n"
                "Полный отчёт с протоколом действий <b>заблокирован</b>.\n"
                "Для разблокировки — произведите оплату.",
                reply_markup=build_payment_keyboard(pay_url),
            )
        except Exception as exc:
            logger.error("Payment link creation failed for user %s: %s", user_id, exc)
            await callback.message.edit_text("Система временно недоступна. Попробуйте /start")

    await callback.answer()


@router.callback_query(StateFilter(TestState.awaiting_payment), F.data == "check_payment")
async def cb_check_payment(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    label: Optional[str] = data.get("payment_label")
    score: int = data.get("score", 0)

    if not label:
        await callback.answer("Данные платежа не найдены. Пройдите тест заново.", show_alert=True)
        return

    await callback.answer("Проверяю платёж…")
    if verify_payment_by_history(label):
        await deliver_report(callback.message, score, state)
    else:
        await callback.message.answer(
            "░ Оплата пока не зафиксирована.\nПосле оплаты нажмите «Проверить оплату» повторно."
        )


async def deliver_report(message: Message, score: int, state: FSMContext) -> None:
    try:
        pdf_bytes = generate_report(score)
        await message.answer_document(
            BufferedInputFile(pdf_bytes, filename="quantum_break_report.pdf"),
            caption=(
                "█ ОТЧЁТ РАЗБЛОКИРОВАН █\n\n"
                "Изучите протокол. Следуйте инструкциям.\n"
                "Система не ждёт — но и не торопит."
            ),
        )
        logger.info("Report delivered. Score=%d", score)
        await state.clear()
    except Exception as exc:
        logger.error("Failed to generate/send PDF: %s", exc)
        await message.answer("Ошибка при генерации отчёта. Свяжитесь с поддержкой.")


# ──────────────────────────────────────────────
# YOOMONEY WEBHOOK
# ──────────────────────────────────────────────
async def yoomoney_notify_handler(request: web.Request) -> web.Response:
    try:
        data = await request.post()
        logger.info("YooMoney notify: %s", dict(data))

        check_str = "&".join([
            data.get("notification_type", ""), data.get("operation_id", ""),
            data.get("amount", ""), data.get("currency", ""),
            data.get("datetime", ""), data.get("sender", ""),
            data.get("codepro", ""), YOOMONEY_SECRET, data.get("label", ""),
        ])
        expected = hashlib.sha1(check_str.encode()).hexdigest()

        if expected != data.get("sha1_hash", ""):
            logger.warning("YooMoney: invalid SHA1 for label=%s", data.get("label"))
            return web.Response(status=400, text="bad signature")

        label = data.get("label", "")
        if label in pending_payments:
            user_id = pending_payments.pop(label)
            score = user_scores.get(user_id, 0)
            bot: Bot = request.app["bot"]
            pdf_bytes = generate_report(score)
            await bot.send_document(
                user_id,
                BufferedInputFile(pdf_bytes, filename="quantum_break_report.pdf"),
                caption="█ ОПЛАТА ПОДТВЕРЖДЕНА █\n\nВаш отчёт — ниже.\nДействуйте по протоколу.",
            )
            logger.info("Auto-delivered report via webhook. user_id=%s score=%d", user_id, score)

    except Exception as exc:
        logger.error("YooMoney notify handler error: %s", exc)

    return web.Response(status=200, text="ok")


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
