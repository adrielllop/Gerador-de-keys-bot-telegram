import asyncio
import html
import json
import logging
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from firebase_admin import credentials, firestore, initialize_app
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# =========================
# Configuração
# =========================
ADMIN_ID = 8646015281
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8796716322:AAGqHq-kXrehQ7YT3i5372sXvCgs6kG87X4")  # Cole aqui o token do BotFather se não usar variável de ambiente

DURATION_LABELS = {
    "1h": "🕐 Teste 1 hora",
    "1": "1 dia",
    "3": "3 dias",
    "7": "7 dias",
    "15": "15 dias",
    "30": "30 dias",
    "permanent": "♾️ Permanente",
}
DURATION_MS = {
    "1h": 60 * 60 * 1000,
    "1": 1 * 24 * 60 * 60 * 1000,
    "3": 3 * 24 * 60 * 60 * 1000,
    "7": 7 * 24 * 60 * 60 * 1000,
    "15": 15 * 24 * 60 * 60 * 1000,
    "30": 30 * 24 * 60 * 60 * 1000,
}
KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("mx-key-bot")


def get_firestore_client():
    """Inicializa o Firebase usando JSON ou arquivo de service account."""
    if os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON"):
        service_account = json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT_JSON"])
        initialize_app(credentials.Certificate(service_account))
    elif os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        initialize_app(credentials.Certificate(os.environ["GOOGLE_APPLICATION_CREDENTIALS"]))
    else:
        local_file = Path("firebase-service-account.json")
        if not local_file.exists():
            raise RuntimeError(
                "Configure FIREBASE_SERVICE_ACCOUNT_JSON ou GOOGLE_APPLICATION_CREDENTIALS."
            )
        initialize_app(credentials.Certificate(str(local_file)))
    return firestore.client()


DB = None
FIREBASE_ERROR = ""
try:
    DB = get_firestore_client()
except Exception as exc:
    FIREBASE_ERROR = str(exc)
    logger.exception("Firebase não foi inicializado. O bot continuará respondendo ao /start.")


def require_db() -> Any:
    if DB is None:
        raise RuntimeError(
            "Firebase não configurado. Defina FIREBASE_SERVICE_ACCOUNT_JSON ou "
            "GOOGLE_APPLICATION_CREDENTIALS e reinicie o bot."
        )
    return DB


def allowed(update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id == ADMIN_ID)


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def generate_key(prefix: str, existing: set[str]) -> str:
    prefix = prefix.strip() or "MX-"
    while True:
        value = prefix + "".join(secrets.choice(KEY_ALPHABET) for _ in range(16))
        if value not in existing:
            return value


def duration_label(duration: str) -> str:
    return DURATION_LABELS.get(duration, duration)


def format_date(value: Any) -> str:
    if not value:
        return "—"
    if hasattr(value, "timestamp"):
        timestamp = value.timestamp()
    else:
        timestamp = float(value) / 1000
    return datetime.fromtimestamp(timestamp, timezone.utc).astimezone().strftime("%d/%m/%Y %H:%M")


def key_status(data: dict[str, Any]) -> str:
    if data.get("paused"):
        return "Pausada"
    if not data.get("device"):
        return "Não ativada"
    expires = data.get("expires")
    if expires and expires <= now_ms():
        return "Expirada"
    return "Ativa"


def key_ref(key_id: str):
    return require_db().collection("keys").document(key_id)


def all_keys() -> list[dict[str, Any]]:
    docs = require_db().collection("keys").order_by("created", direction=firestore.Query.DESCENDING).stream()
    result = []
    for doc in docs:
        result.append({"id": doc.id, **doc.to_dict()})
    return result


def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Gerar keys", callback_data="menu_generate")],
            [
                InlineKeyboardButton("🕐 Gerar teste 1h", callback_data="generate_test"),
                InlineKeyboardButton("📊 Estatísticas", callback_data="stats"),
            ],
            [
                InlineKeyboardButton("📋 Listar keys", callback_data="list_0"),
                InlineKeyboardButton("⬇ Exportar TXT", callback_data="export"),
            ],
        ]
    )


