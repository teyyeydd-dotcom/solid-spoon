import asyncio
import logging
import re
import datetime
import time
import random
import os
import json
from typing import Dict, List
# ⚡ Colored button support (Telethon 1.45+)
from telethon.tl import types as tl_types
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    FloodWaitError,
    UserAlreadyParticipantError,
    InviteRequestSentError,
    MessageNotModifiedError,
)
from telethon.tl.functions.messages import (
    ImportChatInviteRequest,
    SendReactionRequest,
    GetMessagesViewsRequest,
    CheckChatInviteRequest,
)
from telethon.tl.functions.channels import (
    JoinChannelRequest,
    LeaveChannelRequest,
    GetFullChannelRequest,
)
from telethon.tl.functions.account import (
    UpdateStatusRequest,
    SetPrivacyRequest,
    UpdateProfileRequest,
)

from telethon.tl.functions.phone import JoinGroupCallRequest, LeaveGroupCallRequest
from telethon.tl.types import (
    PeerChannel,
    ReactionEmoji,
    InputPrivacyKeyStatusTimestamp,
    InputPrivacyValueAllowAll,
    InputPrivacyValueDisallowAll,
    DataJSON,
    ChannelParticipantAdmin,
    ChannelParticipantCreator,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ======================= CONFIG =======================
API_ID = int(os.getenv("API_ID", "30434687"))
API_HASH = os.getenv("API_HASH", "d4adb52774efd1cbbbebdfc1647b4ee9")
BOT_TOKEN = os.getenv("BOT_TOKEN", "8607328636:AAFAwU27rCF9Ojs69FwgZIzTficZwLDxbQA")
OWNER_IDS = [5498096445, 5498096445]

KEEP_ALIVE_INTERVAL = 15
START_TIME = datetime.datetime.now()
DATA_FILE = "bot_data.json"

active_clients: Dict[str, TelegramClient] = {}
task_states: Dict[int, dict] = {}
VC_MONITORS: Dict[str, asyncio.Task] = {}
VC_JOINED: Dict[str, set] = {} 
SHUTDOWN = False

account_modes: Dict[str, str] = {}
temp_online_until: Dict[str, float] = {}
next_random_toggle: Dict[str, float] = {}

# ======================= COLORED BUTTON (Telethon 1.45+) =======================
def _btn(text, callback_data=None, url=None, style=None):
    """
    Colored inline button helper (Telethon 1.45+)
    style: None | "primary" | "success" | "danger"
    Fallback → plain Button.inline / Button.url (purane Telethon ke liye)
    """
    KB_Inline = getattr(tl_types, "KeyboardInlineButton", None)
    IB_Callback = getattr(tl_types, "InlineButtonTypeCallback", None)
    IB_Url = getattr(tl_types, "InlineButtonTypeUrl", None)
    KB_Style = getattr(tl_types, "KeyboardButtonStyle", None)

    # Purana Telethon → plain fallback
    if KB_Inline is None or IB_Callback is None:
        if callback_data is not None:
            data = callback_data.encode() if isinstance(callback_data, str) else callback_data
            return Button.inline(text, data)
        if url is not None:
            return Button.url(text, url)
        return Button.inline(text, b"noop")

    # Style object
    style_obj = None
    if KB_Style is not None:
        try:
            if style == "success":
                style_obj = KB_Style(bg_success=True)
            elif style == "danger":
                style_obj = KB_Style(bg_danger=True)
            elif style == "primary":
                style_obj = KB_Style(bg_primary=True)
        except Exception:
            style_obj = None

    # Inner button build with multiple param fallbacks
    def _try_build(inner):
        for pname in ("type", "button", "data", "callback", "value", "inner"):
            try:
                kwargs = {pname: inner}
                if style_obj is not None:
                    kwargs["style"] = style_obj
                return KB_Inline(text=text, **kwargs)
            except TypeError:
                continue
        return None

    if callback_data is not None:
        data = callback_data.encode() if isinstance(callback_data, str) else callback_data
        try:
            inner = IB_Callback(data=data)
        except TypeError:
            inner = IB_Callback(data)
        res = _try_build(inner)
        if res is not None:
            return res
        return Button.inline(text, data)

    if url is not None and IB_Url is not None:
        try:
            inner = IB_Url(url=url)
        except TypeError:
            inner = IB_Url(url)
        res = _try_build(inner)
        if res is not None:
            return res
        return Button.url(text, url)

    return Button.inline(text, b"noop")
    
    
# ======================= JSON STORAGE =======================
def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"sessions": {}, "approved_users": [], "history": []}


def save_data(data: dict) -> None:
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ======================= SESSIONS =======================
async def get_all_sessions() -> dict:
    return load_data().get("sessions", {})


async def add_session(phone: str, session_str: str) -> None:
    data = load_data()
    data["sessions"][phone] = session_str
    save_data(data)


async def remove_session(phone: str) -> bool:
    data = load_data()
    if phone in data["sessions"]:
        del data["sessions"][phone]
        save_data(data)
        return True
    return False


# ======================= APPROVALS =======================
async def approve_user(user_id: int, approved_by: int, username: str = "", title: str = "") -> bool:
    try:
        data = load_data()
        data["approved_users"] = [u for u in data["approved_users"] if u["user_id"] != user_id]
        data["approved_users"].append({
            "user_id": user_id,
            "username": username,
            "title": title,
            "approved_by": approved_by,
            "approved_at": datetime.datetime.utcnow().isoformat(),
            "is_approved": True,
        })
        save_data(data)
        return True
    except Exception as e:
        logger.error(f"Approve error: {e}")
        return False


async def unapprove_user(user_id: int) -> bool:
    try:
        data = load_data()
        original = len(data["approved_users"])
        data["approved_users"] = [u for u in data["approved_users"] if u["user_id"] != user_id]
        if len(data["approved_users"]) < original:
            save_data(data)
            return True
        return False
    except Exception as e:
        logger.error(f"Unapprove error: {e}")
        return False


async def is_user_approved(user_id: int) -> bool:
    if user_id in OWNER_IDS:
        return True
    for u in load_data()["approved_users"]:
        if u["user_id"] == user_id and u.get("is_approved", True):
            return True
    return False


async def get_approved_users() -> List[dict]:
    return [u for u in load_data()["approved_users"] if u.get("is_approved", True)]


# ======================= HISTORY =======================
async def log_activity(phone: str, action: str, target: str, status: str) -> None:
    try:
        data = load_data()
        data["history"].append({
            "phone": phone,
            "action": action,
            "target": target,
            "status": status,
            "timestamp": datetime.datetime.utcnow().isoformat(),
        })
        if len(data["history"]) > 1000:
            data["history"] = data["history"][-1000:]
        save_data(data)
    except Exception as e:
        logger.error(f"Log activity error: {e}")


async def get_today_joins() -> int:
    today = datetime.datetime.now().date().isoformat()
    return sum(
        1 for log in load_data()["history"]
        if log["action"] == "JOIN" and log["timestamp"].startswith(today)
    )


def get_uptime() -> str:
    diff = datetime.datetime.now() - START_TIME
    minutes = int(diff.total_seconds() / 60)
    if minutes < 60:
        return f"{minutes}ᴍ"
    if minutes < 1440:
        return f"{minutes // 60}ʜ {minutes % 60}ᴍ"
    return f"{minutes // 1440}ᴅ {minutes % 1440 // 60}ʜ"


# ======================= STATUS ENGINE =======================
async def set_account_status(client: TelegramClient, phone: str, is_online: bool) -> None:
    try:
        if is_online:
            await client(SetPrivacyRequest(
                key=InputPrivacyKeyStatusTimestamp(),
                rules=[InputPrivacyValueAllowAll()],
            ))
            await client(UpdateStatusRequest(offline=False))
        else:
            await client(SetPrivacyRequest(
                key=InputPrivacyKeyStatusTimestamp(),
                rules=[InputPrivacyValueDisallowAll()],
            ))
            await client(UpdateStatusRequest(offline=True))
    except Exception as e:
        logger.error(f"Status update error for {phone}: {e}")


def trigger_temp_online(phone: str, duration_seconds: int = 90) -> None:
    random_duration = duration_seconds + random.randint(0, 30)
    temp_online_until[phone] = time.time() + random_duration
    logger.info(f"⚡ {phone} mixed status set to ONLINE for {random_duration}s")


async def keep_alive(client: TelegramClient, phone: str) -> None:
    last_applied_status = None
    while not SHUTDOWN:
        try:
            now = time.time()
            should_be_online = False
            if phone in temp_online_until and now < temp_online_until[phone]:
                should_be_online = True
            else:
                if next_random_toggle.get(phone, 0) < now:
                    if random.random() < 0.35:
                        should_be_online = True
                        temp_online_until[phone] = now + random.randint(60, 120)
                    next_random_toggle[phone] = now + random.randint(300, 900)
            if last_applied_status != should_be_online:
                await set_account_status(client, phone, is_online=should_be_online)
                last_applied_status = should_be_online
            else:
                await client(UpdateStatusRequest(offline=not should_be_online))
            await client.get_me()
        except Exception as e:
            logger.warning(f"Keep-alive warning for {phone}: {e}")
        await asyncio.sleep(KEEP_ALIVE_INTERVAL)


async def start_saved_clients() -> None:
    sessions = await get_all_sessions()
    logger.info(f"Loading {len(sessions)} sessions...")
    loaded = []
    for phone, session_str in sessions.items():
        try:
            client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
            await client.connect()
            if await client.is_user_authorized():
                active_clients[phone] = client
                loaded.append(phone)
                logger.info(f"Connected: {phone}")
        except Exception as e:
            logger.error(f"Failed to connect {phone}: {e}")
    for phone in loaded:
        account_modes[phone] = "SMART_MIXED"
        next_random_toggle[phone] = time.time() + random.randint(10, 300)
        asyncio.create_task(keep_alive(active_clients[phone], phone))
    logger.info(f"Loaded {len(loaded)} accounts with SMART MIXED Status Engine")


# ======================= HELPERS =======================
def parse_target(link: str):
    link = link.strip()
    m = re.search(r"(?:t\.me/|@)([a-zA-Z0-9_]{5,32})", link)
    if m and "joinchat" not in link and "/c/" not in link:
        return ("public", m.group(1))
    m = re.search(r"t\.me/(?:\+|joinchat/)([a-zA-Z0-9_-]+)", link)
    if m:
        return ("private", m.group(1))
    return (None, None)


def parse_vc_link(link: str):
    link = link.strip().replace(" ", "")
    for prefix in ["https://", "http://", "www.", "t.me/", "telegram.me/"]:
        link = link.replace(prefix, "")
    if "?" in link:
        link = link.split("?")[0]
    if "joinchat/" in link:
        return True, link.split("joinchat/")[1].replace("/", "")
    if link.startswith("+"):
        return True, link[1:].replace("/", "")
    return False, link.replace("@", "").replace("/", "")


async def resolve_user(bot_client, input_text: str):
    input_text = input_text.strip()
    try:
        entity = (
            await bot_client.get_entity(int(input_text))
            if input_text.isdigit()
            else await bot_client.get_entity(input_text.replace("@", ""))
        )
        user_id = entity.id
        username_str = f"@{entity.username}" if getattr(entity, "username", None) else ""
        first = getattr(entity, "first_name", "") or ""
        last = getattr(entity, "last_name", "") or ""
        full = f"{first} {last}".strip() or "User"
        display = f"{full} ({username_str})" if username_str else full
        return user_id, username_str, display
    except Exception as e:
        logger.error(f"Error resolving user {input_text}: {e}")
        if input_text.isdigit():
            return int(input_text), "", f"ID: {input_text}"
        return None, None, None


async def is_client_admin(client: TelegramClient, entity) -> bool:
    try:
        perms = await client.get_permissions(entity)
        if perms and (perms.is_admin or perms.is_creator):
            return True
    except Exception:
        try:
            participant = await client.get_permissions(entity, "me")
            if isinstance(participant, (ChannelParticipantAdmin, ChannelParticipantCreator)):
                return True
        except Exception:
            pass
    return False


async def send_reaction_normal(client, peer, msg_id, emoji):
    try:
        await client(SendReactionRequest(
            peer=peer, msg_id=msg_id, reaction=[ReactionEmoji(emoticon=emoji)]
        ))
        return True, None
    except Exception as e:
        return False, str(e)


async def view_post(client, peer, msg_id):
    try:
        await client(GetMessagesViewsRequest(peer=peer, id=[msg_id], increment=True))
        return True, None
    except Exception as e:
        return False, str(e)


async def send_multiple_reactions(client, peer, msg_id, emojis):
    results = []
    for emoji in emojis:
        ok, err = await send_reaction_normal(client, peer, msg_id, emoji)
        results.append((emoji, ok, err))
        await asyncio.sleep(0.1 + random.uniform(0.05, 0.15))
    return results