async def deny(update: Update) -> None:
    if update.callback_query:
        await update.callback_query.answer("Acesso não autorizado.", show_alert=True)
    elif update.effective_message:
        await update.effective_message.reply_text("Acesso não autorizado.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        await deny(update)
        return
    if DB is None:
        await update.effective_message.reply_text(
            "🔐 <b>MX Key Manager</b>\n\n"
            "✅ Bot online e seu acesso foi autorizado.\n\n"
            "⚠️ O Firebase ainda não está configurado. Configure "
            "<code>FIREBASE_SERVICE_ACCOUNT_JSON</code> ou "
            "<code>GOOGLE_APPLICATION_CREDENTIALS</code> e reinicie o bot.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❓ Ajuda", callback_data="help")]]),
        )
        return
    await update.effective_message.reply_text(
        "🔐 <b>MX Key Manager</b>\n\nEscolha uma operação:",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        await deny(update)
        return
    text = (
        "❓ <b>Comandos disponíveis</b>\n\n"
        "/start — abrir o painel\n/gerar — gerar keys\n/teste — gerar teste de 1 hora\n"
        "/listar — listar keys\n/buscar TEXTO — procurar por key, produto ou dispositivo\n"
        "/filtrar STATUS — ativa, expirada, pausada ou nao_ativada\n"
        "/estatisticas — mostrar totais\n/exportar — enviar TXT\n/ajuda — mostrar esta mensagem"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=main_keyboard())


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        await deny(update)
        return
    term = " ".join(context.args).strip().lower()
    if not term:
        await update.effective_message.reply_text("Use: /buscar MX-ABC123 ou /buscar MestreXit")
        return
    try:
        items = await asyncio.to_thread(all_keys)
    except Exception as exc:
        await update.effective_message.reply_text(f"⚠️ Erro ao consultar o Firebase: {exc}")
        return
    matches = [item for item in items if term in str(item.get("key", "")).lower() or term in str(item.get("product", "")).lower() or term in str(item.get("device", "")).lower()]
    if not matches:
        await update.effective_message.reply_text("🔎 Nenhuma key encontrada.")
        return
    lines = [f"🔎 <b>{len(matches)} resultado(s)</b>\n"]
    for item in matches[:30]:
        lines.append(f"<code>{html.escape(str(item.get('key', item['id'])))}</code> — {html.escape(duration_label(str(item.get('duration', ''))))} — <b>{html.escape(key_status(item))}</b>")
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=main_keyboard())


async def filter_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        await deny(update)
        return
    raw = " ".join(context.args).strip().lower().replace("-", "_")
    aliases = {"ativa": "Ativa", "ativas": "Ativa", "active": "Ativa", "expirada": "Expirada", "expiradas": "Expirada", "expired": "Expirada", "pausada": "Pausada", "pausadas": "Pausada", "paused": "Pausada", "nao_ativada": "Não ativada", "não_ativada": "Não ativada", "waiting": "Não ativada"}
    status = aliases.get(raw)
    if not status:
        await update.effective_message.reply_text("Use: /filtrar ativa, /filtrar expirada, /filtrar pausada ou /filtrar nao_ativada")
        return
    try:
        items = await asyncio.to_thread(all_keys)
    except Exception as exc:
        await update.effective_message.reply_text(f"⚠️ Erro ao consultar o Firebase: {exc}")
        return
    matches = [item for item in items if key_status(item) == status]
    if not matches:
        await update.effective_message.reply_text(f"📋 Nenhuma key com status: {status}.")
        return
    lines = [f"📋 <b>{status}</b> — {len(matches)} key(s)\n"]
    lines.extend(f"<code>{html.escape(str(item.get('key', item['id'])))}</code>" for item in matches[:30])
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=main_keyboard())


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        await deny(update)
        return
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔐 <b>MX Key Manager</b>\n\nEscolha uma operação:", parse_mode=ParseMode.HTML, reply_markup=main_keyboard())


async def ask_prefix(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not allowed(update):
        await deny(update)
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    context.user_data["duration"] = "1"
    await query.edit_message_text("Digite o prefixo da key (ex.: <code>MX-</code>):", parse_mode=ParseMode.HTML)
    return PREFIX


async def prefix_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["prefix"] = update.effective_message.text.strip() or "MX-"
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("MestreXit.ia", callback_data="product_MestreXit.ia")],
            [InlineKeyboardButton("MestreXit.ia V2", callback_data="product_MestreXit.ia V2")],
        ]
    )
    await update.effective_message.reply_text("Escolha o produto:", reply_markup=keyboard)
    return PRODUCT


async def product_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["product"] = query.data.removeprefix("product_")
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("1 dia", callback_data="duration_1"), InlineKeyboardButton("3 dias", callback_data="duration_3")],
            [InlineKeyboardButton("7 dias", callback_data="duration_7"), InlineKeyboardButton("15 dias", callback_data="duration_15")],
            [InlineKeyboardButton("30 dias", callback_data="duration_30"), InlineKeyboardButton("Permanente", callback_data="duration_permanent")],
        ]
    )
    await query.edit_message_text("Escolha o período:", reply_markup=keyboard)
    return DURATION


async def duration_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["duration"] = query.data.removeprefix("duration_")
    await query.edit_message_text("Digite a quantidade de keys (1 a 100):")
    return QUANTITY


async def quantity_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not allowed(update):
        await deny(update)
        return ConversationHandler.END
    try:
        quantity = max(1, min(100, int(update.effective_message.text.strip())))
    except ValueError:
        await update.effective_message.reply_text("Quantidade inválida. Digite um número entre 1 e 100:")
        return QUANTITY
    context.user_data["quantity"] = quantity
    created = await asyncio.to_thread(create_keys, context.user_data)
    await update.effective_message.reply_text(
        f"✅ <b>{len(created)} key(s) gerada(s)</b>\n\n<code>{html.escape(chr(10).join(created))}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )
    return ConversationHandler.END


def create_keys(data: dict[str, Any]) -> list[str]:
    existing = {item.get("key") for item in all_keys()}
    created = []
    for _ in range(int(data["quantity"])):
        key = generate_key(data.get("prefix", "MX-"), existing)
        existing.add(key)
        key_ref(key).set(
            {
                "key": key,
                "duration": data["duration"],
                "product": data.get("product", "MestreXit.ia"),
                "created": now_ms(),
                "activatedAt": None,
                "expires": None,
                "device": None,
                "paused": False,
            }
        )
        created.append(key)
    return created


async def generate_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        await deny(update)
        return
    query = update.callback_query
    await query.answer()
    data = {"prefix": "MX-", "product": "MestreXit.ia", "duration": "1h", "quantity": 1}
    created = await asyncio.to_thread(create_keys, data)
    await query.edit_message_text(
        f"✅ <b>Teste de 1 hora gerado</b>\n\n<code>{html.escape(created[0])}</code>\n\nA contagem começa quando o dispositivo for ativado.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        await deny(update)
        return
    query = update.callback_query
    await query.answer()
    items = await asyncio.to_thread(all_keys)
    counts = {"Não ativada": 0, "Ativa": 0, "Expirada": 0, "Pausada": 0}
    for item in items:
        counts[key_status(item)] += 1
    text = (
        "📊 <b>Estatísticas</b>\n\n"
        f"Total: <b>{len(items)}</b>\n"
        f"Não ativadas: <b>{counts['Não ativada']}</b>\n"
        f"Ativas: <b>{counts['Ativa']}</b>\n"
        f"Expiradas: <b>{counts['Expirada']}</b>\n"
        f"Pausadas: <b>{counts['Pausada']}</b>"
    )
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=main_keyboard())


async def list_keys(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        await deny(update)
        return
    query = update.callback_query
    await query.answer()
    page = int(query.data.removeprefix("list_"))
    items = await asyncio.to_thread(all_keys)
    page_size = 8
    chunk = items[page * page_size : (page + 1) * page_size]
    if not chunk:
        text = "📋 <b>Keys cadastradas</b>\n\nNenhuma key cadastrada."
    else:
        lines = ["📋 <b>Keys cadastradas</b>\n"]
        for item in chunk:
            lines.append(
                f"<code>{html.escape(str(item.get('key', item['id'])))}</code>\n"
                f"Produto: {html.escape(str(item.get('product', 'MestreXit.ia')))}\n"
                f"Plano: {html.escape(duration_label(str(item.get('duration', ''))))}\n"
                f"Status: <b>{html.escape(key_status(item))}</b>\n"
                f"Dispositivo: {html.escape(str(item.get('device') or 'Não cadastrado'))}\n"
            )
        text = "\n".join(lines)
        action_rows = []
        for item in chunk:
            key_id = item["id"]
            pause_text = "▶ Retomar" if item.get("paused") else "⏸ Pausar"
            action_rows.append([
                InlineKeyboardButton(f"{pause_text} {item.get('key', key_id)[-6:]}", callback_data=f"action:pause:{key_id}"),
                InlineKeyboardButton("+Tempo", callback_data=f"action:time:{key_id}"),
                InlineKeyboardButton("Resetar", callback_data=f"action:reset:{key_id}"),
                InlineKeyboardButton("Excluir", callback_data=f"action:delete:{key_id}"),
            ])
    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton("◀ Anterior", callback_data=f"list_{page - 1}"))
    if (page + 1) * page_size < len(items):
        buttons.append(InlineKeyboardButton("Próxima ▶", callback_data=f"list_{page + 1}"))
    keyboard = action_rows if chunk else []
    if buttons:
        keyboard.append(buttons)
    keyboard.append([InlineKeyboardButton("⬅ Menu", callback_data="menu")])
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))