async def resolve_post_peer(link, link_type, target):
    try:
        clean = link.replace("https://", "").replace("http://", "").strip()
        if "t.me/c/" in clean:
            parts = clean.split("t.me/c/")[1].split("/")
            entity = int(parts[0])
            msg_id = int(parts[1].split("?")[0])
            return PeerChannel(abs(entity)), msg_id
        parts = clean.split("t.me/")[1].split("/")
        return parts[0], int(parts[1].split("?")[0])
    except Exception:
        return None, None


def get_stop_button():
    return [[_btn("⛔ ꜰᴏʀᴄᴇ ꜱᴛᴏᴘ", "force_stop", style="danger")]]


def get_cancel():
    return [[_btn("🔙 ʙᴀᴄᴋ", "back", style="danger")]]


def get_qty_buttons():
    return [
        [
            _btn("10",  "qty_10",  style="primary"),
            _btn("50",  "qty_50",  style="primary"),
        ],
        [
            _btn("100", "qty_100", style="primary"),
            _btn("ᴀʟʟ", "qty_all", style="success"),
        ],
        [_btn("ᴄᴜꜱᴛᴏᴍ", "qty_custom", style="primary")],
        [_btn("🔙 ʙᴀᴄᴋ", "back", style="danger")],
    ]


def get_views_qty_buttons():
    return [
        [
            _btn("10",  "views_qty_10",  style="primary"),
            _btn("50",  "views_qty_50",  style="primary"),
        ],
        [
            _btn("100", "views_qty_100", style="primary"),
            _btn("ᴀʟʟ", "views_qty_all", style="success"),
        ],
        [_btn("ᴄᴜꜱᴛᴏᴍ", "views_qty_custom", style="primary")],
        [_btn("🔙 ʙᴀᴄᴋ", "back", style="danger")],
    ]


def get_vc_qty_buttons():
    return [
        [
            _btn("10",  "vc_qty_10",  style="primary"),
            _btn("50",  "vc_qty_50",  style="primary"),
        ],
        [
            _btn("100", "vc_qty_100", style="primary"),
            _btn("ᴀʟʟ", "vc_qty_all", style="success"),
        ],
        [_btn("ᴄᴜꜱᴛᴏᴍ", "vc_qty_custom", style="primary")],
        [_btn("🔙 ʙᴀᴄᴋ", "back", style="danger")],
    ]


async def interruptible_sleep(seconds: float, chat_id: int) -> bool:
    step = 0.2
    elapsed = 0.0
    while elapsed < seconds:
        if task_states.get(chat_id, {}).get("cancel"):
            return True
        await asyncio.sleep(step)
        elapsed += step
    return False


def progress_bar(percent: int) -> str:
    filled = percent // 10
    return "█" * filled + "░" * (10 - filled)


# ======================= VC ENGINE =======================
async def monitor_vc(client, entity, phone, identifier):
    while phone in VC_MONITORS and not SHUTDOWN:
        try:
            full_chat = await client(GetFullChannelRequest(entity))
            call_obj = full_chat.full_chat.call
            if not call_obj:
                logger.info(f"[{phone}] VC ended, stopping monitor")
                VC_MONITORS.pop(phone, None)
                break
            try:
                my_ssrc = random.randint(10000, 99999999)
                params = DataJSON(data=json.dumps({"min_version": 2, "ssrc": my_ssrc, "muted": True}))
                await client(JoinGroupCallRequest(
                    call=call_obj,
                    join_as=await client.get_input_entity("me"),
                    params=params,
                    muted=True,
                ))
            except UserAlreadyParticipantError:
                pass
            except Exception as e:
                logger.error(f"[{phone}] VC monitor join error: {e}")
        except Exception as e:
            logger.error(f"[{phone}] VC monitor error: {e}")
        await asyncio.sleep(15)


async def _worker_vc_join_persistent(client, phone, identifier, is_private):
    try:
        trigger_temp_online(phone, duration_seconds=200)
        entity = None
        if is_private:
            try:
                updates = await client(ImportChatInviteRequest(identifier))
                if updates.chats:
                    entity = updates.chats[0]
            except UserAlreadyParticipantError:
                try:
                    invite_info = await client(CheckChatInviteRequest(identifier))
                    if hasattr(invite_info, "chat"):
                        entity = invite_info.chat
                    elif hasattr(invite_info, "title"):
                        target_title = invite_info.title
                        # ⚡ limit 200 kiya (pehle None tha) — speed ke liye
                        async for dialog in client.iter_dialogs(limit=200):
                            if dialog.title == target_title:
                                entity = dialog.entity
                                break
                except Exception as e:
                    return 0, f"Already in but resolve failed: {str(e)[:50]}"
            except InviteRequestSentError:
                return 0, "Join request sent to admin (pending approval)"
            except FloodWaitError as e:
                return 0, f"Flood wait {e.seconds}s on channel join"
            except Exception as e:
                return 0, f"Failed to join private channel: {str(e)[:50]}"
        else:
            try:
                await client(JoinChannelRequest(identifier))
            except UserAlreadyParticipantError:
                pass
            except Exception as e:
                return 0, f"Failed to join public channel: {str(e)[:50]}"
            try:
                entity = await client.get_entity(identifier)
            except Exception as e:
                return 0, f"Failed to resolve public entity: {str(e)[:50]}"
        if not entity:
            return 0, "Could not obtain channel/group entity"
        full_chat = await client(GetFullChannelRequest(entity))
        if not full_chat.full_chat.call:
            return 0, "No active voice chat in this channel"
        call_obj = full_chat.full_chat.call
        my_ssrc = random.randint(10000, 99999999)
        params = DataJSON(data=json.dumps({"min_version": 2, "ssrc": my_ssrc, "muted": True}))
        await client(JoinGroupCallRequest(
            call=call_obj,
            join_as=await client.get_input_entity("me"),
            params=params,
            muted=True,
        ))
        # ⚡ TRACK: is phone ne is chat ke VC mein join kiya (leave fast karne ke liye)
        try:
            cid = getattr(entity, "id", None)
            if cid:
                VC_JOINED.setdefault(phone, set()).add(cid)
        except Exception:
            pass

        if phone in VC_MONITORS:
            VC_MONITORS[phone].cancel()
        VC_MONITORS[phone] = asyncio.create_task(monitor_vc(client, entity, phone, identifier))
        return 1, "Joined VC with persistent monitor"
    except Exception as e:
        return 0, f"Error: {str(e)[:50]}"