async def export_keys(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        await deny(update)
        return
    query = update.callback_query
    await query.answer()
    items = await asyncio.to_thread(all_keys)
    if not items:
        await query.edit_message_text("Não existem keys cadastradas.", reply_markup=main_keyboard())
        return
    path = Path("keys_mx_key_manager.txt")
    path.write_text("\n".join(f"{item.get('key', item['id'])} | {duration_label(str(item.get('duration', '')))}" for item in items), encoding="utf-8")
    await query.message.reply_document(InputFile(path), caption="⬇ Exportação das keys")
    path.unlink(missing_ok=True)


async def key_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        await deny(update)
        return
    query = update.callback_query
    await query.answer()
    _, action, key_id = query.data.split(":", 2)
    ref = key_ref(key_id)
    snapshot = ref.get()
    if not snapshot.exists:
        await query.message.reply_text("Key não encontrada.")
        return
    data = snapshot.to_dict()
    if action == "pause":
        ref.update({"paused": not bool(data.get("paused"))})
        await query.message.reply_text("Key retomada." if data.get("paused") else "Key pausada.")
    elif action == "reset":
        ref.update({"device": None, "paused": False})
        await query.message.reply_text("Dispositivo removido. A validade e o tempo contado foram mantidos.")
    elif action == "delete":
        ref.delete()
        await query.message.reply_text("Key excluída.")
    elif action == "time":
        if not data.get("device"):
            await query.message.reply_text("A key ainda não foi ativada.")
            return
        if data.get("duration") == "permanent":
            await query.message.reply_text("Essa key é permanente.")
            return
        context.user_data["time_key_id"] = key_id
        unit = "horas" if data.get("duration") == "1h" else "dias"
        context.user_data["time_unit"] = unit
        await query.message.reply_text(f"Quantos {unit} deseja adicionar? Digite um número:")
        return
    await query.message.reply_text("Operação concluída.", reply_markup=main_keyboard())


async def add_time_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        await deny(update)
        return
    key_id = context.user_data.get("time_key_id")
    if not key_id:
        return
    try:
        quantity = float(update.effective_message.text.strip())
        if quantity <= 0:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text("Digite uma quantidade válida maior que zero:")
        return
    ref = key_ref(key_id)
    data = ref.get().to_dict()
    old_expires = data.get("expires") or 0
    base = max(now_ms(), old_expires)
    multiplier = 60 * 60 * 1000 if context.user_data.get("time_unit") == "horas" else 24 * 60 * 60 * 1000
    ref.update({"expires": int(base + quantity * multiplier), "paused": False})
    context.user_data.pop("time_key_id", None)
    await update.effective_message.reply_text("✅ Tempo adicionado e key retomada.", reply_markup=main_keyboard())


async def text_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if allowed(update):
        await update.effective_message.reply_text("Use /start para abrir o painel.", reply_markup=main_keyboard())
    else:
        await deny(update)


PREFIX, PRODUCT, DURATION, QUANTITY = range(4)


def build_application() -> Application:
    if not BOT_TOKEN or BOT_TOKEN == "COLOQUE_SEU_TOKEN_AQUI":
        raise RuntimeError("Configure TELEGRAM_BOT_TOKEN antes de iniciar o bot.")
    app = Application.builder().token(BOT_TOKEN).build()
    generation = ConversationHandler(
        entry_points=[CallbackQueryHandler(ask_prefix, pattern="^menu_generate$")],
        states={
            PREFIX: [MessageHandler(filters.TEXT & ~filters.COMMAND, prefix_received)],
            PRODUCT: [CallbackQueryHandler(product_received, pattern="^product_")],
            DURATION: [CallbackQueryHandler(duration_received, pattern="^duration_")],
            QUANTITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, quantity_received)],
        },
        fallbacks=[CommandHandler("start", start)],
        per_user=True,
        per_chat=True,
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler(["ajuda", "help", "comandos"], help_command))
    app.add_handler(CommandHandler("buscar", search_command))
    app.add_handler(CommandHandler("filtrar", filter_command))
    app.add_handler(generation)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, add_time_value))
    app.add_handler(CallbackQueryHandler(menu, pattern="^menu$"))
    app.add_handler(CallbackQueryHandler(help_command, pattern="^help$"))
    app.add_handler(CallbackQueryHandler(generate_test, pattern="^generate_test$"))
    app.add_handler(CallbackQueryHandler(stats, pattern="^stats$"))
    app.add_handler(CallbackQueryHandler(list_keys, pattern=r"^list_\d+$"))
    app.add_handler(CallbackQueryHandler(export_keys, pattern="^export$"))
    app.add_handler(CallbackQueryHandler(key_action, pattern="^action:(pause|time|reset|delete):"))
    app.add_handler(MessageHandler(filters.ALL, text_fallback))
    return app


if __name__ == "__main__":
    application = build_application()
    application.run_polling(allowed_updates=Update.ALL_TYPES)