async def execute_vc_join(event, raw_link: str, count_limit: int) -> None:
    chat_id = event.chat_id
    task_states[chat_id] = {"running": True, "cancel": False}
    is_private, identifier = parse_vc_link(raw_link)
    if not identifier:
        task_states.pop(chat_id, None)
        return await event.respond("❌ ɪɴᴠᴀʟɪᴅ ᴠᴏɪᴄᴇ ᴄʜᴀᴛ ʟɪɴᴋ !")

    all_phones = list(active_clients.keys())
    random.shuffle(all_phones)
    selected = all_phones[:count_limit] if count_limit > 0 else all_phones
    total = len(selected)

    msg = await event.respond(
        f"🎙️ ᴊᴏɪɴɪɴɢ ᴠᴄ ᴡɪᴛʜ {total} ᴀᴄᴄᴏᴜɴᴛꜱ (ᴘᴀʀᴀʟʟᴇʟ) ...",
        buttons=get_stop_button(),
    )

    success = fail = done = 0
    lock = asyncio.Lock()
    sem = asyncio.Semaphore(25)   # ⚡ ek saath 25 accounts

    async def join_one(phone):
        nonlocal success, fail, done
        if task_states.get(chat_id, {}).get("cancel"):
            return
        async with sem:
            if task_states.get(chat_id, {}).get("cancel"):
                return
            client = active_clients.get(phone)
            if not client:
                async with lock:
                    fail += 1
                    done += 1
                return
            status, reason = await _worker_vc_join_persistent(
                client, phone, identifier, is_private
            )
            await log_activity(
                phone, "VC_JOIN", raw_link,
                "Success" if status == 1 else reason
            )
            async with lock:
                if status == 1:
                    success += 1
                else:
                    fail += 1
                done += 1
                snap_done = done
                snap_s, snap_f = success, fail
            if snap_done % 5 == 0 or snap_done == total:
                percent = int((snap_done / total) * 100) if total else 0
                try:
                    await msg.edit(
                        f"🎙️ ᴠᴄ ᴊᴏɪɴ ᴘʀᴏɢʀᴇꜱꜱ\n"
                        f"[{progress_bar(percent)}] {percent}%\n"
                        f"👤 ᴘʀᴏɢʀᴇꜱꜱ: {snap_done}/{total}\n"
                        f"✅ ꜱᴜᴄᴄᴇꜱꜱ: {snap_s}  |  ❌ ꜰᴀɪʟᴇᴅ: {snap_f}",
                        buttons=get_stop_button(),
                    )
                except Exception:
                    pass

    await asyncio.gather(*[join_one(p) for p in selected], return_exceptions=True)

    cancelled = task_states.get(chat_id, {}).get("cancel", False)
    task_states.pop(chat_id, None)
    title = "⛔ ᴠᴄ ᴊᴏɪɴ ꜰᴏʀᴄᴇ ꜱᴛᴏᴘᴘᴇᴅ !" if cancelled else "✅ ᴠᴄ ᴊᴏɪɴ ᴛᴀꜱᴋ ᴄᴏᴍᴘʟᴇᴛᴇ !"
    final = (
        f"{title}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 ᴠᴄ ᴊᴏɪɴ ʀᴇꜱᴜʟᴛꜱ\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ ᴊᴏɪɴᴇᴅ ᴠᴄ: {success}\n"
        f"❌ ꜰᴀɪʟᴇᴅ: {fail}\n"
        f"👥 ᴛᴏᴛᴀʟ ᴀᴄᴄᴏᴜɴᴛꜱ: {total}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )
    await msg.edit(final, buttons=None)
    await asyncio.sleep(1)
    await show_menu(event, event.sender_id)


async def execute_vc_leave(event) -> None:
    chat_id = event.chat_id
    task_states[chat_id] = {"running": True, "cancel": False}
    msg = await event.respond(
        "🎙️ ʟᴇᴀᴠɪɴɢ ᴀʟʟ ᴠᴏɪᴄᴇ ᴄʜᴀᴛꜱ (ᴘᴀʀᴀʟʟᴇʟ) ...",
        buttons=get_stop_button(),
    )

    # ⚡ Saare monitors parallel cancel
    monitor_items = list(VC_MONITORS.items())
    VC_MONITORS.clear()
    for _, task in monitor_items:
        task.cancel()
    if monitor_items:
        try:
            await asyncio.gather(*[t for _, t in monitor_items], return_exceptions=True)
        except Exception:
            pass

    left_count = done = 0
    total = len(active_clients)
    lock = asyncio.Lock()
    sem = asyncio.Semaphore(25)

    async def leave_one(phone, client):
        nonlocal left_count, done
        if task_states.get(chat_id, {}).get("cancel"):
            return
        async with sem:
            if task_states.get(chat_id, {}).get("cancel"):
                return
            try:
                # ⚡ Tracking set — sirf wahi chats check karo jaha VC join kiya tha
                joined_ids = VC_JOINED.get(phone, set())
                dialogs = await client.get_dialogs(limit=200)
                for dialog in dialogs:
                    if task_states.get(chat_id, {}).get("cancel"):
                        return
                    if not (dialog.is_channel or dialog.is_group):
                        continue
                    # ⚡ Agar joined set hai aur ye chat usme nahi hai to skip
                    if joined_ids and dialog.entity.id not in joined_ids:
                        continue
                    try:
                        full_chat = await client(GetFullChannelRequest(dialog.entity))
                        if full_chat.full_chat.call:
                            await client(LeaveGroupCallRequest(full_chat.full_chat.call))
                            async with lock:
                                left_count += 1
                            joined_ids.discard(dialog.entity.id)
                    except Exception:
                        continue
            except Exception as e:
                logger.warning(f"[{phone}] VC leave error: {e}")
            async with lock:
                done += 1
                snap_done, snap_left = done, left_count
            if snap_done % 3 == 0 or snap_done == total:
                try:
                    await msg.edit(
                        f"🎙️ ʟᴇᴀᴠɪɴɢ ᴠᴄ ...\n"
                        f"👤 ᴘʀᴏɢʀᴇꜱꜱ: {snap_done}/{total}\n"
                        f"✅ ʟᴇꜰᴛ ᴠᴄ ɪɴꜱᴛᴀɴᴄᴇꜱ: {snap_left}",
                        buttons=get_stop_button(),
                    )
                except Exception:
                    pass

    await asyncio.gather(
        *[leave_one(p, c) for p, c in list(active_clients.items())],
        return_exceptions=True,
    )

    cancelled = task_states.get(chat_id, {}).get("cancel", False)
    task_states.pop(chat_id, None)
    status = "⛔ ᴠᴄ ʟᴇᴀᴠᴇ ꜰᴏʀᴄᴇ ꜱᴛᴏᴘᴘᴇᴅ !" if cancelled else "✅ ʟᴇꜰᴛ ᴀʟʟ ᴠᴏɪᴄᴇ ᴄʜᴀᴛꜱ !"
    await msg.edit(f"{status}\nᴛᴏᴛᴀʟ ʟᴇꜰᴛ ᴄᴀʟʟꜱ: {left_count}", buttons=None)
    await asyncio.sleep(1)
    await show_menu(event, event.sender_id)


# ======================= AUTO VIEWS =======================
async def send_auto_views(chat_id, msg_id, channel_username=None):
    if not active_clients:
        return
    phones = list(active_clients.keys())
    random.shuffle(phones)
    success = 0
    for phone in phones:
        client = active_clients[phone]
        trigger_temp_online(phone, duration_seconds=90)
        try:
            target_peer = channel_username if channel_username else chat_id
            try:
                await client(GetMessagesViewsRequest(peer=target_peer, id=[msg_id], increment=True))
                success += 1
            except Exception:
                await client.get_messages(target_peer, ids=msg_id)
                success += 1
            await log_activity(phone, "AUTO_VIEW", f"msg_id:{msg_id}", "Success")
        except Exception as e:
            if channel_username and ("not found" in str(e).lower() or "channel" in str(e).lower()):
                try:
                    await client(JoinChannelRequest(channel_username))
                    await asyncio.sleep(1)
                    await client.get_messages(channel_username, ids=msg_id)
                    success += 1
                    await log_activity(phone, "AUTO_VIEW", f"msg_id:{msg_id}", "Success after join")
                except Exception:
                    await log_activity(phone, "AUTO_VIEW", f"msg_id:{msg_id}", "Failed join")
            else:
                await log_activity(phone, "AUTO_VIEW", f"msg_id:{msg_id}", str(e)[:30])
        await asyncio.sleep(0.3 + random.uniform(0.1, 0.4))


# ======================= EXECUTORS =======================
async def execute_join(event, task_data, count_limit):
    chat_id = event.chat_id
    task_states[chat_id] = {"running": True, "cancel": False}
    links_data = task_data["links_data"]
    delay = task_data["delay"]

    all_phones = list(active_clients.keys())
    random.shuffle(all_phones)
    selected = all_phones[:count_limit]

    msg = await event.respond(
        f"🚀 ᴊᴏɪɴɪɴɢ {len(links_data)} ʟɪɴᴋꜱ ᴡɪᴛʜ {len(selected)} ᴀᴄᴄᴏᴜɴᴛꜱ ...",
        buttons=get_stop_button(),
    )
    success = failed = already = 0
    cancelled = False
    total_ops = len(links_data) * len(selected)
    current_op = 0
    link_idx = 0

    for link_idx, link_info in enumerate(links_data, 1):
        if task_states.get(chat_id, {}).get("cancel"):
            cancelled = True
            break
        link_type, target, original_link = link_info["type"], link_info["target"], link_info["link"]

        for acc_idx, phone in enumerate(selected):
            if task_states.get(chat_id, {}).get("cancel"):
                cancelled = True
                break
            trigger_temp_online(phone, duration_seconds=90)
            current_op += 1
            client = active_clients.get(phone)
            if not client:
                failed += 1
                continue
            try:
                if link_type == "public":
                    await client(JoinChannelRequest(target))
                else:
                    await client(ImportChatInviteRequest(target))
                success += 1
                await log_activity(phone, "JOIN", original_link, "Success")
            except UserAlreadyParticipantError:
                already += 1
                success += 1
                await log_activity(phone, "JOIN", original_link, "Already member")
            except InviteRequestSentError:
                success += 1
                await log_activity(phone, "JOIN", original_link, "Request sent")
            except FloodWaitError as e:
                wait_time = e.seconds if e.seconds < 30 else 30
                await log_activity(phone, "JOIN", original_link, f"Flood wait {wait_time}s")
                if await interruptible_sleep(wait_time, chat_id):
                    cancelled = True
                    break
                try:
                    if link_type == "public":
                        await client(JoinChannelRequest(target))
                    else:
                        await client(ImportChatInviteRequest(target))
                    success += 1
                    await log_activity(phone, "JOIN", original_link, "Success after retry")
                except Exception:
                    failed += 1
            except Exception as e:
                failed += 1
                await log_activity(phone, "JOIN", original_link, str(e)[:30])

            percent = int((current_op / total_ops) * 100) if total_ops else 0
            text = (
                f"⟪ ᴊᴏɪɴ ᴘʀᴏɢʀᴇꜱꜱ ⟫\n"
                f"[{progress_bar(percent)}] {percent}%\n\n"
                f"📌 ʟɪɴᴋ {link_idx}/{len(links_data)}: {original_link[:35]}\n"
                f"👤 ᴀᴄᴄᴏᴜɴᴛ {acc_idx + 1}/{len(selected)}: {phone}\n\n"
                f"📊 ꜱᴛᴀᴛꜱ\n"
                f"✅ ꜱᴜᴄᴄᴇꜱꜱ: {success}\n"
                f"⚠️ ᴀʟʀᴇᴀᴅʏ: {already}\n"
                f"❌ ꜰᴀɪʟᴇᴅ: {failed}"
            )
            try:
                await msg.edit(text, buttons=get_stop_button())
            except Exception:
                pass
            if await interruptible_sleep(delay + random.uniform(0.1, 0.5), chat_id):
                cancelled = True
                break

    title = "⛔ ᴊᴏɪɴ ᴛᴀꜱᴋ ꜰᴏʀᴄᴇ ꜱᴛᴏᴘᴘᴇᴅ !" if cancelled else "✅ ᴊᴏɪɴ ᴛᴀꜱᴋ ᴄᴏᴍᴘʟᴇᴛᴇ !"
    rate = int((success / (success + failed)) * 100) if (success + failed) else 0
    final = (
        f"{title}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 ꜰɪɴᴀʟ ꜱᴛᴀᴛɪꜱᴛɪᴄꜱ\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ ᴊᴏɪɴᴇᴅ: {success - already}\n"
        f"⚠️ ᴀʟʀᴇᴀᴅʏ: {already}\n"
        f"❌ ꜰᴀɪʟᴇᴅ: {failed}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 ʟɪɴᴋꜱ ᴘʀᴏᴄᴇꜱꜱᴇᴅ: {link_idx if cancelled else len(links_data)}\n"
        f"👥 ᴀᴄᴄᴏᴜɴᴛꜱ ᴜꜱᴇᴅ: {len(selected)}\n"
        f"📈 ʀᴀᴛᴇ: {rate}%\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )
    task_states.pop(chat_id, None)
    await msg.edit(final, buttons=None)
    await asyncio.sleep(1)
    await show_menu(event, event.sender_id)


async def execute_leave_specific(event, link):
    chat_id = event.chat_id
    task_states[chat_id] = {"running": True, "cancel": False}
    link_type, target = parse_target(link)
    if not link_type and "t.me/" not in link:
        return await event.respond(
            "ɪɴᴠᴀʟɪᴅ ʟɪɴᴋ !",
            buttons=[[_btn("🔙 ʙᴀᴄᴋ", "back", style="danger")]],
        )

    msg = await event.respond(f"📤 ʟᴇᴀᴠɪɴɢ: {link}", buttons=get_stop_button())
    count, skipped_admin, cancelled = 0, 0, False

    for phone, client in list(active_clients.items()):
        if task_states.get(chat_id, {}).get("cancel"):
            cancelled = True
            break
        trigger_temp_online(phone, duration_seconds=90)
        try:
            entity = await (client.get_entity(target) if link_type == "public" else client.get_entity(link))
            if entity:
                if await is_client_admin(client, entity):
                    skipped_admin += 1
                    logger.info(f"Skipping leave for {phone} in {link} because account is Admin")
                    continue
                if link_type == "public":
                    await client(LeaveChannelRequest(target))
                else:
                    await client.delete_dialog(entity)
                count += 1
                try:
                    await msg.edit(
                        f"📤 ʟᴇᴀᴠɪɴɢ: {link}\n✅ ʟᴇꜰᴛ: {count}\n🛡️ ᴘʀᴏᴛᴇᴄᴛᴇᴅ ᴀᴅᴍɪɴꜱ: {skipped_admin}",
                        buttons=get_stop_button(),
                    )
                except Exception:
                    pass
        except Exception:
            pass
        if await interruptible_sleep(1.0 + random.uniform(0.1, 0.3), chat_id):
            cancelled = True
            break

    task_states.pop(chat_id, None)
    status = "⛔ ʟᴇᴀᴠᴇ ꜰᴏʀᴄᴇ ꜱᴛᴏᴘᴘᴇᴅ" if cancelled else "✅ ʟᴇꜰᴛ ꜰʀᴏᴍ"
    await msg.edit(f"{status} {count} ᴀᴄᴄᴏᴜɴᴛꜱ\n🛡️ ᴘʀᴏᴛᴇᴄᴛᴇᴅ ᴀᴅᴍɪɴꜱ: {skipped_admin}", buttons=None)
    await asyncio.sleep(1)
    await show_menu(event, event.sender_id)

async def execute_leave_all(event):
    chat_id = event.chat_id
    task_states[chat_id] = {"running": True, "cancel": False}
    msg = await event.respond("⚠️ ʟᴇᴀᴠɪɴɢ ᴀʟʟ ᴄʜᴀɴɴᴇʟꜱ ...", buttons=get_stop_button())
    total, skipped_admin, cancelled = 0, 0, False

    for phone, client in list(active_clients.items()):
        if task_states.get(chat_id, {}).get("cancel"):
            cancelled = True
            break
        trigger_temp_online(phone, duration_seconds=90)
        try:
            async for dialog in client.iter_dialogs():
                if task_states.get(chat_id, {}).get("cancel"):
                    cancelled = True
                    break
                if dialog.is_channel or dialog.is_group:
                    try:
                        if await is_client_admin(client, dialog.entity):
                            skipped_admin += 1
                            continue
                        await client.delete_dialog(dialog.entity)
                        total += 1
                        try:
                            await msg.edit(
                                f"⚠️ ʟᴇᴀᴠɪɴɢ ...\n✅ ʟᴇꜰᴛ: {total}\n🛡️ ᴘʀᴏᴛᴇᴄᴛᴇᴅ ᴀᴅᴍɪɴꜱ: {skipped_admin}",
                                buttons=get_stop_button(),
                            )
                        except Exception:
                            pass
                        if await interruptible_sleep(0.5 + random.uniform(0.1, 0.3), chat_id):
                            cancelled = True
                            break
                    except Exception:
                        pass
        except Exception:
            pass

    task_states.pop(chat_id, None)
    status = "⛔ ʟᴇᴀᴠᴇ ᴀʟʟ ꜰᴏʀᴄᴇ ꜱᴛᴏᴘᴘᴇᴅ" if cancelled else "✅ ʟᴇꜰᴛ ꜰʀᴏᴍ"
    await msg.edit(f"{status} {total} ᴄʜᴀᴛꜱ\n🛡️ ᴘʀᴏᴛᴇᴄᴛᴇᴅ ᴀᴅᴍɪɴꜱ: {skipped_admin}", buttons=None)
    await asyncio.sleep(1)
    await show_menu(event, event.sender_id)


async def execute_react_view(event, task_data, count_limit):
    chat_id = event.chat_id
    task_states[chat_id] = {"running": True, "cancel": False}
    links_data, emoji, speed = task_data["links_data"], task_data["emoji"], task_data["speed"]
    accounts = list(active_clients.keys())[:count_limit]
    random.shuffle(accounts)

    msg = await event.respond(
        f"⚡ ʀᴇᴀᴄᴛ & ᴠɪᴇᴡ ᴏɴ {len(links_data)} ᴘᴏꜱᴛꜱ ᴡɪᴛʜ {len(accounts)} ᴀᴄᴄᴏᴜɴᴛꜱ ...",
        buttons=get_stop_button(),
    )
    total_ops = len(links_data) * len(accounts)
    current = success = failed = react_ok = view_ok = 0
    cancelled = False

    for link_info in links_data:
        if task_states.get(chat_id, {}).get("cancel"):
            cancelled = True
            break
        peer, msg_id = await resolve_post_peer(link_info["link"], link_info["type"], link_info["target"])
        if not peer:
            failed += len(accounts)
            continue
        for phone in accounts:
            if task_states.get(chat_id, {}).get("cancel"):
                cancelled = True
                break
            trigger_temp_online(phone, duration_seconds=90)
            current += 1
            client = active_clients.get(phone)
            if not client:
                failed += 1
                continue
            r_ok, _ = await send_reaction_normal(client, peer, msg_id, emoji)
            if r_ok:
                react_ok += 1
            v_ok, _ = await view_post(client, peer, msg_id)
            if v_ok:
                view_ok += 1
            if r_ok and v_ok:
                success += 1
            else:
                failed += 1

            percent = int((current / total_ops) * 100) if total_ops else 0
            status = (
                f"⟪ ʀᴇᴀᴄᴛ & ᴠɪᴇᴡ ⟫\n"
                f"[{progress_bar(percent)}] {percent}%\n"
                f"✅ ꜱᴜᴄᴄᴇꜱꜱ: {success}  ❌ ꜰᴀɪʟᴇᴅ: {failed}\n"
                f"👍 ʀᴇᴀᴄᴛɪᴏɴꜱ: {react_ok}  👁 ᴠɪᴇᴡꜱ: {view_ok}"
            )
            try:
                await msg.edit(status, buttons=get_stop_button())
            except Exception:
                pass
            if await interruptible_sleep(speed + random.uniform(0.05, 0.2), chat_id):
                cancelled = True
                break

    task_states.pop(chat_id, None)
    title = "⛔ ʀᴇᴀᴄᴛ & ᴠɪᴇᴡ ꜰᴏʀᴄᴇ ꜱᴛᴏᴘᴘᴇᴅ !" if cancelled else "✅ ʀᴇᴀᴄᴛ & ᴠɪᴇᴡ ᴄᴏᴍᴘʟᴇᴛᴇ !"
    final = (
        f"{title}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ ʙᴏᴛʜ ᴀᴄᴛɪᴏɴꜱ: {success}\n"
        f"❌ ꜰᴀɪʟᴇᴅ: {failed}\n"
        f"👍 ʀᴇᴀᴄᴛɪᴏɴꜱ ꜱᴇɴᴛ: {react_ok}\n"
        f"👁 ᴠɪᴇᴡꜱ ɪɴᴄʀᴇᴍᴇɴᴛᴇᴅ: {view_ok}"
    )
    await msg.edit(final, buttons=None)
    await asyncio.sleep(1)
    await show_menu(event, event.sender_id)


async def execute_multi_react_view(event, task_data, count_limit):
    chat_id = event.chat_id
    task_states[chat_id] = {"running": True, "cancel": False}
    links_data, emojis, speed = task_data["links_data"], task_data["emojis"], task_data["speed"]
    accounts = list(active_clients.keys())[:count_limit]
    random.shuffle(accounts)

    msg = await event.respond(
        f"🔄 ᴍᴜʟᴛɪ‑ʀᴇᴀᴄᴛ & ᴠɪᴇᴡ ᴏɴ {len(links_data)} ᴘᴏꜱᴛꜱ ᴡɪᴛʜ {len(accounts)} ᴀᴄᴄᴏᴜɴᴛꜱ ...",
        buttons=get_stop_button(),
    )
    total_ops = len(links_data) * len(accounts)
    current = success = failed = reactions_sent = 0
    cancelled = False

    for link_info in links_data:
        if task_states.get(chat_id, {}).get("cancel"):
            cancelled = True
            break
        peer, msg_id = await resolve_post_peer(link_info["link"], link_info["type"], link_info["target"])
        if not peer:
            failed += len(accounts)
            continue
        for phone in accounts:
            if task_states.get(chat_id, {}).get("cancel"):
                cancelled = True
                break
            trigger_temp_online(phone, duration_seconds=90)
            current += 1
            client = active_clients.get(phone)
            if not client:
                failed += 1
                continue
            res = await send_multiple_reactions(client, peer, msg_id, emojis)
            reactions_sent += len([r for r in res if r[1]])
            v_ok, _ = await view_post(client, peer, msg_id)
            success += 1 if v_ok else 0
            failed += 0 if v_ok else 1

            percent = int((current / total_ops) * 100) if total_ops else 0
            status = (
                f"⟪ ᴍᴜʟᴛɪ‑ʀᴇᴀᴄᴛ ⟫\n"
                f"[{progress_bar(percent)}] {percent}%\n"
                f"✅ ᴠɪᴇᴡᴇᴅ: {success}  ❌ ꜰᴀɪʟᴇᴅ: {failed}\n"
                f"🔥 ʀᴇᴀᴄᴛɪᴏɴꜱ ꜱᴇɴᴛ: {reactions_sent}"
            )
            try:
                await msg.edit(status, buttons=get_stop_button())
            except Exception:
                pass
            if await interruptible_sleep(speed + random.uniform(0.05, 0.2), chat_id):
                cancelled = True
                break

    task_states.pop(chat_id, None)
    title = "⛔ ᴍᴜʟᴛɪ‑ʀᴇᴀᴄᴛ ꜰᴏʀᴄᴇ ꜱᴛᴏᴘᴘᴇᴅ !" if cancelled else "✅ ᴍᴜʟᴛɪ‑ʀᴇᴀᴄᴛ & ᴠɪᴇᴡ ᴄᴏᴍᴘʟᴇᴛᴇ !"
    final = (
        f"{title}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ ᴠɪᴇᴡꜱ: {success}\n"
        f"❌ ꜰᴀɪʟᴇᴅ: {failed}\n"
        f"🔥 ʀᴇᴀᴄᴛɪᴏɴꜱ ꜱᴇɴᴛ: {reactions_sent}"
    )
    await msg.edit(final, buttons=None)
    await asyncio.sleep(1)
    await show_menu(event, event.sender_id)


async def execute_views(event, post_link, count_limit):
    chat_id_task = event.chat_id
    task_states[chat_id_task] = {"running": True, "cancel": False}

    channel_username = message_id = chat_id = None
    if m := re.search(r"t\.me/([a-zA-Z0-9_]+)/(\d+)", post_link):
        channel_username, message_id = m.group(1), int(m.group(2))
    if m := re.search(r"t\.me/c/(\d+)/(\d+)", post_link):
        chat_id = int(f"-100{m.group(1)}")
        message_id = int(m.group(2))

    if not channel_username and not chat_id:
        task_states.pop(chat_id_task, None)
        return await event.respond(
            "❌ ɪɴᴠᴀʟɪᴅ ᴘᴏꜱᴛ ʟɪɴᴋ !\n\n"
            "✅ ᴠᴀʟɪᴅ ꜰᴏʀᴍᴀᴛꜱ:\n"
            "• https://t.me/username/123\n"
            "• https://t.me/c/123456789/123"
        )

    all_phones = list(active_clients.keys())
    random.shuffle(all_phones)
    selected = all_phones[:count_limit] if count_limit > 0 else all_phones
    if not selected:
        task_states.pop(chat_id_task, None)
        return await event.respond("❌ ɴᴏ ᴀᴄᴛɪᴠᴇ ᴀᴄᴄᴏᴜɴᴛꜱ !")

    msg = await event.respond(
        f"👁️ ꜱᴇɴᴅɪɴɢ ʀᴇᴀʟ ᴠɪᴇᴡꜱ ...\n"
        f"📌 {post_link[:50]}...\n"
        f"👥 ᴀᴄᴄᴏᴜɴᴛꜱ: {len(selected)}\n"
        f"⏳ ᴘʀᴏᴄᴇꜱꜱɪɴɢ ...",
        buttons=get_stop_button(),
    )

    success = failed = not_member = 0
    cancelled = False
    total_ops = len(selected)
    current_op = 0

    for acc_idx, phone in enumerate(selected):
        if task_states.get(chat_id_task, {}).get("cancel"):
            cancelled = True
            break
        trigger_temp_online(phone, duration_seconds=90)
        current_op += 1
        client = active_clients.get(phone)
        if not client:
            failed += 1
            continue
        try:
            if chat_id:
                try:
                    message = await client.get_messages(chat_id, ids=message_id)
                    if message:
                        success += 1
                        await log_activity(phone, "VIEW", post_link, "Success (Private)")
                    else:
                        failed += 1
                        await log_activity(phone, "VIEW", post_link, "Message not found")
                except Exception as e:
                    err = str(e)
                    if "not found" in err.lower() or "invalid" in err.lower():
                        not_member += 1
                        await log_activity(phone, "VIEW", post_link, "Not member of private channel")
                    else:
                        failed += 1
                        await log_activity(phone, "VIEW", post_link, f"Error: {err[:30]}")
            else:
                try:
                    entity = await client.get_entity(channel_username)
                    message = await client.get_messages(entity, ids=message_id)
                    if message:
                        success += 1
                        await log_activity(phone, "VIEW", post_link, "Success (Public)")
                    else:
                        failed += 1
                except Exception as e:
                    err = str(e)
                    if "not found" in err.lower():
                        try:
                            await client(JoinChannelRequest(channel_username))
                            await asyncio.sleep(1)
                            entity = await client.get_entity(channel_username)
                            message = await client.get_messages(entity, ids=message_id)
                            if message:
                                success += 1
                                await log_activity(phone, "VIEW", post_link, "Success after join")
                            else:
                                failed += 1
                        except Exception:
                            failed += 1
                            await log_activity(phone, "VIEW", post_link, "Failed to join")
                    else:
                        failed += 1
                        await log_activity(phone, "VIEW", post_link, f"Error: {err[:30]}")
        except Exception as e:
            failed += 1
            await log_activity(phone, "VIEW", post_link, f"Exception: {str(e)[:30]}")

        percent = int((current_op / total_ops) * 100) if total_ops else 0
        text = (
            f"⟪ ʀᴇᴀʟ ᴠɪᴇᴡꜱ ᴘʀᴏɢʀᴇꜱꜱ ⟫\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 ᴛᴀʀɢᴇᴛ: {post_link[:35]}\n"
            f"👥 ᴘʀᴏɢʀᴇꜱꜱ: {acc_idx + 1}/{len(selected)}\n"
            f"📊 ꜱᴜᴄᴄᴇꜱꜱ: {success}  |  ❌ ꜰᴀɪʟᴇᴅ: {failed}\n"
            f"🚫 ɴᴏᴛ ᴍᴇᴍʙᴇʀ: {not_member}\n"
            f"{progress_bar(percent)} {percent}%\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⏳ ꜱᴇɴᴅɪɴɢ ᴠɪᴇᴡꜱ ..."
        )
        try:
            await msg.edit(text, buttons=get_stop_button())
        except Exception:
            pass
        if await interruptible_sleep(0.3 + random.uniform(0.05, 0.15), chat_id_task):
            cancelled = True
            break

    task_states.pop(chat_id_task, None)
    total = success + failed + not_member
    rate = int((success / total) * 100) if total else 0
    title = "⛔ ʀᴇᴀʟ ᴠɪᴇᴡꜱ ꜰᴏʀᴄᴇ ꜱᴛᴏᴘᴘᴇᴅ !" if cancelled else "✅ ʀᴇᴀʟ ᴠɪᴇᴡꜱ ᴛᴀꜱᴋ ᴄᴏᴍᴘʟᴇᴛᴇ !"
    final = (
        f"{title}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 ꜰɪɴᴀʟ ꜱᴛᴀᴛɪꜱᴛɪᴄꜱ\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👁 ʀᴇᴀʟ ᴠɪᴇᴡꜱ ꜱᴇɴᴛ: {success}\n"
        f"❌ ꜰᴀɪʟᴇᴅ: {failed}\n"
        f"🚫 ɴᴏᴛ ᴍᴇᴍʙᴇʀ: {not_member}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 ᴘᴏꜱᴛ: {post_link[:50]}\n"
        f"👥 ᴀᴄᴄᴏᴜɴᴛꜱ ᴜꜱᴇᴅ: {len(selected)}\n"
        f"📈 ꜱᴜᴄᴄᴇꜱꜱ ʀᴀᴛᴇ: {rate}%\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )
    await msg.edit(final, buttons=None)
    await asyncio.sleep(1)
    await show_menu(event, event.sender_id)


async def execute_views_batch(event, links_text, count_per_post):
    chat_id_task = event.chat_id
    task_states[chat_id_task] = {"running": True, "cancel": False}
    links = [l.strip() for l in links_text.strip().splitlines() if l.strip()]

    if not links:
        task_states.pop(chat_id_task, None)
        return await event.respond("❌ ɴᴏ ᴠᴀʟɪᴅ ʟɪɴᴋꜱ !")
    if len(links) > 20:
        task_states.pop(chat_id_task, None)
        return await event.respond("⚠️ ᴍᴀx 20 ᴘᴏꜱᴛꜱ ᴀᴛ ᴏɴᴄᴇ !")

    msg = await event.respond(f"👁️ ᴘʀᴏᴄᴇꜱꜱɪɴɢ {len(links)} ᴘᴏꜱᴛꜱ ...", buttons=get_stop_button())
    total_views = 0
    results = []
    cancelled = False

    for idx, link in enumerate(links, 1):
        if task_states.get(chat_id_task, {}).get("cancel"):
            cancelled = True
            break
        try:
            await msg.edit(
                f"📌 ᴘʀᴏᴄᴇꜱꜱɪɴɢ ᴘᴏꜱᴛ {idx}/{len(links)} ...\n🔗 {link[:50]}",
                buttons=get_stop_button(),
            )
        except Exception:
            pass

        chat_id = channel_username = message_id = None
        if m := re.search(r"t\.me/([a-zA-Z0-9_]+)/(\d+)", link):
            channel_username, message_id = m.group(1), int(m.group(2))
        if m := re.search(r"t\.me/c/(\d+)/(\d+)", link):
            chat_id = int(f"-100{m.group(1)}")
            message_id = int(m.group(2))
        if not channel_username and not chat_id:
            results.append(f"❌ {link[:30]}... - ɪɴᴠᴀʟɪᴅ ꜰᴏʀᴍᴀᴛ")
            continue

        views_sent = 0
        clients_list = list(active_clients.items())
        random.shuffle(clients_list)
        for phone, client in clients_list:
            if task_states.get(chat_id_task, {}).get("cancel"):
                cancelled = True
                break
            trigger_temp_online(phone, duration_seconds=90)
            try:
                if chat_id:
                    message = await client.get_messages(chat_id, ids=message_id)
                else:
                    entity = await client.get_entity(channel_username)
                    message = await client.get_messages(entity, ids=message_id)
                if message:
                    views_sent += 1
                    total_views += 1
                await log_activity(phone, "VIEW", link, "Success")
            except Exception as e:
                await log_activity(phone, "VIEW", link, str(e)[:30])
            if await interruptible_sleep(0.2 + random.uniform(0.05, 0.15), chat_id_task):
                cancelled = True
                break
        results.append(f"✅ {link[:30]}... - {views_sent} ᴠɪᴇᴡꜱ")
        if cancelled:
            break

    task_states.pop(chat_id_task, None)
    title = "⛔ ʙᴀᴛᴄʜ ᴠɪᴇᴡꜱ ꜰᴏʀᴄᴇ ꜱᴛᴏᴘᴘᴇᴅ !" if cancelled else "✅ ʙᴀᴛᴄʜ ᴠɪᴇᴡꜱ ᴄᴏᴍᴘʟᴇᴛᴇ !"
    text = f"{title}\n━━━━━━━━━━━━━━━━━━━━━━\n📊 ʀᴇꜱᴜʟᴛꜱ\n━━━━━━━━━━━━━━━━━━━━━━\n"
    for r in results[:10]:
        text += f"{r}\n"
    if len(results) > 10:
        text += f"\n+ {len(results) - 10} ᴍᴏʀᴇ"
    text += f"\n━━━━━━━━━━━━━━━━━━━━━━\n👁 ᴛᴏᴛᴀʟ ᴠɪᴇᴡꜱ: {total_views}"
    await msg.edit(text, buttons=None)
    await asyncio.sleep(1)
    await show_menu(event, event.sender_id)

# ======================= CHANGE NAME (LIVE INSTANT) =======================
async def execute_change_name(event, first_name: str, last_name: str = ""):
    """
    Change first + last name of ALL active accounts instantly (parallel).
    """
    chat_id = event.chat_id
    task_states[chat_id] = {"running": True, "cancel": False}

    total = len(active_clients)
    if total == 0:
        task_states.pop(chat_id, None)
        return await event.respond("❌ ɴᴏ ᴀᴄᴄᴏᴜɴᴛꜱ !")

    display_name = f"{first_name} {last_name}".strip()
    msg = await event.respond(
        f"✏️ ᴄʜᴀɴɢɪɴɢ ɴᴀᴍᴇ ᴛᴏ: <b>{display_name}</b>\n"
        f"👥 ᴛᴏᴛᴀʟ ᴀᴄᴄᴏᴜɴᴛꜱ: {total}\n"
        f"⚡ ʟɪᴠᴇ ɪɴꜱᴛᴀɴᴛ ᴍᴏᴅᴇ ...",
        buttons=get_stop_button(),
    )

    success = 0
    failed = 0
    done = 0
    lock = asyncio.Lock()

    async def change_one(phone: str, client: TelegramClient):
        nonlocal success, failed, done
        if task_states.get(chat_id, {}).get("cancel"):
            return
        trigger_temp_online(phone, duration_seconds=120)
        try:
            await client(UpdateProfileRequest(
                first_name=first_name,
                last_name=last_name,
            ))
            async with lock:
                success += 1
            await log_activity(phone, "CHANGE_NAME", display_name, "Success")
        except FloodWaitError as e:
            wait_time = min(e.seconds, 20)
            await asyncio.sleep(wait_time)
            try:
                await client(UpdateProfileRequest(
                    first_name=first_name, last_name=last_name,
                ))
                async with lock:
                    success += 1
                await log_activity(phone, "CHANGE_NAME", display_name, "Success after flood")
            except Exception as e2:
                async with lock:
                    failed += 1
                await log_activity(phone, "CHANGE_NAME", display_name, str(e2)[:40])
        except Exception as e:
            async with lock:
                failed += 1
            await log_activity(phone, "CHANGE_NAME", display_name, str(e)[:40])
        finally:
            async with lock:
                done += 1
            # Live progress update
            if done % max(1, total // 10) == 0 or done == total:
                percent = int((done / total) * 100)
                try:
                    await msg.edit(
                        f"✏️ ᴄʜᴀɴɢɪɴɢ ɴᴀᴍᴇ ᴛᴏ: <b>{display_name}</b>\n"
                        f"[{progress_bar(percent)}] {percent}%\n"
                        f"👤 ᴘʀᴏɢʀᴇꜱꜱ: {done}/{total}\n"
                        f"✅ ꜱᴜᴄᴄᴇꜱꜱ: {success}  |  ❌ ꜰᴀɪʟᴇᴅ: {failed}",
                        buttons=get_stop_button(),
                    )
                except Exception:
                    pass

    # ⚡ Parallel = LIVE INSTANT
    tasks = [change_one(phone, cli) for phone, cli in list(active_clients.items())]
    await asyncio.gather(*tasks, return_exceptions=True)

    cancelled = task_states.get(chat_id, {}).get("cancel", False)
    task_states.pop(chat_id, None)

    title = "⛔ ᴄʜᴀɴɢᴇ ɴᴀᴍᴇ ꜰᴏʀᴄᴇ ꜱᴛᴏᴘᴘᴇᴅ !" if cancelled else "✅ ɴᴀᴍᴇ ᴄʜᴀɴɢᴇ ᴄᴏᴍᴘʟᴇᴛᴇ !"
    final = (
        f"{title}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📝 ɴᴇᴡ ɴᴀᴍᴇ: {display_name}\n"
        f"✅ ꜱᴜᴄᴄᴇꜱꜱ: {success}\n"
        f"❌ ꜰᴀɪʟᴇᴅ: {failed}\n"
        f"👥 ᴛᴏᴛᴀʟ: {total}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )
    await msg.edit(final, buttons=None)
    await asyncio.sleep(1)
    await show_menu(event, event.sender_id)

# ======================= MENU =======================
async def show_menu(event, user_id, edit_msg=None):
    accounts_count = len(active_clients)
    today_joins = await get_today_joins()
    uptime = get_uptime()
    active_vc_monitors = len(VC_MONITORS)

    menu = (
        "🤖 ᴊᴏɪɴ / ʟᴇᴀᴠᴇ & ᴀᴜᴛᴏ ᴠɪᴇᴡᴇʀ ᴘʀᴏ\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 ᴀᴄᴄᴏᴜɴᴛꜱ : {accounts_count}\n"
        f"📊 ᴛᴏᴅᴀʏ ᴊᴏɪɴꜱ : {today_joins}\n"
        f"🎙️ ᴀᴄᴛɪᴠᴇ ᴠᴄ : {active_vc_monitors}\n"
        f"⏱️ ᴜᴘᴛɪᴍᴇ : {uptime}\n"
        "⚡ ᴀᴜᴛᴏ ᴍᴏɴɪᴛᴏʀ : ᴀᴄᴛɪᴠᴇ (ꜱᴍᴀʀᴛ ᴍɪxᴇᴅ) 🟢\n"
        "🛡️ ᴀᴅᴍɪɴ ᴘʀᴏᴛᴇᴄᴛɪᴏɴ : ᴀᴄᴛɪᴠᴇ 🔒\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🎯 ꜱᴇʟᴇᴄᴛ ᴀɴ ᴏᴘᴛɪᴏɴ ꜰʀᴏᴍ ʙᴇʟᴏᴡ :"
    )

    is_owner = user_id in OWNER_IDS

    if is_owner:
        buttons = [
            [_btn("➕ ᴀᴅᴅ ᴀᴄᴄᴏᴜɴᴛ", "add", style="success")],
            [
                _btn("🚀 ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟ", "join",  style="success"),
                _btn("📤 ʟᴇᴀᴠᴇ ᴄʜᴀᴛ",  "leave", style="danger"),
            ],
            [
                _btn("🎙️ ᴊᴏɪɴ ᴠᴄ",  "join_vc",  style="success"),
                _btn("🔴 ʟᴇᴀᴠᴇ ᴠᴄ", "leave_vc", style="danger"),
            ],
            [
                _btn("⚡ ʀᴇᴀᴄᴛ + ᴠɪᴇᴡ", "react_view",  style="primary"),
                _btn("💥 ᴍᴜʟᴛɪ ʀᴇᴀᴄᴛ",  "multi_react", style="primary"),
            ],
            [_btn("👁️ ᴠɪᴇᴡꜱ ᴍᴇɴᴜ", "views_menu", style="primary")],
            [_btn("✏️ ᴄʜᴀɴɢᴇ ɴᴀᴍᴇ (ʟɪᴠᴇ)", "change_name", style="success")],
            [
                _btn("📋 ᴀᴄᴄᴏᴜɴᴛꜱ ʟɪꜱᴛ", "list_accounts", style="primary"),
                _btn("👑 ᴀᴘᴘʀᴏᴠᴇ",       "approve",       style="success"),
            ],
            [_btn("📊 ꜱᴛᴀᴛɪꜱᴛɪᴄꜱ", "stats", style="primary")],
        ]
    else:
        buttons = [
            [_btn("➕ ᴀᴅᴅ ᴀᴄᴄᴏᴜɴᴛ", "add", style="success")],
            [
                _btn("🚀 ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟ", "join",  style="success"),
                _btn("📤 ʟᴇᴀᴠᴇ ᴄʜᴀᴛ",  "leave", style="danger"),
            ],
            [
                _btn("🎙️ ᴊᴏɪɴ ᴠᴄ",  "join_vc",  style="success"),
                _btn("🔴 ʟᴇᴀᴠᴇ ᴠᴄ", "leave_vc", style="danger"),
            ],
            [
                _btn("⚡ ʀᴇᴀᴄᴛ + ᴠɪᴇᴡ", "react_view",  style="primary"),
                _btn("💥 ᴍᴜʟᴛɪ ʀᴇᴀᴄᴛ",  "multi_react", style="primary"),
            ],
            [_btn("👁️ ᴠɪᴇᴡꜱ ᴍᴇɴᴜ", "views_menu", style="primary")],
            [_btn("✏️ ᴄʜᴀɴɢᴇ ɴᴀᴍᴇ (ʟɪᴠᴇ)", "change_name", style="success")],
            [_btn("📋 ᴀᴄᴄᴏᴜɴᴛꜱ ʟɪꜱᴛ", "list_accounts", style="primary")],
        ]

    if edit_msg:
        try:
            await edit_msg.edit(menu, buttons=buttons)
        except MessageNotModifiedError:
            pass
        except Exception:
            try:
                await event.respond(menu, buttons=buttons)
            except Exception:
                pass
    else:
        await event.respond(menu, buttons=buttons)

bot = TelegramClient("bot_session", API_ID, API_HASH)
bot.parse_mode = "html"


# ======================= AUTO LISTENER =======================
@bot.on(events.NewMessage(incoming=True))
async def auto_channel_post_listener(event):
    if not event.is_channel:
        return
    if event.raw_text and event.raw_text.startswith("/"):
        return
    try:
        channel = await event.get_chat()
        channel_title = getattr(channel, "title", "Channel")
        username = getattr(channel, "username", None)
        logger.info(f"⚡ New Post Detected in: {channel_title} | Msg ID: {event.id}")
        asyncio.create_task(send_auto_views(event.chat_id, event.id, username))
    except Exception as e:
        logger.error(f"Auto listener error: {e}")


# ======================= COMMANDS =======================
@bot.on(events.NewMessage(pattern="/start"))
async def start_cmd(event):
    user_id = event.sender_id
    if not await is_user_approved(user_id):
        await event.respond("⛔ ᴀᴄᴄᴇꜱꜱ ᴅᴇɴɪᴇᴅ\nᴄᴏɴᴛᴀᴄᴛ ᴏᴡɴᴇʀ ᴛᴏ ɢᴇᴛ ᴀᴘᴘʀᴏᴠᴇᴅ.")
        return
    task_states.pop(event.chat_id, None)
    await show_menu(event, user_id)


@bot.on(events.NewMessage(pattern=r"/add\s+(.+)"))
async def add_user_cmd(event):
    if event.sender_id not in OWNER_IDS:
        return await event.respond("⛔ ᴏᴡɴᴇʀ ᴏɴʟʏ !")
    try:
        uid, username_str, title = await resolve_user(bot, event.pattern_match.group(1).strip())
        if not uid:
            return await event.respond("❌ ᴜꜱᴇʀ ɴᴏᴛ ꜰᴏᴜɴᴅ !")
        if await approve_user(uid, event.sender_id, username=username_str, title=title):
            await event.respond(f"✅ ᴜꜱᴇʀ {title} ({uid}) ᴀᴘᴘʀᴏᴠᴇᴅ !")
        else:
            await event.respond(f"❌ ꜰᴀɪʟᴇᴅ ᴛᴏ ᴀᴘᴘʀᴏᴠᴇ {title}.")
    except Exception as e:
        await event.respond(f"❌ ᴇʀʀᴏʀ: {e}")


@bot.on(events.NewMessage(pattern=r"/remove\s+(.+)"))
async def remove_user_cmd(event):
    if event.sender_id not in OWNER_IDS:
        return await event.respond("⛔ ᴏᴡɴᴇʀ ᴏɴʟʏ !")
    try:
        uid, _, title = await resolve_user(bot, event.pattern_match.group(1).strip())
        if not uid:
            return await event.respond("❌ ᴜꜱᴇʀ ɴᴏᴛ ꜰᴏᴜɴᴅ !")
        if await unapprove_user(uid):
            await event.respond(f"❌ ᴜꜱᴇʀ {title} ({uid}) ʀᴇᴍᴏᴠᴇᴅ !")
        else:
            await event.respond(f"⚠️ ᴜꜱᴇʀ {title} ɴᴏᴛ ɪɴ ᴀᴘᴘʀᴏᴠᴇᴅ ʟɪꜱᴛ.")
    except Exception as e:
        await event.respond(f"❌ ᴇʀʀᴏʀ: {e}")


@bot.on(events.NewMessage(pattern="/list"))
async def list_cmd(event):
    if not await is_user_approved(event.sender_id):
        return
    users = await get_approved_users()
    if not users:
        return await event.respond("📋 ɴᴏ ᴀᴘᴘʀᴏᴠᴇᴅ ᴜꜱᴇʀꜱ")
    msg = "📋 ᴀᴘᴘʀᴏᴠᴇᴅ ᴜꜱᴇʀꜱ\n━━━━━━━━━━━━━━━━\n"
    for i, u in enumerate(users, 1):
        uname = u.get("username") or ""
        title = u.get("title") or "User"
        msg += f"{i}. {title} | {uname} (`{u['user_id']}`)\n" if uname else f"{i}. {title} (`{u['user_id']}`)\n"
    await event.respond(msg)


# ======================= CALLBACKS =======================
@bot.on(events.CallbackQuery)
async def callback(event):
    user_id = event.sender_id
    if not await is_user_approved(user_id):
        return await event.answer("ᴀᴄᴄᴇꜱꜱ ᴅᴇɴɪᴇᴅ !", alert=True)
    data = event.data.decode()

    if data == "force_stop":
        if event.chat_id in task_states:
            task_states[event.chat_id]["cancel"] = True
            await event.answer("🛑 ꜱᴛᴏᴘᴘɪɴɢ ᴛᴀꜱᴋ ...", alert=True)
        else:
            await event.answer("ɴᴏ ʀᴜɴɴɪɴɢ ᴛᴀꜱᴋ ᴛᴏ ꜱᴛᴏᴘ.", alert=True)
        return

    if data == "back":
        await event.answer()
        if event.chat_id in task_states:
            state = task_states[event.chat_id]
            if state.get("type") == "login" and state.get("client"):
                try:
                    await state["client"].disconnect()
                except Exception:
                    pass
            del task_states[event.chat_id]
        try:
            current_msg = await event.get_message()
            await show_menu(event, user_id, edit_msg=current_msg)
        except Exception as e:
            logger.error(f"Back button error: {e}")
            try:
                await event.respond(
                    "🔙 ᴍᴀɪɴ ᴍᴇɴᴜ",
                    buttons=[[_btn("🏠 ᴍᴇɴᴜ", "main_menu", style="primary")]],
                )
            except Exception:
                pass
        return

    if data == "main_menu":
        await event.answer()
        task_states.pop(event.chat_id, None)
        try:
            current_msg = await event.get_message()
        except Exception:
            current_msg = None
        await show_menu(event, user_id, edit_msg=current_msg)
        return

    await event.answer()

    # ---------- ADD ACCOUNT ----------
    if data == "add":
        await event.edit("ᴀᴅᴅ ᴀᴄᴄᴏᴜɴᴛ", buttons=[
            [_btn("ᴘʜᴏɴᴇ",   "add_phone",  style="primary")],
            [_btn("ꜱᴇꜱꜱɪᴏɴ", "add_string", style="primary")],
            [_btn("🔙 ʙᴀᴄᴋ", "back",       style="danger")],
        ])
        return

    if data == "add_phone":
        task_states[event.chat_id] = {"type": "login", "step": "phone"}
        await event.edit(
            "📱 ꜱᴇɴᴅ ᴘʜᴏɴᴇ ɴᴜᴍʙᴇʀ\nᴇxᴀᴍᴘʟᴇ: +1234567890",
            buttons=get_cancel(),
        )
        return

    if data == "add_string":
        task_states[event.chat_id] = {"type": "login", "step": "string"}
        await event.edit(
            "🔑 ꜱᴇɴᴅ ᴛᴇʟᴇᴛʜᴏɴ ꜱᴇꜱꜱɪᴏɴ ꜱᴛʀɪɴɢ",
            buttons=get_cancel(),
        )
        return

    # ---------- JOIN ----------
    if data == "join":
        if not active_clients:
            return await event.respond("❌ ɴᴏ ᴀᴄᴄᴏᴜɴᴛꜱ !")
        task_states[event.chat_id] = {"type": "join", "step": "link"}
        await event.edit(
            "🔗 ꜱᴇɴᴅ ʟɪɴᴋꜱ (ᴏɴᴇ ᴘᴇʀ ʟɪɴᴇ, ᴍᴀx 50)",
            buttons=get_cancel(),
        )
        return

    if data == "join_vc":
        if not active_clients:
            return await event.respond("❌ ɴᴏ ᴀᴄᴄᴏᴜɴᴛꜱ !")
        task_states[event.chat_id] = {"type": "vc_join", "step": "link"}
        await event.edit(
            "🎙️ ꜱᴇɴᴅ ᴠᴏɪᴄᴇ ᴄʜᴀᴛ ʟɪɴᴋ ᴏʀ ᴄʜᴀɴɴᴇʟ ɪɴᴠɪᴛᴇ ʟɪɴᴋ:",
            buttons=get_cancel(),
        )
        return

    if data == "leave_vc":
        if not active_clients:
            return await event.respond("❌ ɴᴏ ᴀᴄᴄᴏᴜɴᴛꜱ !")
        await event.edit("🎙️ ʟᴇᴀᴠɪɴɢ ᴀʟʟ ᴠᴏɪᴄᴇ ᴄʜᴀᴛꜱ ...")
        await execute_vc_leave(event)
        return

    if data.startswith("vc_qty_"):
        if event.chat_id not in task_states:
            return await event.edit(
                "⚠️ ꜱᴇꜱꜱɪᴏɴ ᴇxᴘɪʀᴇᴅ. ᴘʟᴇᴀꜱᴇ ꜱᴛᴀʀᴛ ᴀɢᴀɪɴ.",
                buttons=get_cancel(),
            )
        qty = data.replace("vc_qty_", "")
        total = len(active_clients)
        if qty == "custom":
            task_states[event.chat_id]["step"] = "custom_qty"
            await event.edit(
                f"🔢 ᴇɴᴛᴇʀ ɴᴜᴍʙᴇʀ ᴏꜰ ᴀᴄᴄᴏᴜɴᴛꜱ (1-{total})",
                buttons=get_cancel(),
            )
            return
        count = total if qty == "all" else min(int(qty), total)
        vc_link = task_states[event.chat_id].get("vc_link")
        if vc_link:
            await event.delete()
            await execute_vc_join(event, vc_link, count)
        return

    # ---------- REACT ----------
    if data == "react_view":
        if not active_clients:
            return await event.respond("❌ ɴᴏ ᴀᴄᴄᴏᴜɴᴛꜱ !")
        task_states[event.chat_id] = {"type": "react_view", "step": "link"}
        await event.edit(
            "🔗 ꜱᴇɴᴅ ᴘᴏꜱᴛ ʟɪɴᴋ(ꜱ) (ᴏɴᴇ ᴘᴇʀ ʟɪɴᴇ, ᴍᴀx 50)",
            buttons=get_cancel(),
        )
        return

    if data == "multi_react":
        if not active_clients:
            return await event.respond("❌ ɴᴏ ᴀᴄᴄᴏᴜɴᴛꜱ !")
        task_states[event.chat_id] = {"type": "multi_react", "step": "link"}
        await event.edit(
            "🔗 ꜱᴇɴᴅ ᴘᴏꜱᴛ ʟɪɴᴋ(ꜱ) (ᴏɴᴇ ᴘᴇʀ ʟɪɴᴇ, ᴍᴀx 50)",
            buttons=get_cancel(),
        )
        return

    # ---------- CHANGE NAME (LIVE INSTANT) ----------
    if data == "change_name":
        if not active_clients:
            return await event.respond("❌ ɴᴏ ᴀᴄᴄᴏᴜɴᴛꜱ !")
        task_states[event.chat_id] = {"type": "change_name", "step": "name"}
        await event.edit(
            "✏️ ᴄʜᴀɴɢᴇ ɴᴀᴍᴇ (ʟɪᴠᴇ ɪɴꜱᴛᴀɴᴛ)\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👥 ᴀᴄᴛɪᴠᴇ ᴀᴄᴄᴏᴜɴᴛꜱ: {len(active_clients)}\n\n"
            "ꜱᴇɴᴅ ɴᴇᴡ ɴᴀᴍᴇ ɪɴ ᴛʜɪꜱ ꜰᴏʀᴍᴀᴛ:\n"
            "• <b>First</b> — only first name\n"
            "• <b>First Last</b> — first + last name\n\n"
            "ᴇxᴀᴍᴘʟᴇ:\n"
            "<code>Rahul</code>  → first only\n"
            "<code>Rahul Sharma</code>  → first + last",
            buttons=get_cancel(),
        )
        return

    # ---------- VIEWS ----------
    if data == "views_menu":
        await event.edit("👁️ ᴠɪᴇᴡꜱ ᴏᴘᴛɪᴏɴꜱ", buttons=[
            [_btn("ꜱɪɴɢʟᴇ ᴘᴏꜱᴛ", "views_single", style="primary")],
            [_btn("ʙᴀᴛᴄʜ ᴘᴏꜱᴛꜱ", "views_batch",  style="primary")],
            [_btn("🔙 ʙᴀᴄᴋ",     "back",         style="danger")],
        ])
        return

    if data == "views_single":
        if not active_clients:
            return await event.respond("❌ ɴᴏ ᴀᴄᴄᴏᴜɴᴛꜱ !")
        task_states[event.chat_id] = {"type": "views", "subtype": "single", "step": "link"}
        await event.edit(
            "🔗 ꜱᴇɴᴅ ᴛᴇʟᴇɢʀᴀᴍ ᴘᴏꜱᴛ ʟɪɴᴋ\n\n"
            "✅ ᴠᴀʟɪᴅ ꜰᴏʀᴍᴀᴛꜱ:\n"
            "• https://t.me/username/123\n"
            "• https://t.me/c/123456789/123",
            buttons=get_cancel(),
        )
        return

    if data == "views_batch":
        if not active_clients:
            return await event.respond("❌ ɴᴏ ᴀᴄᴄᴏᴜɴᴛꜱ !")
        task_states[event.chat_id] = {"type": "views", "subtype": "batch", "step": "links"}
        await event.edit(
            "📚 ꜱᴇɴᴅ ᴍᴜʟᴛɪᴘʟᴇ ᴘᴏꜱᴛ ʟɪɴᴋꜱ (ᴏɴᴇ ᴘᴇʀ ʟɪɴᴇ, ᴍᴀx 20)",
            buttons=get_cancel(),
        )
        return

    if data.startswith("views_qty_"):
        if event.chat_id not in task_states:
            return await event.edit(
                "⚠️ ꜱᴇꜱꜱɪᴏɴ ᴇxᴘɪʀᴇᴅ. ᴘʟᴇᴀꜱᴇ ꜱᴛᴀʀᴛ ᴀɢᴀɪɴ.",
                buttons=get_cancel(),
            )
        qty = data.replace("views_qty_", "")
        total = len(active_clients)
        if qty == "custom":
            task_states[event.chat_id]["step"] = "custom_qty"
            await event.edit(
                f"🔢 ᴇɴᴛᴇʀ ɴᴜᴍʙᴇʀ ᴏꜰ ᴀᴄᴄᴏᴜɴᴛꜱ (1-{total})",
                buttons=get_cancel(),
            )
            return
        count = total if qty == "all" else min(int(qty), total)
        post_link = task_states[event.chat_id].get("post_link")
        if post_link:
            await event.delete()
            await execute_views(event, post_link, count)
        return

    # ---------- QTY (JOIN / REACT / MULTI) ----------
    if data.startswith("qty_"):
        if event.chat_id not in task_states:
            return await event.edit(
                "⚠️ ꜱᴇꜱꜱɪᴏɴ ᴇxᴘɪʀᴇᴅ. ᴘʟᴇᴀꜱᴇ ꜱᴛᴀʀᴛ ᴀɢᴀɪɴ.",
                buttons=get_cancel(),
            )
        state = task_states[event.chat_id]
        qty = data.split("_")[1]
        total = len(active_clients)
        if qty == "custom":
            state["step"] = "custom_qty"
            await event.edit(
                "🔢 ᴇɴᴛᴇʀ ɴᴜᴍʙᴇʀ ᴏꜰ ᴀᴄᴄᴏᴜɴᴛꜱ",
                buttons=get_cancel(),
            )
            return
        count = total if qty == "all" else int(qty) if qty.isdigit() else total
        count = min(count, total)
        await event.delete()
        if state["type"] == "join":
            await execute_join(event, state, count)
        elif state["type"] == "react_view":
            await execute_react_view(event, state, count)
        elif state["type"] == "multi_react":
            await execute_multi_react_view(event, state, count)
        return

    # ---------- LEAVE ----------
    if data == "leave":
        await event.edit("ʟᴇᴀᴠᴇ ᴄʜᴀᴛ", buttons=[
            [_btn("ʟᴇᴀᴠᴇ ᴏɴᴇ", "leave_specific", style="danger")],
            [_btn("ʟᴇᴀᴠᴇ ᴀʟʟ", "leave_all",      style="danger")],
            [_btn("🔙 ʙᴀᴄᴋ",   "back",           style="primary")],
        ])
        return

    if data == "leave_specific":
        task_states[event.chat_id] = {"type": "leave_specific", "step": "link"}
        await event.edit(
            "📤 ꜱᴇɴᴅ ᴄʜᴀɴɴᴇʟ ʟɪɴᴋ ᴛᴏ ʟᴇᴀᴠᴇ",
            buttons=get_cancel(),
        )
        return

    if data == "leave_all":
        await event.edit(
            "ᴅᴀɴɢᴇʀ ᴢᴏɴᴇ\nʟᴇᴀᴠᴇ ᴀʟʟ ᴄʜᴀɴɴᴇʟꜱ?\nᴀʀᴇ ʏᴏᴜ ꜱᴜʀᴇ?",
            buttons=[
                [_btn("ᴄᴏɴꜰɪʀᴍ", "exec_leave_all", style="success")],
                [_btn("ᴄᴀɴᴄᴇʟ",  "back",           style="danger")],
            ],
        )
        return

    if data == "exec_leave_all":
        await event.edit("⚠️ ᴘʀᴏᴄᴇꜱꜱɪɴɢ ...")
        await execute_leave_all(event)
        return

    # ---------- LIST ACCOUNTS ----------
    if data == "list_accounts":
        sessions = await get_all_sessions()
        if not sessions:
            return await event.edit(
                "📋 ɴᴏ ᴀᴄᴄᴏᴜɴᴛꜱ ꜰᴏᴜɴᴅ.",
                buttons=[[_btn("🔙 ʙᴀᴄᴋ", "back", style="danger")]],
            )
        msg = "📋 ꜱᴀᴠᴇᴅ ᴀᴄᴄᴏᴜɴᴛꜱ\n━━━━━━━━━━━━━━━━\n"
        for i, phone in enumerate(sessions.keys(), 1):
            status = "🟢" if phone in active_clients else "🔴"
            msg += f"{i}. {status} {phone} (ꜱᴍᴀʀᴛ_ᴍɪxᴇᴅ)\n"
        await event.edit(
            msg,
            buttons=[[_btn("🔙 ʙᴀᴄᴋ", "back", style="danger")]],
        )
        return

    # ---------- APPROVE / USERS ----------
    if data == "approve":
        if user_id not in OWNER_IDS:
            return await event.respond("ᴏᴡɴᴇʀ ᴏɴʟʏ !")
        await event.edit(
            "👑 ᴜꜱᴇʀ ᴍᴀɴᴀɢᴇᴍᴇɴᴛ ᴘᴀɴᴇʟ\n"
            "━━━━━━━━━━━━━━━━\n"
            "ꜱᴇʟᴇᴄᴛ ᴀɴ ᴀᴄᴛɪᴏɴ ʙᴇʟᴏᴡ ᴏʀ ᴜꜱᴇ ᴄᴏᴍᴍᴀɴᴅꜱ ᴅɪʀᴇᴄᴛʟʏ:\n\n"
            "• /add <username/userid>\n"
            "• /remove <username/userid>\n"
            "• /list",
            buttons=[
                [
                    _btn("ᴀᴅᴅ ᴀᴅᴍɪɴ",    "btn_add_admin",    style="success"),
                    _btn("ʀᴇᴍᴏᴠᴇ ᴀᴅᴍɪɴ", "btn_remove_admin", style="danger"),
                ],
                [_btn("🔙 ʙᴀᴄᴋ", "back", style="primary")],
            ],
        )
        return

    if data == "btn_add_admin":
        if user_id not in OWNER_IDS:
            return await event.respond("ᴏᴡɴᴇʀ ᴏɴʟʏ !")
        task_states[event.chat_id] = {"type": "admin_manage", "action": "add"}
        await event.edit(
            "👤 ꜱᴇɴᴅ ᴛᴇʟᴇɢʀᴀᴍ ᴜꜱᴇʀ ɪᴅ ᴏʀ @ᴜꜱᴇʀɴᴀᴍᴇ ᴛᴏ ᴀᴅᴅ ᴀᴅᴍɪɴ:",
            buttons=get_cancel(),
        )
        return

    if data == "btn_remove_admin":
        if user_id not in OWNER_IDS:
            return await event.respond("ᴏᴡɴᴇʀ ᴏɴʟʏ !")
        task_states[event.chat_id] = {"type": "admin_manage", "action": "remove"}
        await event.edit(
            "👤 ꜱᴇɴᴅ ᴛᴇʟᴇɢʀᴀᴍ ᴜꜱᴇʀ ɪᴅ ᴏʀ @ᴜꜱᴇʀɴᴀᴍᴇ ᴛᴏ ʀᴇᴍᴏᴠᴇ ᴀᴅᴍɪɴ:",
            buttons=get_cancel(),
        )
        return

    # ---------- STATS ----------
    if data == "stats":
        if user_id not in OWNER_IDS:
            return await event.respond("ᴏᴡɴᴇʀ ᴏɴʟʏ !")
        users = await get_approved_users()
        today_joins = await get_today_joins()
        uptime = get_uptime()
        msg = (
            "📊 ꜱᴛᴀᴛɪꜱᴛɪᴄꜱ\n"
            "━━━━━━━━━━━━━━━━\n"
            f"◎ ᴀᴘᴘʀᴏᴠᴇᴅ: {len(users)}\n"
            f"◎ ᴀᴄᴄᴏᴜɴᴛꜱ: {len(active_clients)}\n"
            "  - ꜱᴛᴀᴛᴜꜱ ꜱᴛʀᴀᴛᴇɢʏ: ꜱᴍᴀʀᴛ ᴍɪxᴇᴅ (ɴᴀᴛᴜʀᴀʟ ᴊɪᴛᴛᴇʀ)\n"
            f"◎ ᴀᴄᴛɪᴠᴇ ᴠᴄ ᴍᴏɴɪᴛᴏʀꜱ: {len(VC_MONITORS)}\n"
            f"◎ ᴛᴏᴅᴀʏ ᴊᴏɪɴꜱ: {today_joins}\n"
            f"◎ ᴜᴘᴛɪᴍᴇ: {uptime}"
        )
        await event.edit(
            msg,
            buttons=[[_btn("🔙 ʙᴀᴄᴋ", "back", style="danger")]],
        )
        return


# ======================= MESSAGE HANDLER =======================
@bot.on(events.NewMessage)
async def message_handler(event):
    user_id = event.sender_id
    if not await is_user_approved(user_id):
        return
    chat_id = event.chat_id
    if chat_id not in task_states:
        return
    state = task_states[chat_id]
    if state.get("running"):
        return
    text = event.raw_text.strip()

    if state.get("type") == "admin_manage":
        action = state.get("action")
        uid, username_str, title = await resolve_user(bot, text)
        if not uid:
            return await event.respond("⚠️ ᴜꜱᴇʀ ɴᴏᴛ ꜰᴏᴜɴᴅ ! ᴘʟᴇᴀꜱᴇ ꜱᴇɴᴅ ᴀ ᴠᴀʟɪᴅ ᴜꜱᴇʀ ɪᴅ ᴏʀ ᴜꜱᴇʀɴᴀᴍᴇ.", buttons=get_cancel())
        if action == "add":
            if await approve_user(uid, user_id, username=username_str, title=title):
                await event.respond(f"✅ ᴜꜱᴇʀ {title} ({uid}) ᴀᴘᴘʀᴏᴠᴇᴅ ᴀꜱ ᴀᴅᴍɪɴ !")
            else:
                await event.respond(f"❌ ꜰᴀɪʟᴇᴅ ᴛᴏ ᴀᴘᴘʀᴏᴠᴇ {title}.")
        elif action == "remove":
            if await unapprove_user(uid):
                await event.respond(f"❌ ᴜꜱᴇʀ {title} ({uid}) ʀᴇᴍᴏᴠᴇᴅ !")
            else:
                await event.respond(f"⚠️ ᴜꜱᴇʀ {title} ɴᴏᴛ ɪɴ ᴀᴘᴘʀᴏᴠᴇᴅ ʟɪꜱᴛ.")
        del task_states[chat_id]
        await show_menu(event, user_id)
        return

    if state.get("type") == "login":
        if state["step"] == "phone":
            phone = text.replace(" ", "")
            client = TelegramClient(StringSession(), API_ID, API_HASH)
            await client.connect()
            try:
                send = await client.send_code_request(phone)
                state.update({"client": client, "phone": phone, "hash": send.phone_code_hash, "step": "otp"})
                await event.respond(f"📩 ᴏᴛᴘ ꜱᴇɴᴛ ᴛᴏ {phone}\nᴇɴᴛᴇʀ ᴄᴏᴅᴇ:", buttons=get_cancel())
            except Exception as e:
                await event.respond(f"❌ ᴇʀʀᴏʀ: {e}", buttons=get_cancel())
        elif state["step"] == "otp":
            try:
                await state["client"].sign_in(phone=state["phone"], code=text, phone_code_hash=state["hash"])
                await add_session(state["phone"], state["client"].session.save())
                active_clients[state["phone"]] = state["client"]
                account_modes[state["phone"]] = "SMART_MIXED"
                next_random_toggle[state["phone"]] = time.time() + random.randint(10, 300)
                asyncio.create_task(keep_alive(state["client"], state["phone"]))
                trigger_temp_online(state["phone"], duration_seconds=90)
                del task_states[chat_id]
                await event.respond(f"✅ ᴀᴅᴅᴇᴅ: {state['phone']}")
                await show_menu(event, user_id)
            except SessionPasswordNeededError:
                state["step"] = "2fa"
                await event.respond("🔐 ᴇɴᴛᴇʀ 2ꜰᴀ ᴘᴀꜱꜱᴡᴏʀᴅ:", buttons=get_cancel())
            except Exception as e:
                await event.respond(f"❌ ꜰᴀɪʟᴇᴅ: {e}", buttons=get_cancel())
        elif state["step"] == "2fa":
            try:
                await state["client"].sign_in(password=text)
                await add_session(state["phone"], state["client"].session.save())
                active_clients[state["phone"]] = state["client"]
                account_modes[state["phone"]] = "SMART_MIXED"
                next_random_toggle[state["phone"]] = time.time() + random.randint(10, 300)
                asyncio.create_task(keep_alive(state["client"], state["phone"]))
                trigger_temp_online(state["phone"], duration_seconds=90)
                del task_states[chat_id]
                await event.respond(f"✅ ᴀᴅᴅᴇᴅ: {state['phone']} (2ꜰᴀ ꜱᴀᴠᴇᴅ)")
                await show_menu(event, user_id)
            except Exception as e:
                await event.respond(f"❌ ᴡʀᴏɴɢ ᴘᴀꜱꜱᴡᴏʀᴅ: {e}", buttons=get_cancel())
        elif state["step"] == "string":
            try:
                client = TelegramClient(StringSession(text), API_ID, API_HASH)
                await client.connect()
                me = await client.get_me()
                phone = f"+{me.phone}" if me.phone else f"id_{me.id}"
                await add_session(phone, client.session.save())
                active_clients[phone] = client
                account_modes[phone] = "SMART_MIXED"
                next_random_toggle[phone] = time.time() + random.randint(10, 300)
                asyncio.create_task(keep_alive(client, phone))
                trigger_temp_online(phone, duration_seconds=90)
                del task_states[chat_id]
                await event.respond(f"✅ ᴀᴅᴅᴇᴅ: {phone}")
                await show_menu(event, user_id)
            except Exception as e:
                await event.respond(f"❌ ɪɴᴠᴀʟɪᴅ ꜱᴇꜱꜱɪᴏɴ ꜱᴛʀɪɴɢ: {e}", buttons=get_cancel())

    elif state.get("type") == "join":
        if state["step"] == "link":
            links = text.strip().splitlines()
            valid = []
            for link in links:
                ltype, target = parse_target(link)
                if ltype:
                    valid.append({"link": link, "type": ltype, "target": target})
            if not valid:
                return await event.respond("❌ ɴᴏ ᴠᴀʟɪᴅ ʟɪɴᴋꜱ !", buttons=get_cancel())
            if len(valid) > 50:
                return await event.respond("⚠️ ᴍᴀx 50 ʟɪɴᴋꜱ !", buttons=get_cancel())
            state["links_data"] = valid
            state["step"] = "delay"
            await event.respond(f"✅ {len(valid)} ʟɪɴᴋꜱ ᴀᴄᴄᴇᴘᴛᴇᴅ\n⏱️ ᴇɴᴛᴇʀ ᴅᴇʟᴀʏ (ꜱᴇᴄᴏɴᴅꜱ):", buttons=get_cancel())
        elif state["step"] == "delay":
            if not text.isdigit():
                return await event.respond("⚠️ ɴᴜᴍʙᴇʀꜱ ᴏɴʟʏ !", buttons=get_cancel())
            state["delay"] = int(text)
            state["step"] = "qty"
            await event.respond(f"👥 ᴛᴏᴛᴀʟ ᴀᴄᴄᴏᴜɴᴛꜱ: {len(active_clients)}\nꜱᴇʟᴇᴄᴛ ǫᴜᴀɴᴛɪᴛʏ:", buttons=get_qty_buttons())
        elif state["step"] == "custom_qty":
            if not text.isdigit():
                return await event.respond("⚠️ ɴᴜᴍʙᴇʀꜱ ᴏɴʟʏ !", buttons=get_cancel())
            count = min(int(text), len(active_clients))
            await event.delete()
            await execute_join(event, state, count)

    elif state.get("type") == "vc_join":
        if state["step"] == "link":
            state["vc_link"] = text.strip()
            state["step"] = "qty"
            await event.respond(
                f"🎙️ ʟɪɴᴋ ʀᴇᴄᴇɪᴠᴇᴅ !\n\n👥 ᴛᴏᴛᴀʟ ᴀᴄᴄᴏᴜɴᴛꜱ: {len(active_clients)}\nʜᴏᴡ ᴍᴀɴʏ ᴀᴄᴄᴏᴜɴᴛꜱ ᴛᴏ ᴊᴏɪɴ ᴠᴄ?",
                buttons=get_vc_qty_buttons(),
            )
        elif state["step"] == "custom_qty":
            if not text.isdigit():
                return await event.respond("⚠️ ɴᴜᴍʙᴇʀꜱ ᴏɴʟʏ !", buttons=get_cancel())
            count = min(int(text), len(active_clients))
            vc_link = state.get("vc_link")
            await event.delete()
            if vc_link:
                await execute_vc_join(event, vc_link, count)

    elif state.get("type") in ("react_view", "multi_react"):
        if state["step"] == "link":
            links = text.strip().splitlines()
            valid = []
            for link in links:
                ltype, target = parse_target(link)
                if ltype and "t.me/" in link:
                    valid.append({"link": link, "type": ltype, "target": target})
            if not valid:
                return await event.respond("❌ ɴᴏ ᴠᴀʟɪᴅ ʟɪɴᴋꜱ !", buttons=get_cancel())
            if len(valid) > 50:
                return await event.respond("⚠️ ᴍᴀx 50 ʟɪɴᴋꜱ !", buttons=get_cancel())
            state["links_data"] = valid
            state["step"] = "emoji"
            if state["type"] == "react_view":
                await event.respond("😀 ᴇɴᴛᴇʀ ᴏɴᴇ ᴇᴍᴏᴊɪ (ᴇ.ɢ. ❤️):", buttons=get_cancel())
            else:
                await event.respond("🔥 ᴇɴᴛᴇʀ ᴍᴜʟᴛɪᴘʟᴇ ᴇᴍᴏᴊɪꜱ ꜱᴇᴘᴀʀᴀᴛᴇᴅ ʙʏ ᴄᴏᴍᴍᴀꜱ (ᴇ.ɢ. ❤️,🔥,👍):", buttons=get_cancel())
        elif state["step"] == "emoji":
            if state["type"] == "react_view":
                emoji = text.strip()
                if not emoji:
                    return await event.respond("⚠️ ᴘʟᴇᴀꜱᴇ ᴘʀᴏᴠɪᴅᴇ ᴀɴ ᴇᴍᴏᴊɪ.", buttons=get_cancel())
                state["emoji"] = emoji
            else:
                emojis = [e.strip() for e in text.split(",") if e.strip()]
                if not emojis:
                    return await event.respond("⚠️ ᴘʟᴇᴀꜱᴇ ᴘʀᴏᴠɪᴅᴇ ᴀᴛ ʟᴇᴀꜱᴛ ᴏɴᴇ ᴇᴍᴏᴊɪ.", buttons=get_cancel())
                state["emojis"] = emojis
            state["step"] = "speed"
            await event.respond("⏱️ ᴇɴᴛᴇʀ ᴅᴇʟᴀʏ ʙᴇᴛᴡᴇᴇɴ ᴀᴄᴄᴏᴜɴᴛꜱ (ꜱᴇᴄᴏɴᴅꜱ, ᴇ.ɢ. 0.2):", buttons=get_cancel())
        elif state["step"] == "speed":
            try:
                speed = float(text.replace(",", "."))
                if speed < 0:
                    raise ValueError
            except Exception:
                return await event.respond("⚠️ ᴇɴᴛᴇʀ ᴀ ᴠᴀʟɪᴅ ɴᴜᴍʙᴇʀ (ᴇ.ɢ. 0.2).", buttons=get_cancel())
            state["speed"] = speed
            state["step"] = "qty"
            await event.respond(f"👥 ᴛᴏᴛᴀʟ ᴀᴄᴄᴏᴜɴᴛꜱ: {len(active_clients)}\nꜱᴇʟᴇᴄᴛ ǫᴜᴀɴᴛɪᴛʏ:", buttons=get_qty_buttons())
        elif state["step"] == "custom_qty":
            if not text.isdigit():
                return await event.respond("⚠️ ɴᴜᴍʙᴇʀꜱ ᴏɴʟʏ !", buttons=get_cancel())
            count = min(int(text), len(active_clients))
            await event.delete()
            if state["type"] == "react_view":
                await execute_react_view(event, state, count)
            else:
                await execute_multi_react_view(event, state, count)

    elif state.get("type") == "views":
        if state.get("subtype") == "single":
            if state["step"] == "link":
                post_link = text.strip()
                if not re.search(r"t\.me/([a-zA-Z0-9_]+|\d+)/\d+", post_link) and not re.search(r"t\.me/c/\d+/\d+", post_link):
                    return await event.respond(
                        "❌ ɪɴᴠᴀʟɪᴅ ᴘᴏꜱᴛ ʟɪɴᴋ !\n\n✅ ᴠᴀʟɪᴅ ꜰᴏʀᴍᴀᴛꜱ:\n"
                        "• https://t.me/username/123\n"
                        "• https://t.me/c/123456789/123",
                        buttons=get_cancel(),
                    )
                state["post_link"] = post_link
                state["step"] = "qty"
                await event.respond(
                    f"✅ ᴘᴏꜱᴛ ᴀᴄᴄᴇᴘᴛᴇᴅ !\n\n👥 ᴛᴏᴛᴀʟ ᴀᴄᴄᴏᴜɴᴛꜱ: {len(active_clients)}\n\nʜᴏᴡ ᴍᴀɴʏ ᴀᴄᴄᴏᴜɴᴛꜱ ᴛᴏ ᴜꜱᴇ ꜰᴏʀ ᴠɪᴇᴡꜱ?",
                    buttons=get_views_qty_buttons(),
                )
        elif state.get("subtype") == "batch":
            if state["step"] == "links":
                links = text.strip().splitlines()
                valid_count = sum(
                    1 for link in links
                    if re.search(r"t\.me/([a-zA-Z0-9_]+|\d+)/\d+", link) or re.search(r"t\.me/c/\d+/\d+", link)
                )
                if valid_count == 0:
                    return await event.respond("❌ ɴᴏ ᴠᴀʟɪᴅ ʟɪɴᴋꜱ ꜰᴏᴜɴᴅ !", buttons=get_cancel())
                if valid_count > 20:
                    return await event.respond("⚠️ ᴍᴀx 20 ᴘᴏꜱᴛꜱ ᴀʟʟᴏᴡᴇᴅ !", buttons=get_cancel())
                await event.respond(f"✅ {valid_count} ᴘᴏꜱᴛꜱ ᴀᴄᴄᴇᴘᴛᴇᴅ !\n\n📤 ꜱᴇɴᴅɪɴɢ ᴠɪᴇᴡꜱ ꜰʀᴏᴍ ᴀʟʟ {len(active_clients)} ᴀᴄᴄᴏᴜɴᴛꜱ ...")
                await execute_views_batch(event, text, len(active_clients))
        elif state["step"] == "custom_qty":
            if not text.isdigit():
                return await event.respond("⚠️ ɴᴜᴍʙᴇʀꜱ ᴏɴʟʏ !", buttons=get_cancel())
            count = min(int(text), len(active_clients))
            post_link = state.get("post_link")
            if post_link:
                await execute_views(event, post_link, count)

    # ---------- CHANGE NAME (LIVE INSTANT) ----------
    elif state.get("type") == "change_name":
        if state["step"] == "name":
            text_clean = text.strip()
            if not text_clean:
                return await event.respond("⚠️ ᴘʟᴇᴀꜱᴇ ꜱᴇɴᴅ ᴀ ᴠᴀʟɪᴅ ɴᴀᴍᴇ.", buttons=get_cancel())
            if len(text_clean) > 64:
                return await event.respond("⚠️ ɴᴀᴍᴇ ᴛᴏᴏ ʟᴏɴɢ (ᴍᴀx 64 ᴄʜᴀʀꜱ).", buttons=get_cancel())
            parts = text_clean.split(maxsplit=1)
            first_name = parts[0]
            last_name = parts[1] if len(parts) > 1 else ""
            del task_states[chat_id]
            await execute_change_name(event, first_name, last_name)

    elif state.get("type") == "leave_specific":
        if state["step"] == "link":
            del task_states[chat_id]
            await execute_leave_specific(event, text)


# ======================= MAIN =======================
async def main():
    print("=" * 60)
    print("🤖 ᴄᴏᴍᴘʟᴇᴛᴇ ʙᴏᴛ (ꜱᴍᴀʀᴛ ᴍɪxᴇᴅ + ᴠᴄ ᴇɴɢɪɴᴇ ɪɴᴛᴇɢʀᴀᴛᴇᴅ)")
    print("=" * 60)
    print("✅ ʙᴏᴛ ᴛᴏᴋᴇɴ ʟᴏᴀᴅᴇᴅ")
    print("✅ ᴜꜱɪɴɢ ᴊꜱᴏɴ ꜰɪʟᴇ ꜱᴛᴏʀᴀɢᴇ (ɴᴏ ᴍᴏɴɢᴏᴅʙ ʀᴇǫᴜɪʀᴇᴅ)")
    print(f"👑 ᴏᴡɴᴇʀꜱ: {OWNER_IDS}")
    print("=" * 60)
    await start_saved_clients()
    print(f"✅ ᴀᴄᴛɪᴠᴇ ᴀᴄᴄᴏᴜɴᴛꜱ: {len(active_clients)}")
    print("✅ ʙᴏᴛ ʀᴇᴀᴅʏ !")
    await bot.start(bot_token=BOT_TOKEN)
    await bot.run_until_disconnected()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 ʙᴏᴛ ꜱᴛᴏᴘᴘᴇᴅ ʙʏ ᴜꜱᴇʀ")
    except Exception as e:
        print(f"❌ ꜰᴀᴛᴀʟ ᴇʀʀᴏʀ: {e}")