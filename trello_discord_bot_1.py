import discord
from discord import app_commands
import requests
import asyncio
import calendar as pycal
import io
import re
import time
import uuid
from functools import partial
from datetime import datetime, timedelta
import pytz
from PIL import Image, ImageDraw, ImageFont

# ====== ตั้งค่าตรงนี้ ======
import os
DISCORD_TOKEN     = os.environ["DISCORD_TOKEN"]
TRELLO_API_KEY    = os.environ["TRELLO_API_KEY"]
TRELLO_TOKEN      = os.environ["TRELLO_TOKEN"]
TRELLO_BOARD_ID   = os.environ["TRELLO_BOARD_ID"]
NOTIFY_CHANNEL_ID = int(os.environ["NOTIFY_CHANNEL_ID"])
# ==========================

TRELLO_BASE = "https://api.trello.com/1"
TZ = pytz.timezone("Asia/Bangkok")
NOTIFY_TIMES = [(10, 0), (12, 30), (18, 0)]  # ชั่วโมง, นาที

# ── ข้อมูลสมาชิก (เพิ่ม/แก้ตรงนี้ที่เดียว) ──────────────────────
# ชื่อ = ชื่อที่อยู่ในชื่อลิสต์ Trello ของคนนั้น, id = Discord user ID (4ก เป็นลิสต์กลาง ไม่มี id)
PEOPLE = {
    "โดม":    {"id": 1447848699718799390, "team": "กราฟิก",   "emoji": "🟣", "color": (155, 114, 207), "desc": "Web master, Support"},
    "ไอซ์":   {"id": 1447848730781945876, "team": "กราฟิก",   "emoji": "🟢", "color": (46, 184, 114),  "desc": "Video, Support, Promotion Shopee, General Affairs"},
    "พี":     {"id": 1448155823623639162, "team": "กราฟิก",   "emoji": "🟡", "color": (232, 179, 38),  "desc": "หน้าร้าน, เบรค, ADS, Event BIC, Merchandise, Support"},
    "4ก":     {"id": None,                "team": "กราฟิก",   "emoji": "🔵", "color": (66, 133, 244),  "desc": "งานกลาง / มอบหมายร่วม"},
    "กาย":    {"id": 1519904425672446073, "team": "กราฟิก",   "emoji": "🟠", "color": (240, 134, 52),  "desc": "Grading, Support, Information"},
    "Best":   {"id": 1328671400726106125, "team": "วิดีโอ",    "emoji": "🔴", "color": (230, 70, 70),   "desc": "ทีมวิดีโอ"},
    "Flow":   {"id": 1447853135665303673, "team": "วิดีโอ",    "emoji": "🩵", "color": (40, 170, 190),  "desc": "ทีมวิดีโอ"},
    "ไอเซฟ":  {"id": 1519619421323264070, "team": "วิดีโอ",    "emoji": "🟤", "color": (140, 100, 70),  "desc": "ทีมวิดีโอ"},
    "ทีมคอนเทนต์": {"id": None, "team": "คอนเทนต์", "emoji": "📝", "color": (180, 90, 40), "desc": "งานกลางทีมคอนเทนต์ / มอบหมายร่วม"},
    "ทีมวีดีโอ": {"id": None,             "team": "วิดีโอ",    "emoji": "🎬", "color": (140, 160, 40),  "desc": "งานกลางทีมวิดีโอ / มอบหมายร่วม"},
    "Stamp":  {"id": 1447873476462645350, "team": "คอนเทนต์", "emoji": "🩷", "color": (236, 110, 170), "desc": "ทีมคอนเทนต์"},
    "SATANG": {"id": 1513433329268166726, "team": "คอนเทนต์", "emoji": "⚫", "color": (70, 75, 95),    "desc": "ทีมคอนเทนต์"},
}
AVATARS = {n: p["emoji"] for n, p in PEOPLE.items()}
MEMBERS = list(PEOPLE.keys())
MEMBER_DISCORD_IDS = {n: p["id"] for n, p in PEOPLE.items() if p["id"]}
DISCORD_ID_TO_MEMBER = {v: k for k, v in MEMBER_DISCORD_IDS.items()}

# เซิร์ฟเวอร์ที่ NOVA ใช้งาน: teams = ทีมที่เซิร์ฟเวอร์นั้นเห็น (สรุปงาน/ปฏิทิน/เมนู), notify_channel_id = ช่องแจ้งเตือนตามเวลา
GUILDS = {
    1447896845501337714: {"name": "4ก", "teams": {"กราฟิก"}, "notify_channel_id": NOTIFY_CHANNEL_ID},
    1565566425098100897: {"name": "Digital Multimedia", "teams": {"กราฟิก", "วิดีโอ", "คอนเทนต์"},
                          "notify_channel_id": 1565572815208456252},
}

def scope_names(guild_id) -> list:
    """รายชื่อสมาชิกที่เซิร์ฟเวอร์นี้เห็น (DM หรือเซิร์ฟเวอร์ที่ไม่รู้จัก = เห็นทุกคน)"""
    cfg = GUILDS.get(guild_id)
    teams = cfg["teams"] if cfg else None
    return [n for n, p in PEOPLE.items() if teams is None or p["team"] in teams]

def notify_targets() -> list:
    return [(gid, cfg["notify_channel_id"], scope_names(gid))
            for gid, cfg in GUILDS.items() if cfg.get("notify_channel_id")]

def member_options(names: list, with_desc: bool = False) -> list:
    return [
        discord.SelectOption(label=f"{PEOPLE[n]['emoji']} {n}", value=n,
                             **({"description": PEOPLE[n]["desc"][:100]} if with_desc else {}))
        for n in names
    ]

# ── Trello helpers ────────────────────────────────────────────────
# แคชสั้นๆ กันยิง API ซ้ำตอนหลายคนกดพร้อมกัน / หลาย step ในคำสั่งเดียว
CACHE_TTL = 15  # วินาที
_cache = {"lists": None, "lists_ts": 0.0, "cards": None, "cards_ts": 0.0}

def invalidate_cache():
    _cache["lists_ts"] = 0.0
    _cache["cards_ts"] = 0.0

def _request_with_retry(method: str, url: str, retries: int = 3, **kwargs):
    """เรียก Trello API พร้อม retry เมื่อเจอปัญหาชั่วคราว (network error / 429 / 5xx)"""
    kwargs.setdefault("timeout", 10)
    last_exc = None
    for attempt in range(retries):
        try:
            r = requests.request(method, url, **kwargs)
        except requests.RequestException as e:
            last_exc = e
            if attempt < retries - 1:
                time.sleep(0.5 * (2 ** attempt))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            last_exc = Exception(f"Trello ตอบ {r.status_code}: {r.text[:200]}")
            if attempt < retries - 1:
                time.sleep(0.5 * (2 ** attempt))
            continue
        return r
    raise last_exc

def _get_lists_sync():
    r = _request_with_retry("GET", f"{TRELLO_BASE}/boards/{TRELLO_BOARD_ID}/lists",
                             params={"key": TRELLO_API_KEY, "token": TRELLO_TOKEN})
    return {lst["name"]: lst["id"] for lst in r.json()}

async def get_lists():
    now = asyncio.get_event_loop().time()
    if _cache["lists"] is not None and now - _cache["lists_ts"] < CACHE_TTL:
        return _cache["lists"]
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _get_lists_sync)
    _cache["lists"], _cache["lists_ts"] = result, now
    return result

def _get_all_cards_sync():
    r = _request_with_retry("GET", f"{TRELLO_BASE}/boards/{TRELLO_BOARD_ID}/cards",
                             params={"key": TRELLO_API_KEY, "token": TRELLO_TOKEN})
    return r.json()

async def get_all_cards():
    now = asyncio.get_event_loop().time()
    if _cache["cards"] is not None and now - _cache["cards_ts"] < CACHE_TTL:
        return _cache["cards"]
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _get_all_cards_sync)
    _cache["cards"], _cache["cards_ts"] = result, now
    return result

def _move_card_sync(card_id, list_id):
    r = _request_with_retry("PUT", f"{TRELLO_BASE}/cards/{card_id}",
                             params={"key": TRELLO_API_KEY, "token": TRELLO_TOKEN},
                             json={"idList": list_id})
    return r.json()

async def move_card(card_id, list_id):
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, partial(_move_card_sync, card_id, list_id))
    invalidate_cache()
    return result

async def get_active_cards(names: list = None):
    lists = await get_lists()
    done_ids = {v for k, v in lists.items() if "เสร็จ" in k}
    cards = await get_all_cards()
    list_id_to_name = {v: k for k, v in lists.items()}
    result = {}
    for card in cards:
        if card["idList"] in done_ids:
            continue
        list_name = list_id_to_name.get(card["idList"], "")
        member = next((m for m in MEMBERS if m in list_name), None)
        if not member or (names is not None and member not in names):
            continue
        if member not in result:
            result[member] = []
        due_str = ""
        if card.get("due"):
            try:
                due_dt = datetime.fromisoformat(card["due"].replace("Z", "+00:00"))
                due_local = due_dt.astimezone(TZ)
                due_str = f" *(ครบ {due_local.strftime('%-d/%-m %H:%M')} น.)*"
            except Exception:
                pass
        status = "🔄 กำลังทำ" if "กำลังทำ" in list_name else "📋 รอดำเนินการ"
        raw = parse_assignees(card.get("desc"))
        who = [display_assignee(x) for x in raw]
        who_str = f" 👥 {', '.join(who)}" if len(who) > 1 else ""
        if GROUP_RE.search(card.get("desc") or ""):
            who_str += " 🔗 งานข้ามทีม"
        result[member].append(f"{status} {card['name']}{due_str}{who_str}")
    return result

async def fetch_member_cards(member: str):
    lists = await get_lists()
    done_ids = {v for k, v in lists.items() if "เสร็จ" in k}
    list_id_to_name = {v: k for k, v in lists.items()}
    cards_raw = await get_all_cards()
    cards = []
    for c in cards_raw:
        ln = list_id_to_name.get(c["idList"], "")
        if c["idList"] in done_ids:
            continue
        if member in ln or member in expand_assignees(parse_assignees(c.get("desc"))):
            cards.append({**c, "list_name": ln})
    return cards

# ── discord setup ─────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True  # ต้องเปิด "Message Content Intent" ใน Developer Portal ด้วย
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# ── UI helpers ────────────────────────────────────────────────────
def add_cancel_button(view: discord.ui.View):
    btn = discord.ui.Button(label="❌ ยกเลิก", style=discord.ButtonStyle.danger, row=4)
    async def cb(interaction: discord.Interaction):
        await interaction.response.edit_message(content="❌ ยกเลิกแล้ว", view=None)
    btn.callback = cb
    view.add_item(btn)

def trello_link_button(url: str) -> discord.ui.Button:
    return discord.ui.Button(label="🔗 เปิดใน Trello", url=url, style=discord.ButtonStyle.link)

def sort_by_due(cards: list) -> list:
    return sorted(cards, key=lambda c: c.get("due") or "9999")

def cards_cap_note(total: int, shown: int = 25) -> str:
    return f" (แสดง {shown} จาก {total} งาน เรียงตามความเร่งด่วน)" if total > shown else ""

# ── คนสั่งงาน: เก็บลง desc ของ card แล้ว DM กลับเมื่องานเสร็จ ─────
REQUESTER_RE = re.compile(r"สั่งโดย: <@(\d+)>")

ASSIGNEES_RE = re.compile(r"^ผู้รับผิดชอบ:\s*(.+)$", re.M)
GROUP_RE = re.compile(r"^กลุ่มงาน:\s*(\w+)$", re.M)

def parse_assignees(desc: str) -> list:
    m = ASSIGNEES_RE.search(desc or "")
    return [x.strip() for x in m.group(1).split(",") if x.strip()] if m else []

def expand_assignees(tokens: list) -> list:
    """แปลงโทเคน 'ทีม:กราฟิก' เป็นสมาชิกทุกคนในทีม (ที่มี Discord ID) รวมกับรายบุคคล"""
    out = []
    for t in tokens or []:
        if t.startswith("ทีม:"):
            out += [n for n, p in PEOPLE.items() if p["team"] == t[4:] and p["id"]]
        elif t in PEOPLE:
            out.append(t)
    return list(dict.fromkeys(out))

def display_assignee(token: str) -> str:
    return "ทีม" + token[4:] if token.startswith("ทีม:") else token

def build_desc(note: str, interaction: discord.Interaction = None, assignees: list = None,
               group: str = None) -> str:
    parts = []
    if note:
        parts.append(note)
    if assignees:
        parts.append("ผู้รับผิดชอบ: " + ", ".join(assignees))
    if group:
        parts.append(f"กลุ่มงาน: {group}")
    if interaction is not None:
        u = interaction.user
        parts.append(f"---\nสั่งโดย: <@{u.id}> ({u.display_name})")
    return "\n\n".join(parts)

async def notify_requester_done(card_id: str, card_name: str = None, completer_id: int = None):
    try:
        loop = asyncio.get_running_loop()
        r = await loop.run_in_executor(None, partial(
            _request_with_retry, "GET", f"{TRELLO_BASE}/cards/{card_id}",
            params={"key": TRELLO_API_KEY, "token": TRELLO_TOKEN, "fields": "desc,name"}
        ))
        data = r.json()
        m = REQUESTER_RE.search(data.get("desc") or "")
        if not m:
            return
        uid = int(m.group(1))
        if completer_id is not None and uid == completer_id:
            return  # คนสั่งกดเสร็จเอง ไม่ต้อง DM ตัวเอง
        group_title = None
        gm = GROUP_RE.search(data.get("desc") or "")
        if gm:  # งานข้ามทีม: แจ้งเมื่อการ์ดของทุกทีมเสร็จครบ
            lists = await get_lists()
            done_ids = {v for k, v in lists.items() if "เสร็จ" in k}
            marker = f"กลุ่มงาน: {gm.group(1)}"
            if any(c["id"] != card_id and c["idList"] not in done_ids and marker in (c.get("desc") or "")
                   for c in await get_all_cards()):
                return
            group_title = "✅ งานที่คุณสั่งเสร็จครบทุกทีมแล้ว"
        user = client.get_user(uid) or await client.fetch_user(uid)
        embed = discord.Embed(
            title=group_title or "✅ งานที่คุณสั่งเสร็จเรียบร้อยแล้ว",
            description=card_name or data.get("name", ""),
            color=0x2ECC71, timestamp=datetime.now(TZ)
        )
        await user.send(embed=embed)
    except Exception as e:
        print(f"notify_requester_done error: {e}")

async def find_duplicate_active_card(card_name: str):
    lists = await get_lists()
    done_ids = {v for k, v in lists.items() if "เสร็จ" in k}
    cards = await get_all_cards()
    return next((c for c in cards
                 if c["idList"] not in done_ids and c["name"] == card_name), None)

# ── rate limit สำหรับคนนอกทีม: สร้างได้ไม่เกิน 5 งาน/ชั่วโมง ──────
CARD_RATE_LIMIT = 5
CARD_RATE_WINDOW = 3600  # วินาที
_card_create_log: dict = {}  # user_id -> [เวลาที่สร้างสำเร็จ]

def check_rate_limit(user_id: int) -> bool:
    now = time.time()
    log = [t for t in _card_create_log.get(user_id, []) if now - t < CARD_RATE_WINDOW]
    _card_create_log[user_id] = log
    return len(log) < CARD_RATE_LIMIT

def record_card_creation(user_id: int):
    _card_create_log.setdefault(user_id, []).append(time.time())

# ── /status ───────────────────────────────────────────────────────
@tree.command(name="status", description="ดูสรุปงานของทีมตอนนี้")
async def status_command(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        active = await get_active_cards(scope_names(interaction.guild_id))
        embed = discord.Embed(title="📋 งานที่ยังไม่เสร็จ",
                              color=0x7F77DD,
                              timestamp=datetime.now(TZ))
        if not active:
            embed.description = "ยอดเยี่ยม ไม่มีงานค้างอยู่เลย 🎉"
        else:
            for member, cards in active.items():
                val = "\n".join(f"• {c}" for c in cards) or "ไม่มีงาน"
                embed.add_field(name=f"{AVATARS.get(member,'')} {member} ({len(cards)} งาน)",
                                value=val, inline=False)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ เกิดข้อผิดพลาด: {e}")

class DoneCardButton(discord.ui.DynamicItem[discord.ui.Button],
                     template=r"nova:done:(?P<card_id>[0-9a-f]{24})"):
    """ปุ่ม 'เสร็จแล้ว' บนข้อความแจ้งเตือน deadline — custom_id มี card id จึงกดได้แม้บอทรีสตาร์ท"""
    def __init__(self, card_id: str):
        super().__init__(discord.ui.Button(
            label="✅ เสร็จแล้ว", style=discord.ButtonStyle.success, custom_id=f"nova:done:{card_id}"
        ))
        self.card_id = card_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /):
        return cls(match["card_id"])

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            lists = await get_lists()
            target = next((v for k, v in lists.items() if "เสร็จ" in k), None)
            if not target:
                await interaction.followup.send("❌ ไม่พบลิสต์ 'เสร็จแล้ว' ในบอร์ด", ephemeral=True)
                return
            await move_card(self.card_id, target)
            await notify_requester_done(self.card_id, completer_id=interaction.user.id)
            view = discord.ui.View.from_message(interaction.message, timeout=None)
            for child in view.children:
                if getattr(child, "custom_id", None) == self.item.custom_id:
                    child.disabled = True
                    child.label = "✅ เสร็จแล้ว!"
            await interaction.message.edit(view=view)
        except Exception as e:
            await interaction.followup.send(f"❌ เกิดข้อผิดพลาด: {e}", ephemeral=True)

class DeadlineDoneView(discord.ui.View):
    def __init__(self, card_id: str, card_url: str):
        super().__init__(timeout=None)
        self.add_item(trello_link_button(card_url))
        self.add_item(DoneCardButton(card_id))

# ── background: deadline alert (เช็คทุก 5 นาที) ──────────────────
async def deadline_alert():
    await client.wait_until_ready()
    alerted = set()
    while not client.is_closed():
        try:
            now = datetime.now(TZ)
            cards = await get_all_cards()
            lists = await get_lists()
            done_ids = {v for k, v in lists.items() if "เสร็จ" in k}
            list_id_to_name = {v: k for k, v in lists.items()}

            for card in cards:
                if not card.get("due"):
                    continue
                # ถ้า card เสร็จแล้วให้ reset alerted เผื่อ reopen
                if card["idList"] in done_ids:
                    alerted.discard(card["id"])
                    continue
                if card["id"] in alerted:
                    continue

                due_dt = datetime.fromisoformat(card["due"].replace("Z", "+00:00")).astimezone(TZ)
                diff_hours = (due_dt - now).total_seconds() / 3600

                if 0 < diff_hours <= 24:
                    list_name = list_id_to_name.get(card["idList"], "")
                    owner = next((m for m in MEMBERS if m in list_name), None)
                    member = owner or "ทีม"

                    embed = discord.Embed(
                        title="🚨 ใกล้ถึงกำหนดส่งแล้ว โปรดตรวจสอบงานนี้",
                        color=0xE24B4A, timestamp=now
                    )
                    embed.add_field(name="งาน", value=card["name"], inline=False)
                    embed.add_field(name="ผู้รับผิดชอบ", value=f"{AVATARS.get(member,'')} {member}", inline=True)
                    embed.add_field(name="เหลือเวลา", value=f"⏳ {int(diff_hours)} ชั่วโมงเท่านั้น!", inline=True)
                    embed.add_field(name="ครบกำหนด", value=due_dt.strftime("%-d/%-m/%Y %H:%M น."), inline=True)

                    for _gid, cid, names in notify_targets():
                        if owner is not None and owner not in names:
                            continue  # งานของทีมที่เซิร์ฟเวอร์นี้ไม่เห็น
                        try:
                            channel = client.get_channel(cid) or await client.fetch_channel(cid)
                            await channel.send(embed=embed, view=DeadlineDoneView(card_id=card["id"], card_url=card["url"]))
                        except Exception as e:
                            print(f"deadline send error (channel {cid}): {e}")
                    alerted.add(card["id"])
                    print(f"⚠️ แจ้งเตือน: {card['name']} เหลือ {int(diff_hours)} ชั่วโมง")

                elif diff_hours <= 0:
                    alerted.discard(card["id"])

        except Exception as e:
            print(f"deadline alert error: {e}")
        await asyncio.sleep(300)

async def post_daily_summary(cid: int, names: list, now: datetime, title_prefix: str = ""):
    channel = client.get_channel(cid) or await client.fetch_channel(cid)
    active = await get_active_cards(names)
    embed = discord.Embed(
        title=f"{title_prefix}⏰ สรุปงานประจำวัน — {now.strftime('%-H:%M น.')}",
        color=0xBA7517,
        timestamp=now
    )
    if not active:
        embed.description = "ไม่มีงานค้างเลย ดีเยี่ยม 🎉"
    else:
        for member, cards in active.items():
            val = "\n".join(f"• {c}" for c in cards)
            embed.add_field(
                name=f"{AVATARS.get(member,'')} {member} ({len(cards)} งาน)",
                value=val, inline=False
            )
    await channel.send(embed=embed)

# ── background: daily notify (ใช้ last_sent กันยิงซ้ำ) ───────────
async def daily_notify():
    await client.wait_until_ready()
    last_sent: set = set()
    while not client.is_closed():
        now = datetime.now(TZ)
        today = now.date()
        last_sent = {k for k in last_sent if k[0] == today}  # ล้าง key เก่า
        for h, mn in NOTIFY_TIMES:
            key = (today, h, mn)
            if now.hour == h and now.minute == mn and key not in last_sent:
                last_sent.add(key)
                for _gid, cid, names in notify_targets():
                    try:
                        await post_daily_summary(cid, names, now)
                        print(f"✓ แจ้งเตือนประจำเวลา {h}:{mn:02d} สำเร็จ (channel {cid})")
                    except Exception as e:
                        print(f"notify error (channel {cid}): {e}")
                await send_personal_summaries(now, h, mn)
        await asyncio.sleep(30)

# ══════════════════════════════════════════════════════════════════
# /newcard  FLOW: สมาชิก → หัวข้อ → ชื่องาน+หมายเหตุ → วันที่ → เวลา
# ══════════════════════════════════════════════════════════════════

CATEGORIES = [
    ("🏪", "หน้าร้าน"),
    ("☕", "เบรค"),
    ("🎨", "เกรดดิ้ง"),
    ("🎬", "วีดีโอ"),
    ("🌐", "เว็บไซต์"),
    ("📌", "อื่นๆ"),
]

def make_placements(tokens: list) -> list:
    """หนึ่งการ์ดต่อทีมที่เกี่ยวข้อง: [(ลิสต์ที่วาง, โทเคนผู้รับผิดชอบของการ์ดนั้น)]
    ทีมที่เลือกทั้งทีม → ลิสต์กลางของทีม, คนรายบุคคลของทีมที่ไม่ได้เลือกทั้งทีม → ลิสต์คนแรกของทีมนั้น"""
    teams = [t[4:] for t in tokens if t.startswith("ทีม:")]
    people = [t for t in tokens if not t.startswith("ทีม:")]
    order = TEAM_ORDER + [t for t in {PEOPLE[n]["team"] for n in people} | set(teams) if t not in TEAM_ORDER]
    by_team = {}
    for n in people:
        if PEOPLE[n]["team"] not in teams:
            by_team.setdefault(PEOPLE[n]["team"], []).append(n)
    placements = []
    for t in order:
        if t in teams:
            shared = next((n for n, p in PEOPLE.items() if p["id"] is None and p["team"] == t), None)
            owner = shared or next(n for n, p in PEOPLE.items() if p["team"] == t and p["id"])
            placements.append((owner, [f"ทีม:{t}"]))
        elif t in by_team:
            grp = by_team[t]
            placements.append((grp[0], grp if len(grp) > 1 else []))
    return placements

async def create_task_cards(interaction: discord.Interaction, ctx: dict, card_name: str,
                            due_iso, due_display, cat_icon: str, lists: dict) -> list:
    tokens = ctx.get("assignees") or []
    placements = make_placements(tokens) if tokens else [(ctx["member"], [])]
    for owner, _ in placements:
        if owner not in lists:
            raise Exception(f"ไม่พบลิสต์ของ {owner} ในบอร์ด")
    group = uuid.uuid4().hex[:8] if len(placements) > 1 else None
    loop = asyncio.get_running_loop()
    created = []
    for owner, toks in placements:
        body = {"name": card_name, "idList": lists[owner]}
        if due_iso:
            body["due"] = due_iso
        desc = build_desc(ctx.get("note"), interaction, toks, group)
        if desc:
            body["desc"] = desc
        r = await loop.run_in_executor(None, partial(
            _request_with_retry, "POST", f"{TRELLO_BASE}/cards",
            **{"params": {"key": TRELLO_API_KEY, "token": TRELLO_TOKEN}, "json": body}
        ))
        created.append((owner, toks, r.json()))
    invalidate_cache()
    record_card_creation(interaction.user.id)
    task_text = f"{cat_icon} {ctx.get('category','')} — {ctx['task']}"
    for owner, toks, _card in created:
        await notify_assignment(owner, task_text, due_display, interaction=interaction, assignees=toks or None)
    return created

def build_created_embed(ctx: dict, cat_icon: str, due_display, created: list):
    multi = len(created) > 1
    embed = discord.Embed(title="✅ สร้างงานใน Trello เรียบร้อยแล้ว", color=0x0052cc)
    embed.add_field(name="การ์ดอยู่ในลิสต์" if multi else "สมาชิก",
                    value=", ".join(f"{AVATARS.get(o,'')} {o}" for o, _, _ in created), inline=True)
    if ctx.get("assignees"):
        embed.add_field(name="ผู้รับผิดชอบ", value=", ".join(display_assignee(x) for x in ctx["assignees"]), inline=True)
    embed.add_field(name="หัวข้อ", value=f"{cat_icon} {ctx.get('category','')}", inline=True)
    embed.add_field(name="งาน", value=ctx["task"], inline=False)
    if due_display:
        embed.add_field(name="กำหนดส่ง", value=due_display, inline=True)
    if ctx.get("note"):
        embed.add_field(name="หมายเหตุ", value=ctx["note"], inline=False)
    if multi:
        embed.set_footer(text="สร้างการ์ดแยกให้แต่ละทีม ปิดงานแยกกัน และจะแจ้งคนสั่งเมื่อเสร็จครบทุกทีม")
    link_view = discord.ui.View()
    for owner, _, card in created:
        link_view.add_item(discord.ui.Button(label=f"🔗 {owner}" if multi else "🔗 เปิดใน Trello",
                                             url=card["url"], style=discord.ButtonStyle.link))
    return embed, link_view

# ── Custom modals ────────────────────────────────────────────────
class CustomTimeModal(discord.ui.Modal, title="⏰ กรอกเวลาเอง"):
    เวลา = discord.ui.TextInput(
        label="เวลา", placeholder="เช่น 13:30 หรือ 8:00",
        required=True, max_length=5
    )
    def __init__(self, ctx: dict):
        super().__init__()
        self.ctx = ctx

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.เวลา.value.strip().replace(".", ":")
        try:
            parts = raw.split(":")
            h, mn = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
            if not (0 <= h <= 23 and 0 <= mn <= 59):
                raise ValueError
            time_str = f"{h}:{mn:02d}"
        except Exception:
            await interaction.response.send_message(
                "❌ กรุณากรอกเวลาให้ถูกต้อง เช่น 13:30 หรือ 21.25", ephemeral=True
            )
            return
        await interaction.response.edit_message(
            content=f"**กำลังสร้าง card...** ⏳\n📅 เวลา: **{time_str} น.**",
            view=None
        )
        ctx = self.ctx
        try:
            lists = await get_lists()
            if ctx["member"] not in lists:
                await interaction.followup.send(f"❌ ไม่พบลิสต์ของ {ctx['member']} ในบอร์ด", ephemeral=True)
                return
            due_iso = None
            due_display = None
            if ctx.get("date"):
                d, m, y = ctx["date"]
                h2, mn2 = map(int, time_str.split(":"))
                local_dt = TZ.localize(datetime(y, m, d, h2, mn2))
                due_iso = local_dt.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                due_display = f"{d}/{m}/{y+543} {time_str} น."
            cat_icon = next((ic for ic, nm in CATEGORIES if nm == ctx.get("category", "")), "📌")
            card_name = f"[{ctx.get('category','')}] {ctx['task']}"
            dup = await find_duplicate_active_card(card_name)
            if dup:
                await interaction.edit_original_response(
                    content=f"⚠️ มีงานชื่อ **{card_name}** ค้างอยู่ในบอร์ดแล้ว ยังไม่สร้างซ้ำ\nหากเป็นงานคนละชิ้น กรุณาตั้งชื่อให้ต่างกัน",
                    view=None
                )
                return
            created = await create_task_cards(interaction, ctx, card_name, due_iso, due_display, cat_icon, lists)
            embed, link_view = build_created_embed(ctx, cat_icon, due_display, created)
            await interaction.edit_original_response(content=None, embed=embed, view=link_view)
        except Exception as e:
            await interaction.edit_original_response(content=f"❌ เกิดข้อผิดพลาด: {e}", view=None)

class CustomDateModal(discord.ui.Modal, title="📅 กรอกวันที่เอง"):
    วันที่ = discord.ui.TextInput(
        label="วันที่", placeholder="เช่น 15/6 หรือ 15/6/2569",
        required=True, max_length=12
    )
    def __init__(self, ctx: dict):
        super().__init__()
        self.ctx = ctx

    async def on_submit(self, interaction: discord.Interaction):
        try:
            parts = self.วันที่.value.strip().split("/")
            d, m = int(parts[0]), int(parts[1])
            y = int(parts[2]) - 543 if len(parts) >= 3 else datetime.now(TZ).year
            self.ctx["date"] = (d, m, y)
            view = TimeSelectView(ctx=self.ctx)
            await interaction.response.edit_message(
                content=f"**ขั้นตอน 5/5** — เลือกเวลากำหนดส่ง\n📅 วัน: **{d}/{m}/{y+543}**",
                view=view
            )
        except Exception:
            await interaction.response.send_message(
                "❌ กรุณากรอกวันที่ให้ถูกต้อง เช่น 15/6 หรือ 15/6/2569", ephemeral=True
            )

# ── Step 4: เลือกเวลา ────────────────────────────────────────────
class TimeSelectView(discord.ui.View):
    TIMES = ["9:00", "10:00", "12:00", "15:00", "18:00", "ไม่ระบุ"]

    def __init__(self, ctx: dict):
        super().__init__(timeout=120)
        self.ctx = ctx
        for t in self.TIMES:
            self.add_item(self._make_btn(t))
        btn_custom = discord.ui.Button(label="📝 กรอกเอง", style=discord.ButtonStyle.primary, row=1)
        async def cb_custom(interaction: discord.Interaction):
            await interaction.response.send_modal(CustomTimeModal(ctx=self.ctx))
        btn_custom.callback = cb_custom
        self.add_item(btn_custom)
        add_cancel_button(self)

    def _make_btn(self, label):
        btn = discord.ui.Button(
            label=label,
            style=discord.ButtonStyle.secondary if label != "ไม่ระบุ" else discord.ButtonStyle.danger
        )
        async def cb(interaction: discord.Interaction, _label=label):
            await self._submit(interaction, _label)
        btn.callback = cb
        return btn

    async def on_timeout(self):
        pass  # หมดเวลาเฉยๆ ไม่ต้องทำอะไร

    async def _submit(self, interaction: discord.Interaction, time_str: str):
        await interaction.response.defer()
        ctx = self.ctx
        try:
            lists = await get_lists()
            if ctx["member"] not in lists:
                await interaction.followup.send(f"❌ ไม่พบลิสต์ของ {ctx['member']} ในบอร์ด")
                return

            due_iso = None
            due_display = None
            if ctx.get("date") and time_str != "ไม่ระบุ":
                try:
                    d, m, y = ctx["date"]
                    h, mn = map(int, time_str.split(":"))
                    local_dt = TZ.localize(datetime(y, m, d, h, mn))
                    due_iso = local_dt.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                    due_display = f"{d}/{m}/{y+543} {time_str} น."
                except Exception:
                    pass
            elif ctx.get("date") and time_str == "ไม่ระบุ":
                d, m, y = ctx["date"]
                local_dt = TZ.localize(datetime(y, m, d, 23, 59))
                due_iso = local_dt.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                due_display = f"{d}/{m}/{y+543}"

            cat_icon = next((ic for ic, nm in CATEGORIES if nm == ctx.get("category", "")), "📌")
            card_name = f"[{ctx.get('category','')}] {ctx['task']}"

            dup = await find_duplicate_active_card(card_name)
            if dup:
                await interaction.followup.edit_message(
                    interaction.message.id,
                    content=f"⚠️ มีงานชื่อ **{card_name}** ค้างอยู่ในบอร์ดแล้ว ยังไม่สร้างซ้ำ\nหากเป็นงานคนละชิ้น กรุณาตั้งชื่อให้ต่างกัน",
                    view=None
                )
                return

            created = await create_task_cards(interaction, ctx, card_name, due_iso, due_display, cat_icon, lists)
            embed, link_view = build_created_embed(ctx, cat_icon, due_display, created)
            await interaction.followup.edit_message(interaction.message.id, content=None, embed=embed, view=link_view)
        except Exception as e:
            await interaction.followup.send(f"❌ เกิดข้อผิดพลาด: {e}")

# ── Step 3: เลือกวันที่ ──────────────────────────────────────────
class DateSelectView(discord.ui.View):
    def __init__(self, ctx: dict):
        super().__init__(timeout=120)
        self.ctx = ctx
        now = datetime.now(TZ)
        labels = ["วันนี้", "พรุ่งนี้", "+2 วัน", "+3 วัน", "+7 วัน", "ไม่ระบุ"]
        deltas = [0, 1, 2, 3, 7, None]
        for label, delta in zip(labels, deltas):
            btn = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary if delta is not None else discord.ButtonStyle.danger
            )
            if delta is not None:
                target = now + timedelta(days=delta)
                date_val = (target.day, target.month, target.year)
            else:
                date_val = None
            async def cb(interaction, _label=label, _date=date_val):
                await self._pick(interaction, _label, _date)
            btn.callback = cb
            self.add_item(btn)
        btn_custom = discord.ui.Button(label="📝 กรอกเอง", style=discord.ButtonStyle.primary, row=1)
        async def cb_custom(interaction: discord.Interaction):
            await interaction.response.send_modal(CustomDateModal(ctx=self.ctx))
        btn_custom.callback = cb_custom
        self.add_item(btn_custom)
        add_cancel_button(self)

    async def on_timeout(self):
        pass

    async def _pick(self, interaction: discord.Interaction, label: str, date_val):
        self.ctx["date"] = date_val
        view = TimeSelectView(ctx=self.ctx)
        suffix = f" ({date_val[0]}/{date_val[1]})" if date_val else ""
        await interaction.response.edit_message(
            content=f"**ขั้นตอน 5/5** — เลือกเวลากำหนดส่ง\n📅 วัน: **{label}{suffix}**",
            view=view
        )

# ── Step 2: Modal กรอกชื่องาน ────────────────────────────────────
class CardDetailModal(discord.ui.Modal, title="✏️ รายละเอียดงาน"):
    งาน = discord.ui.TextInput(
        label="ชื่องาน", placeholder="เช่น ภาพโปรโมชั่น Payday",
        required=True, max_length=200
    )
    หมายเหตุ = discord.ui.TextInput(
        label="หมายเหตุ (ไม่บังคับ)", style=discord.TextStyle.paragraph,
        placeholder="รายละเอียดเพิ่มเติม...", required=False, max_length=500
    )

    def __init__(self, ctx: dict):
        super().__init__()
        self.ctx = ctx

    async def on_submit(self, interaction: discord.Interaction):
        self.ctx["task"] = self.งาน.value
        self.ctx["note"] = self.หมายเหตุ.value.strip()
        view = DateSelectView(ctx=self.ctx)
        await interaction.response.edit_message(
            content=f"**ขั้นตอน 4/5** — เลือกวันกำหนดส่ง\n📋 งาน: **{self.งาน.value}**",
            view=view
        )

# ── Step 1b: เลือกหัวข้อ ─────────────────────────────────────────
class CategorySelect(discord.ui.Select):
    def __init__(self, member: str, assignees: list = None):
        options = [
            discord.SelectOption(label=f"{ic} {nm}", value=nm)
            for ic, nm in CATEGORIES
        ]
        super().__init__(placeholder="เลือกหัวข้องาน...", options=options)
        self.member = member
        self.assignees = assignees or []

    async def callback(self, interaction: discord.Interaction):
        ctx = {"member": self.member, "category": self.values[0], "assignees": self.assignees}
        await interaction.response.send_modal(CardDetailModal(ctx=ctx))

class CategorySelectView(discord.ui.View):
    def __init__(self, member: str, assignees: list = None):
        super().__init__(timeout=60)
        self.add_item(CategorySelect(member=member, assignees=assignees))
        add_cancel_button(self)

    async def on_timeout(self):
        pass

# ── Step 1: เลือกสมาชิก ─────────────────────────────────────────
class MemberSelect(discord.ui.Select):
    def __init__(self, names: list = None):
        super().__init__(placeholder="เลือกสมาชิกที่รับผิดชอบ...",
                         options=member_options(names or MEMBERS, with_desc=True))

    async def callback(self, interaction: discord.Interaction):
        member = self.values[0]
        view = CategorySelectView(member=member)
        await interaction.response.edit_message(
            content=f"**ขั้นตอน 2/5** — เลือกหัวข้องาน\n👤 สมาชิก: **{member}**",
            view=view
        )

class AssigneeSelectView(discord.ui.View):
    """เลือกผู้รับผิดชอบหลายคน: เลือกทั้งทีม และ/หรือรายบุคคลผสมกันได้ (ข้ามทีมได้)"""
    def __init__(self, names: list = None):
        super().__init__(timeout=180)
        self.names = names or MEMBERS
        self.people = [n for n in self.names if PEOPLE[n]["id"]]
        self.teams = list(team_groups(self.people).keys())
        self.sel_teams, self.sel_people = [], []
        self._render()

    def _render(self):
        self.clear_items()
        team_opts = [discord.SelectOption(label=f"{TEAM_ICONS.get(t, '👥')} ทั้งทีม{t}", value=t,
                                          description="ทุกคนในทีมได้รับงาน", default=t in self.sel_teams)
                     for t in self.teams]
        ts = discord.ui.Select(placeholder="เลือกทั้งทีม (เลือกได้หลายทีม)...", min_values=0,
                               max_values=len(team_opts), options=team_opts, row=0)
        ts.callback = self._on_teams
        self.add_item(ts)
        people_opts = [discord.SelectOption(label=f"{PEOPLE[n]['emoji']} {n}", value=n,
                                            description=f"ทีม{PEOPLE[n]['team']}", default=n in self.sel_people)
                       for n in self.people[:25]]
        ps = discord.ui.Select(placeholder="เลือกรายบุคคล (เลือกได้หลายคน ข้ามทีมได้)...", min_values=0,
                               max_values=len(people_opts), options=people_opts, row=1)
        ps.callback = self._on_people
        self.add_item(ps)
        nxt = discord.ui.Button(label="ถัดไป ▶", style=discord.ButtonStyle.primary, row=2)
        nxt.callback = self._on_next
        self.add_item(nxt)
        add_cancel_button(self)

    async def _on_teams(self, interaction: discord.Interaction):
        chosen = set(interaction.data.get("values", []))
        self.sel_teams = [t for t in self.teams if t in chosen]
        self._render()
        await interaction.response.edit_message(view=self)

    async def _on_people(self, interaction: discord.Interaction):
        chosen = set(interaction.data.get("values", []))
        self.sel_people = [n for n in self.people if n in chosen]
        self._render()
        await interaction.response.edit_message(view=self)

    async def _on_next(self, interaction: discord.Interaction):
        if not self.sel_teams and not self.sel_people:
            await interaction.response.send_message("เลือกทีมหรือคนอย่างน้อย 1 อย่างก่อนนะ", ephemeral=True)
            return
        if not self.sel_teams and len(self.sel_people) == 1:  # คนเดียว = งานปกติ
            owner, tokens = self.sel_people[0], []
            who, where = owner, owner
        else:
            tokens = [f"ทีม:{t}" for t in self.sel_teams] + self.sel_people
            pl = make_placements(tokens)
            owner = pl[0][0]
            who = ", ".join(display_assignee(t) for t in tokens)
            where = ", ".join(o for o, _ in pl)
        extra = "\n🔗 สร้างการ์ดแยกให้แต่ละลิสต์ ปิดงานแยกกัน" if len(tokens) and len(make_placements(tokens)) > 1 else ""
        await interaction.response.edit_message(
            content=(f"**ขั้นตอน 2/5** — เลือกหัวข้องาน\n👥 ผู้รับผิดชอบ: **{who}**\n"
                     f"📌 การ์ดจะอยู่ในลิสต์: **{where}**{extra}"),
            view=CategorySelectView(member=owner, assignees=tokens)
        )

TEAM_ORDER = ["กราฟิก", "วิดีโอ", "คอนเทนต์"]
TEAM_ICONS = {"กราฟิก": "🎨", "วิดีโอ": "🎬", "คอนเทนต์": "📝"}

def team_groups(names: list) -> dict:
    """จัดกลุ่มรายชื่อตามทีม (เรียง กราฟิก → วิดีโอ → คอนเทนต์)"""
    groups = {}
    for n in names:
        groups.setdefault(PEOPLE[n]["team"], []).append(n)
    ordered = {t: groups[t] for t in TEAM_ORDER if t in groups}
    ordered.update({t: v for t, v in groups.items() if t not in ordered})
    return ordered

class TeamPickView(discord.ui.View):
    """ขั้นเลือกทีมก่อน แล้วค่อยแสดงรายชื่อคนในทีมนั้น (ใช้เมื่อเซิร์ฟเวอร์เห็นหลายทีม)"""
    def __init__(self, names: list, make_view, intro: str, multi_names: list = None):
        super().__init__(timeout=60)
        self.groups, self.make_view, self.intro = team_groups(names), make_view, intro
        self.multi_names = multi_names
        options = [
            discord.SelectOption(label=f"{TEAM_ICONS.get(t, '👥')} ทีม{t}", value=t,
                                 description=f"{len(ns)} รายการ: " + ", ".join(ns)[:80])
            for t, ns in self.groups.items()
        ]
        sel = discord.ui.Select(placeholder="เลือกทีมก่อน...", options=options)
        sel.callback = self._on_team
        self.add_item(sel)
        if multi_names:
            multi = discord.ui.Button(label="👥 หลายคน / หลายทีม", style=discord.ButtonStyle.secondary, row=1)
            multi.callback = self._on_multi
            self.add_item(multi)
        add_cancel_button(self)

    async def _on_multi(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            content="**ขั้นตอน 1/5** — เลือกผู้รับผิดชอบ (ทั้งทีม และ/หรือรายบุคคลผสมกันได้)",
            view=AssigneeSelectView(self.multi_names)
        )

    async def _on_team(self, interaction: discord.Interaction):
        team = interaction.data["values"][0]
        await interaction.response.edit_message(
            content=f"{self.intro}\n{TEAM_ICONS.get(team, '👥')} ทีม**{team}**",
            view=self.make_view(self.groups[team])
        )

def member_flow_view(names: list, make_view, intro: str, multi: bool = False):
    """เซิร์ฟเวอร์ที่เห็นหลายทีมเริ่มที่การเลือกทีม ส่วนทีมเดียวไปเลือกคนได้เลย"""
    if len(team_groups(names)) > 1:
        return TeamPickView(names, make_view, intro, multi_names=names if multi else None)
    return make_view(names)

class MemberSelectView(discord.ui.View):
    def __init__(self, names: list = None, all_names: list = None):
        super().__init__(timeout=60)
        self.names = all_names or names  # ปุ่ม "หลายคน" เลือกข้ามทีมได้
        self.add_item(MemberSelect(names))
        multi = discord.ui.Button(label="👥 หลายคน / หลายทีม", style=discord.ButtonStyle.secondary, row=1)
        multi.callback = self._on_multi
        self.add_item(multi)
        add_cancel_button(self)

    async def _on_multi(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            content="**ขั้นตอน 1/5** — เลือกผู้รับผิดชอบ (เลือกได้หลายคน)", view=AssigneeSelectView(self.names)
        )

    async def on_timeout(self):
        pass

# ── เปลี่ยนสถานะงานแบบ dropdown ────────────────────────────────
class TaskStatusSelect(discord.ui.Select):
    def __init__(self, member: str, cards: list):
        options = [
            discord.SelectOption(
                label=c["name"][:80],
                value=c["id"],
                description=c.get("list_name", "")[:50]
            ) for c in sort_by_due(cards)[:25]
        ]
        super().__init__(placeholder="เลือกงานที่ต้องการเปลี่ยนสถานะ...", options=options)
        self.member = member

    async def callback(self, interaction: discord.Interaction):
        card_id = self.values[0]
        card_name = next(o.label for o in self.options if o.value == card_id)
        view = StatusChoiceView(card_id=card_id, card_name=card_name)
        await interaction.response.edit_message(
            content=f"**{card_name}**\nเลือกสถานะใหม่:",
            view=view
        )

class StatusChoiceView(discord.ui.View):
    def __init__(self, card_id: str, card_name: str):
        super().__init__(timeout=60)
        self.card_id = card_id
        self.card_name = card_name

    async def on_timeout(self):
        pass

    @discord.ui.button(label="✅ ทำเสร็จแล้ว", style=discord.ButtonStyle.success)
    async def btn_done(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        try:
            lists = await get_lists()
            target = next((v for k, v in lists.items() if "เสร็จ" in k), None)
            if not target:
                await interaction.followup.send("❌ ไม่พบลิสต์ 'เสร็จแล้ว' ในบอร์ด")
                return
            await move_card(self.card_id, target)
            await notify_requester_done(self.card_id, self.card_name, completer_id=interaction.user.id)
            await interaction.followup.edit_message(
                interaction.message.id,
                content=f"✅ **{self.card_name}** → เสร็จสมบูรณ์แล้ว 🎉",
                view=None
            )
        except Exception as e:
            await interaction.followup.send(f"❌ เกิดข้อผิดพลาด: {e}")

    @discord.ui.button(label="❌ ยกเลิก", style=discord.ButtonStyle.danger, row=4)
    async def btn_cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="❌ ยกเลิกแล้ว", view=None)

class TaskSelectView(discord.ui.View):
    def __init__(self, member: str, cards: list):
        super().__init__(timeout=60)
        self.add_item(TaskStatusSelect(member=member, cards=cards))
        add_cancel_button(self)

    async def on_timeout(self):
        pass

class MemberForStatusSelect(discord.ui.Select):
    def __init__(self, names: list = None):
        super().__init__(placeholder="เลือกสมาชิก...", options=member_options(names or MEMBERS))

    async def callback(self, interaction: discord.Interaction):
        member = self.values[0]
        lists = await get_lists()
        done_ids = {v for k, v in lists.items() if "เสร็จ" in k}
        list_id_to_name = {v: k for k, v in lists.items()}

        # FIX: กรองเฉพาะ list ของ member คนนั้น ไม่ใช่ทุกคน
        all_cards = await get_all_cards()
        cards = [
            {**c, "list_name": list_id_to_name.get(c["idList"], "")}
            for c in all_cards
            if member in list_id_to_name.get(c["idList"], "")
            if c["idList"] not in done_ids
        ]
        if not cards:
            await interaction.response.edit_message(
                content=f"✅ {member} ไม่มีงานค้างแล้ว เยี่ยมมาก!", view=None
            )
            return
        view = TaskSelectView(member=member, cards=cards)
        note = cards_cap_note(len(cards))
        await interaction.response.edit_message(
            content=f"**งานของ {member}** — เลือกงานที่จะเปลี่ยนสถานะ:{note}",
            view=view
        )

class MemberForStatusView(discord.ui.View):
    def __init__(self, names: list = None):
        super().__init__(timeout=60)
        self.add_item(MemberForStatusSelect(names))
        add_cancel_button(self)

    async def on_timeout(self):
        pass

@tree.command(name="changestatus", description="เปลี่ยนสถานะงานแบบเลือก dropdown")
async def changestatus_command(interaction: discord.Interaction):
    if not await require_team_member(interaction):
        return
    intro = "**🔄 เปลี่ยนสถานะงาน** — เลือกสมาชิกที่ต้องการอัปเดตสถานะ"
    view = member_flow_view(scope_names(interaction.guild_id), lambda ns: MemberForStatusView(ns), intro)
    await interaction.response.send_message(intro, view=view, ephemeral=True)

@tree.command(name="newcard", description="สร้างงานใหม่แบบ step-by-step")
async def newcard_command(interaction: discord.Interaction):
    if not is_team_member(interaction) and not check_rate_limit(interaction.user.id):
        await interaction.response.send_message(
            f"⏳ คุณสร้างงานครบ {CARD_RATE_LIMIT} งานในชั่วโมงนี้แล้ว กรุณารอสักครู่แล้วลองใหม่",
            ephemeral=True
        )
        return
    intro = "**📋 สร้างงานใหม่** — มอบหมายงานนี้ให้ใคร?"
    names = scope_names(interaction.guild_id)
    view = member_flow_view(names, lambda ns: MemberSelectView(ns, all_names=names), intro, multi=True)
    await interaction.response.send_message(intro, view=view, ephemeral=True)

# ── /mywork: ดูงานตัวเอง + ปุ่มกดเสร็จ ──────────────────────────
QUICKDONE_ID = "nova:quickdone"

def _quickdone_pairs(message) -> list:
    """อ่านตัวเลือกที่ยังเหลืออยู่จากเมนูบนข้อความ (label, card_id)"""
    for row in getattr(message, "components", None) or []:
        for comp in getattr(row, "children", []):
            if getattr(comp, "custom_id", None) == QUICKDONE_ID:
                return [(o.label, o.value) for o in comp.options]
    return []

class QuickDoneSelect(discord.ui.Select):
    def __init__(self, cards: list):
        options = [
            discord.SelectOption(label=c["name"][:90], value=c["id"])
            for c in sort_by_due(cards)[:25]
        ]
        super().__init__(placeholder="เลือกงานที่ทำเสร็จแล้ว...", options=options, custom_id=QUICKDONE_ID)

    async def callback(self, interaction: discord.Interaction):
        card_id = self.values[0]
        private = bool(interaction.message and interaction.message.flags.ephemeral)
        await interaction.response.defer()
        try:
            card_name = next((c["name"] for c in await get_all_cards() if c["id"] == card_id), card_id)
            lists = await get_lists()
            done_list = next((v for k, v in lists.items() if "เสร็จ" in k), None)
            await move_card(card_id, done_list)
            await notify_requester_done(card_id, card_name, completer_id=interaction.user.id)
            if private:  # /mywork ส่วนตัว: แทนที่ด้วยข้อความยืนยัน
                await interaction.edit_original_response(
                    content=f"✅ **{card_name}** → เสร็จสมบูรณ์แล้ว 🎉", view=None
                )
                return
            # ข้อความสาธารณะ (สรุปประจำวัน/overdue): เอางานที่เสร็จออกจากเมนู คนอื่นยังเลือกต่อได้
            remaining = [{"id": v, "name": l} for l, v in _quickdone_pairs(interaction.message) if v != card_id]
            await interaction.edit_original_response(
                view=QuickDoneView(remaining, persistent=True) if remaining else None
            )
            await interaction.followup.send(f"✅ **{interaction.user.display_name}** ทำ **{card_name}** เสร็จแล้ว 🎉")
        except Exception as e:
            await interaction.followup.send(f"❌ เกิดข้อผิดพลาด: {e}", ephemeral=True)

class QuickDoneView(discord.ui.View):
    def __init__(self, cards: list, persistent: bool = False):
        # ข้อความสาธารณะต้องกดได้ตลอด (persistent) ส่วน /mywork ส่วนตัวหมดอายุใน 2 นาที
        super().__init__(timeout=None if persistent else 120)
        if cards:
            self.add_item(QuickDoneSelect(cards))

MEMBER_CHOICES = [app_commands.Choice(name=n, value=n) for n in PEOPLE]

@tree.command(name="mywork", description="ดูงานของฉัน พร้อมกดเสร็จได้เลย")
@app_commands.describe(ชื่อ="เลือกชื่อ (เว้นว่างไว้ = ดูของตัวเองอัตโนมัติ)")
@app_commands.choices(ชื่อ=MEMBER_CHOICES)
async def mywork_command(interaction: discord.Interaction, ชื่อ: app_commands.Choice[str] = None):
    await interaction.response.defer(ephemeral=True)
    if ชื่อ is not None:
        member = ชื่อ.value
    else:
        member = DISCORD_ID_TO_MEMBER.get(interaction.user.id)
        if member is None:
            await interaction.followup.send(
                "❌ ไม่พบข้อมูลผู้ใช้ กรุณาระบุพารามิเตอร์ 'ชื่อ' ด้วยตนเอง", ephemeral=True
            )
            return
    try:
        cards = await fetch_member_cards(member)
        embed = discord.Embed(
            title=f"{AVATARS.get(member,'')} งานของ {member} ที่ยังค้างอยู่",
            color=0x7F77DD
        )
        if not cards:
            embed.description = "ไม่มีงานค้างเลย ดีเยี่ยม 🎉"
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        lines = []
        for c in cards:
            due = ""
            if c.get("due"):
                try:
                    dd = datetime.fromisoformat(c["due"].replace("Z", "+00:00")).astimezone(TZ)
                    due = f" *(ครบ {dd.strftime('%-d/%-m %H:%M')} น.)*"
                except Exception:
                    pass
            status = "🔄" if "กำลังทำ" in c["list_name"] else "📋"
            lines.append(f"{status} {c['name']}{due}")
        embed.description = "\n".join(f"• {l}" for l in lines)
        if len(cards) > 25:
            embed.set_footer(text=f"เมนูเลือกกดเสร็จด้านล่างแสดงได้ 25 จาก {len(cards)} งาน เรียงตามความเร่งด่วน")
        view = QuickDoneView(cards)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ เกิดข้อผิดพลาด: {e}", ephemeral=True)

# ── DM สรุปงานตอนเช้า ─────────────────────────────────────────────
MORNING_DM_TIME = (10, 0)

# ── การมอบหมายงานใหม่: DM แจ้งทันที (ลิสต์กลางอย่าง 4ก/ทีมวีดีโอ = DM ทุกคนในทีมนั้นแยกกัน) ────
async def notify_assignment(member: str, task_text: str, due_display: str = None,
                             interaction: discord.Interaction = None, assignees: list = None):
    shared = PEOPLE.get(member)
    if assignees:  # เลือกผู้รับผิดชอบหลายคน: DM ทุกคนที่ถูกเลือก
        targets = [n for n in expand_assignees(assignees) if n in MEMBER_DISCORD_IDS]
    elif shared and shared["id"] is None:  # ลิสต์กลาง: ทุกคนในทีม
        targets = [n for n, p in PEOPLE.items() if p["team"] == shared["team"] and p["id"]]
    else:
        targets = [member]
    for name in targets:
        uid = MEMBER_DISCORD_IDS.get(name)
        if not uid:
            continue
        try:
            user = client.get_user(uid) or await client.fetch_user(uid)
            embed = discord.Embed(
                title=f"📥 มีงานใหม่มอบหมายให้ {AVATARS.get(name,'')} {name}!",
                description=task_text,
                color=0x0052cc, timestamp=datetime.now(TZ)
            )
            if due_display:
                embed.add_field(name="กำหนดส่ง", value=due_display, inline=True)
            if assignees and (len(assignees) > 1 or assignees[0].startswith("ทีม:")):
                embed.add_field(name="ผู้รับผิดชอบร่วม", value=", ".join(display_assignee(x) for x in assignees), inline=True)
            if interaction is not None:
                creator = interaction.user
                embed.add_field(name="สร้างโดย", value=creator.mention, inline=True)
                roles = getattr(creator, "roles", None)
                if roles:
                    role_names = [r.name for r in roles if r.name != "@everyone"]
                    if role_names:
                        embed.add_field(name="บทบาท", value=", ".join(role_names), inline=True)
                channel = interaction.channel
                if channel is not None:
                    channel_display = getattr(channel, "mention", str(channel))
                    embed.add_field(name="ช่องที่แจ้ง", value=channel_display, inline=True)
            await user.send(embed=embed)
        except Exception as e:
            print(f"notify_assignment error ({name}): {e}")

# ── จำกัดสิทธิ์: /editcard, /changestatus ใช้ได้เฉพาะสมาชิกแผนก (ทุกคนในตาราง PEOPLE) ────
def is_team_member(interaction: discord.Interaction) -> bool:
    return interaction.user.id in MEMBER_DISCORD_IDS.values()

async def require_team_member(interaction: discord.Interaction) -> bool:
    if not is_team_member(interaction):
        await interaction.response.send_message(
            "❌ คำสั่งนี้ใช้ได้เฉพาะสมาชิกในแผนกเท่านั้น", ephemeral=True
        )
        return False
    return True

async def send_personal_summaries(now: datetime, h: int, mn: int, test: bool = False):
    """DM สรุปงานของแต่ละคน (เฉพาะงานของคนนั้น) พร้อมกับรอบสรุปในช่อง"""
    morning = (h, mn) == MORNING_DM_TIME
    for member, uid in MEMBER_DISCORD_IDS.items():
        try:
            cards = await fetch_member_cards(member)
            for shared_name, sp in PEOPLE.items():  # งานกลางของทีม (ลิสต์ที่ไม่มี Discord ID)
                if sp["id"] is None and sp["team"] == PEOPLE[member]["team"]:
                    cards += await fetch_member_cards(shared_name)
            seen_ids = set()
            cards = sort_by_due([c for c in cards if not (c["id"] in seen_ids or seen_ids.add(c["id"]))])
            if not cards and not morning and not test:
                continue  # รอบสายไม่รบกวนคนที่ไม่มีงานค้าง
            lines, overdue_n = [], 0
            for c in cards:
                due, overdue = "", False
                if c.get("due"):
                    try:
                        dd = datetime.fromisoformat(c["due"].replace("Z", "+00:00")).astimezone(TZ)
                        due = f" *(ครบ {dd.strftime('%-d/%-m %H:%M')} น.)*"
                        overdue = dd < now
                    except Exception:
                        pass
                overdue_n += overdue
                status = "⚠️" if overdue else ("🔄" if "กำลังทำ" in c["list_name"] else "📋")
                lines.append(f"{status} {c['name']}{due}")
            text = "\n".join(lines)
            if len(text) > 3900:
                text = text[:3900].rsplit("\n", 1)[0] + "\n…"
            title = (f"☀️ สวัสดีตอนเช้า {member} มาดูงานวันนี้กัน" if morning and not test
                     else f"⏰ สรุปงานของคุณ — {now.strftime('%-H:%M น.')}")
            if test:
                title = "🧪 ทดสอบระบบ — " + title
            embed = discord.Embed(
                title=title, description=text or "ไม่มีงานค้างเลย ดีเยี่ยม 🎉",
                color=0xBA7517, timestamp=now
            )
            foot = f"{len(cards)} งานที่ยังไม่เสร็จ"
            if overdue_n:
                foot += f" • เลยกำหนด {overdue_n} งาน"
            embed.set_footer(text=foot + " • ดูเพิ่มด้วย /mywork")
            user = client.get_user(uid) or await client.fetch_user(uid)
            await user.send(embed=embed)
            print(f"✓ ส่ง DM สรุปให้ {member} แล้ว ({h}:{mn:02d})")
        except Exception as e:
            print(f"personal summary error ({member}): {e}")

# ══════════════════════════════════════════════════════════════════
# /overdue — โชว์งานที่เลยกำหนดแล้วยังไม่เสร็จ
# ══════════════════════════════════════════════════════════════════

@tree.command(name="overdue", description="ดูงานที่เลยกำหนดแล้วแต่ยังไม่เสร็จ")
async def overdue_command(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        lists = await get_lists()
        done_ids = {v for k, v in lists.items() if "เสร็จ" in k}
        list_id_to_name = {v: k for k, v in lists.items()}
        cards = await get_all_cards()
        now = datetime.now(TZ)

        names = scope_names(interaction.guild_id)
        overdue_items = []
        for card in cards:
            if card["idList"] in done_ids:
                continue
            if not card.get("due"):
                continue
            due_dt = datetime.fromisoformat(card["due"].replace("Z", "+00:00")).astimezone(TZ)
            if due_dt >= now:
                continue
            list_name = list_id_to_name.get(card["idList"], "")
            member = next((m for m in MEMBERS if m in list_name), "?")
            if member != "?" and member not in names:
                continue
            diff = now - due_dt
            overdue_items.append({
                "card": card,
                "member": member,
                "due_dt": due_dt,
                "days": diff.days,
                "hours": int(diff.total_seconds() / 3600),
            })

        overdue_items.sort(key=lambda it: it["hours"], reverse=True)

        embed = discord.Embed(
            title="⚠️ งานที่เลยกำหนดส่งแล้ว",
            color=0xFF0000, timestamp=now
        )
        if not overdue_items:
            embed.description = "ไม่มีงานที่เลยกำหนดส่ง ดีเยี่ยม 🎉"
        else:
            by_member: dict = {}
            for item in overdue_items:
                by_member.setdefault(item["member"], []).append(item)
            for member, items in by_member.items():
                lines = []
                for item in items:
                    late = f"{item['days']} วัน" if item["days"] >= 1 else f"{item['hours']} ชม."
                    lines.append(f"• ⚠️ {item['card']['name']} *(เลยมา {late})*")
                embed.add_field(
                    name=f"{AVATARS.get(member,'')} {member} ({len(items)} งาน)",
                    value="\n".join(lines), inline=False
                )
            if len(overdue_items) > 25:
                embed.set_footer(text=f"เมนูเลือกกดเสร็จด้านล่างแสดงได้ 25 จาก {len(overdue_items)} งาน เรียงจากเลยกำหนดมากสุด")
            view = QuickDoneView([item["card"] for item in overdue_items], persistent=True)
            await interaction.followup.send(embed=embed, view=view)
            return
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ เกิดข้อผิดพลาด: {e}")

# ══════════════════════════════════════════════════════════════════
# /search — ค้นหา card จากชื่อ/หมายเหตุ ข้ามทุก list
# ══════════════════════════════════════════════════════════════════

@tree.command(name="search", description="ค้นหา card จากชื่อหรือหมายเหตุ")
@app_commands.describe(คำค้น="คำที่จะค้นหาในชื่อ/หมายเหตุของ card")
async def search_command(interaction: discord.Interaction, คำค้น: str):
    await interaction.response.defer()
    try:
        lists = await get_lists()
        list_id_to_name = {v: k for k, v in lists.items()}
        cards = await get_all_cards()
        keyword = คำค้น.strip().lower()
        names = scope_names(interaction.guild_id)
        matches = [
            c for c in cards
            if (keyword in c.get("name", "").lower() or keyword in (c.get("desc") or "").lower())
            and _member_of_list(list_id_to_name.get(c["idList"], "")) in (None, *names)
        ]
        matches = sort_by_due(matches)

        embed = discord.Embed(
            title=f"🔍 ผลค้นหา: \"{คำค้น}\"",
            color=0x7F77DD, timestamp=datetime.now(TZ)
        )
        if not matches:
            embed.description = "ไม่พบ card ที่ตรงกับคำค้นนี้"
        else:
            lines = []
            for c in matches[:25]:
                list_name = list_id_to_name.get(c["idList"], "")
                status = "✅" if "เสร็จ" in list_name else ("🔄" if "กำลังทำ" in list_name else "📋")
                due = ""
                if c.get("due"):
                    try:
                        dd = datetime.fromisoformat(c["due"].replace("Z", "+00:00")).astimezone(TZ)
                        due = f" *(ครบ {dd.strftime('%-d/%-m %H:%M')} น.)*"
                    except Exception:
                        pass
                lines.append(f"{status} **{c['name']}** — {list_name}{due}")
            embed.description = "\n".join(lines)
            if len(matches) > 25:
                embed.set_footer(text=f"แสดง 25 จาก {len(matches)} ผลลัพธ์ เรียงตามความเร่งด่วน")
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ เกิดข้อผิดพลาด: {e}")

# ══════════════════════════════════════════════════════════════════
# /editcard  FLOW: สมาชิก → card → เลือกแก้อะไร → กรอกค่าใหม่
# ══════════════════════════════════════════════════════════════════

# ── Edit modals ──────────────────────────────────────────────────
class EditNameModal(discord.ui.Modal, title="✏️ แก้ชื่องาน"):
    ชื่อใหม่ = discord.ui.TextInput(label="ชื่องานใหม่", required=True, max_length=200)

    def __init__(self, card_id: str, card_name: str):
        super().__init__()
        self.card_id = card_id
        self.card_name = card_name
        self.ชื่อใหม่.default = card_name

    async def on_submit(self, interaction: discord.Interaction):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, partial(
            _request_with_retry, "PUT", f"{TRELLO_BASE}/cards/{self.card_id}",
            **{"params": {"key": TRELLO_API_KEY, "token": TRELLO_TOKEN},
               "json": {"name": self.ชื่อใหม่.value}}
        ))
        invalidate_cache()
        await interaction.response.edit_message(
            content=f"✅ แก้ชื่อแล้ว!\n~~{self.card_name}~~ → **{self.ชื่อใหม่.value}**",
            view=None
        )

class EditNoteModal(discord.ui.Modal, title="📝 แก้หมายเหตุ"):
    หมายเหตุใหม่ = discord.ui.TextInput(
        label="หมายเหตุใหม่", style=discord.TextStyle.paragraph,
        required=False, max_length=500
    )

    def __init__(self, card_id: str, current_desc: str):
        super().__init__()
        self.card_id = card_id
        self.หมายเหตุใหม่.default = current_desc

    async def on_submit(self, interaction: discord.Interaction):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, partial(
            _request_with_retry, "PUT", f"{TRELLO_BASE}/cards/{self.card_id}",
            **{"params": {"key": TRELLO_API_KEY, "token": TRELLO_TOKEN},
               "json": {"desc": self.หมายเหตุใหม่.value}}
        ))
        invalidate_cache()
        await interaction.response.edit_message(content="✅ แก้หมายเหตุแล้ว!", view=None)

class EditCustomDateModal(discord.ui.Modal, title="📅 กรอกวันที่ใหม่"):
    วันที่ = discord.ui.TextInput(
        label="วันที่ใหม่", placeholder="เช่น 15/6 หรือ 15/6/2569",
        required=True, max_length=12
    )

    def __init__(self, ctx: dict):
        super().__init__()
        self.ctx = ctx

    async def on_submit(self, interaction: discord.Interaction):
        try:
            parts = self.วันที่.value.strip().split("/")
            d, m = int(parts[0]), int(parts[1])
            y = int(parts[2]) - 543 if len(parts) >= 3 else datetime.now(TZ).year
            self.ctx["date"] = (d, m, y)
            view = EditTimeSelectView(ctx=self.ctx)
            await interaction.response.edit_message(
                content=f"**{self.ctx['card_name']}**\nเลือกเวลาใหม่\n📅 วัน: **{d}/{m}/{y+543}**",
                view=view
            )
        except Exception:
            await interaction.response.send_message("❌ รูปแบบวันไม่ถูกต้อง เช่น 15/6", ephemeral=True)

class EditCustomTimeModal(discord.ui.Modal, title="⏰ กรอกเวลาใหม่"):
    เวลา = discord.ui.TextInput(
        label="เวลา", placeholder="เช่น 13:30", required=True, max_length=5
    )

    def __init__(self, ctx: dict):
        super().__init__()
        self.ctx = ctx

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.เวลา.value.strip().replace(".", ":")
        try:
            parts = raw.split(":")
            h, mn = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
            if not (0 <= h <= 23 and 0 <= mn <= 59):
                raise ValueError
        except Exception:
            await interaction.response.send_message("❌ รูปแบบเวลาไม่ถูกต้อง เช่น 13:30", ephemeral=True)
            return
        await self._update_due(interaction, f"{h}:{mn:02d}")

    async def _update_due(self, interaction: discord.Interaction, time_str: str):
        try:
            d, m, y = self.ctx["date"]
            h, mn = map(int, time_str.split(":"))
            local_dt = TZ.localize(datetime(y, m, d, h, mn))
            due_iso = local_dt.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, partial(
                _request_with_retry, "PUT", f"{TRELLO_BASE}/cards/{self.ctx['card_id']}",
                **{"params": {"key": TRELLO_API_KEY, "token": TRELLO_TOKEN},
                   "json": {"due": due_iso}}
            ))
            invalidate_cache()
            await interaction.response.edit_message(
                content=f"✅ แก้กำหนดส่ง **{self.ctx['card_name']}** → {d}/{m}/{y+543} {time_str} น. แล้ว!",
                view=None
            )
        except Exception as e:
            await interaction.response.edit_message(content=f"❌ เกิดข้อผิดพลาด: {e}", view=None)

# ── Edit time select ──────────────────────────────────────────────
class EditTimeSelectView(discord.ui.View):
    TIMES = ["9:00", "10:00", "12:00", "15:00", "18:00", "23:59"]

    def __init__(self, ctx: dict):
        super().__init__(timeout=120)
        self.ctx = ctx
        for t in self.TIMES:
            self.add_item(self._make_btn(t))
        btn_custom = discord.ui.Button(label="📝 กรอกเอง", style=discord.ButtonStyle.primary, row=1)
        async def cb_custom(interaction: discord.Interaction):
            await interaction.response.send_modal(EditCustomTimeModal(ctx=self.ctx))
        btn_custom.callback = cb_custom
        self.add_item(btn_custom)
        add_cancel_button(self)

    def _make_btn(self, label):
        btn = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary)
        async def cb(interaction: discord.Interaction, _label=label):
            await self._submit(interaction, _label)
        btn.callback = cb
        return btn

    async def on_timeout(self):
        pass

    async def _submit(self, interaction: discord.Interaction, time_str: str):
        await interaction.response.defer()
        try:
            d, m, y = self.ctx["date"]
            h, mn = map(int, time_str.split(":"))
            local_dt = TZ.localize(datetime(y, m, d, h, mn))
            due_iso = local_dt.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, partial(
                _request_with_retry, "PUT", f"{TRELLO_BASE}/cards/{self.ctx['card_id']}",
                **{"params": {"key": TRELLO_API_KEY, "token": TRELLO_TOKEN},
                   "json": {"due": due_iso}}
            ))
            invalidate_cache()
            await interaction.followup.edit_message(
                interaction.message.id,
                content=f"✅ แก้กำหนดส่ง **{self.ctx['card_name']}** → {d}/{m}/{y+543} {time_str} น. แล้ว!",
                view=None
            )
        except Exception as e:
            await interaction.followup.send(f"❌ เกิดข้อผิดพลาด: {e}")

# ── Edit date select ──────────────────────────────────────────────
class EditDateSelectView(discord.ui.View):
    def __init__(self, ctx: dict):
        super().__init__(timeout=120)
        self.ctx = ctx
        now = datetime.now(TZ)
        labels = ["วันนี้", "พรุ่งนี้", "+2 วัน", "+3 วัน", "+7 วัน", "ลบกำหนดส่ง"]
        deltas = [0, 1, 2, 3, 7, None]
        for label, delta in zip(labels, deltas):
            btn = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary if delta is not None else discord.ButtonStyle.danger
            )
            if delta is not None:
                target = now + timedelta(days=delta)
                date_val = (target.day, target.month, target.year)
            else:
                date_val = None
            async def cb(interaction, _label=label, _date=date_val):
                await self._pick(interaction, _label, _date)
            btn.callback = cb
            self.add_item(btn)
        btn_custom = discord.ui.Button(label="📝 กรอกเอง", style=discord.ButtonStyle.primary, row=1)
        async def cb_custom(interaction: discord.Interaction):
            await interaction.response.send_modal(EditCustomDateModal(ctx=self.ctx))
        btn_custom.callback = cb_custom
        self.add_item(btn_custom)
        add_cancel_button(self)

    async def on_timeout(self):
        pass

    async def _pick(self, interaction: discord.Interaction, label: str, date_val):
        if date_val is None:
            # ลบกำหนดส่ง
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, partial(
                _request_with_retry, "PUT", f"{TRELLO_BASE}/cards/{self.ctx['card_id']}",
                **{"params": {"key": TRELLO_API_KEY, "token": TRELLO_TOKEN},
                   "json": {"due": None}}
            ))
            invalidate_cache()
            await interaction.response.edit_message(
                content=f"✅ ลบกำหนดส่งของ **{self.ctx['card_name']}** แล้ว", view=None
            )
            return
        self.ctx["date"] = date_val
        view = EditTimeSelectView(ctx=self.ctx)
        suffix = f" ({date_val[0]}/{date_val[1]})"
        await interaction.response.edit_message(
            content=f"**{self.ctx['card_name']}**\nเลือกเวลาใหม่\n📅 วัน: **{label}{suffix}**",
            view=view
        )

# ── Reassign member ───────────────────────────────────────────────
class ReassignMemberSelect(discord.ui.Select):
    def __init__(self, card: dict):
        options = [
            discord.SelectOption(label=f"{AVATARS.get(m,'')} {m}", value=m)
            for m in MEMBERS
        ]
        super().__init__(placeholder="ย้ายให้ใคร?", options=options)
        self.card = card

    async def callback(self, interaction: discord.Interaction):
        new_member = self.values[0]
        lists = await get_lists()
        # หา list "รอดำเนินการ" ของคนนั้นก่อน ถ้าไม่เจอค่อยเอา list แรกที่มีชื่อ
        target = next(
            (lid for lname, lid in lists.items()
             if new_member in lname and "เสร็จ" not in lname and "กำลังทำ" not in lname),
            None
        ) or next(
            (lid for lname, lid in lists.items() if new_member in lname), None
        )
        if not target:
            await interaction.response.edit_message(
                content=f"❌ ไม่พบลิสต์ของ {new_member} ในบอร์ด", view=None
            )
            return
        await move_card(self.card["id"], target)
        await notify_assignment(new_member, self.card["name"], interaction=interaction)
        await interaction.response.edit_message(
            content=f"✅ ย้าย **{self.card['name']}** ให้ {AVATARS.get(new_member,'')} {new_member} แล้ว!",
            view=None
        )

class ReassignMemberView(discord.ui.View):
    def __init__(self, card: dict):
        super().__init__(timeout=60)
        self.add_item(ReassignMemberSelect(card=card))
        add_cancel_button(self)

    async def on_timeout(self):
        pass

# ── Edit options ──────────────────────────────────────────────────
class EditOptionsView(discord.ui.View):
    def __init__(self, card: dict):
        super().__init__(timeout=60)
        self.card = card

    async def on_timeout(self):
        pass

    @discord.ui.button(label="✏️ แก้ชื่องาน", style=discord.ButtonStyle.primary)
    async def btn_name(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            EditNameModal(card_id=self.card["id"], card_name=self.card["name"])
        )

    @discord.ui.button(label="📅 แก้กำหนดส่ง", style=discord.ButtonStyle.secondary)
    async def btn_due(self, interaction: discord.Interaction, button: discord.ui.Button):
        ctx = {"card_id": self.card["id"], "card_name": self.card["name"]}
        view = EditDateSelectView(ctx=ctx)
        await interaction.response.edit_message(
            content=f"**{self.card['name']}**\nเลือกกำหนดส่งใหม่:", view=view
        )

    @discord.ui.button(label="📝 แก้หมายเหตุ", style=discord.ButtonStyle.secondary)
    async def btn_note(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            EditNoteModal(card_id=self.card["id"], current_desc=self.card.get("desc", ""))
        )

    @discord.ui.button(label="👤 ย้ายให้คนอื่น", style=discord.ButtonStyle.danger)
    async def btn_move(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ReassignMemberView(card=self.card)
        await interaction.response.edit_message(
            content=f"**{self.card['name']}**\nต้องการมอบหมายให้ใคร?", view=view
        )

    @discord.ui.button(label="❌ ยกเลิก", style=discord.ButtonStyle.danger, row=4)
    async def btn_cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="❌ ยกเลิกแล้ว", view=None)

# ── Edit card select ──────────────────────────────────────────────
class EditCardSelect(discord.ui.Select):
    def __init__(self, cards: list):
        options = [
            discord.SelectOption(
                label=c["name"][:80],
                value=c["id"],
                description=c.get("list_name", "")[:50]
            ) for c in sort_by_due(cards)[:25]
        ]
        super().__init__(placeholder="เลือกงานที่จะแก้...", options=options)
        self.cards = cards

    async def callback(self, interaction: discord.Interaction):
        card_id = self.values[0]
        card = next(c for c in self.cards if c["id"] == card_id)
        view = EditOptionsView(card=card)
        await interaction.response.edit_message(
            content=f"**{card['name']}**\nต้องการแก้ไขส่วนใด?", view=view
        )

class EditCardSelectView(discord.ui.View):
    def __init__(self, cards: list):
        super().__init__(timeout=60)
        self.add_item(EditCardSelect(cards))
        add_cancel_button(self)

    async def on_timeout(self):
        pass

class EditMemberSelect(discord.ui.Select):
    def __init__(self, names: list = None):
        super().__init__(placeholder="เลือกสมาชิก...", options=member_options(names or MEMBERS))

    async def callback(self, interaction: discord.Interaction):
        member = self.values[0]
        cards = await fetch_member_cards(member)
        if not cards:
            await interaction.response.edit_message(
                content=f"✅ {member} ไม่มีงานค้าง", view=None
            )
            return
        view = EditCardSelectView(cards)
        note = cards_cap_note(len(cards))
        await interaction.response.edit_message(
            content=f"**งานของ {member}** — เลือกงานที่จะแก้:{note}", view=view
        )

class EditMemberSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(EditMemberSelect())
        add_cancel_button(self)

    async def on_timeout(self):
        pass

@tree.command(name="editcard", description="แก้ไขรายละเอียดงาน")
async def editcard_command(interaction: discord.Interaction):
    if not await require_team_member(interaction):
        return
    intro = "**✏️ แก้ไขงาน** — ต้องการแก้ไขงานของใคร?"
    view = member_flow_view(scope_names(interaction.guild_id), lambda ns: EditMemberSelectView(ns), intro)
    await interaction.response.send_message(intro, view=view, ephemeral=True)

# ══════════════════════════════════════════════════════════════════
# ปฏิทินงาน — /calendar (ตารางรายเดือน) + สรุปสัปดาห์ทุกเช้าวันจันทร์
# ══════════════════════════════════════════════════════════════════
THAI_MONTHS = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
               "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]
THAI_DOW_SHORT_SUN_FIRST = ["อา.", "จ.", "อ.", "พ.", "พฤ.", "ศ.", "ส."]
THAI_DOW_FULL = ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"]  # index = weekday()
MEMBER_COLORS = {n: p["color"] for n, p in PEOPLE.items()}
UNASSIGNED_COLOR = (130, 130, 140)
WEEKLY_DIGEST_HOUR = 9  # วันจันทร์ 09:00 น.

_FONT_REGULAR = [("/usr/share/fonts/truetype/tlwg/Loma.ttf", 0),
                 ("/System/Library/Fonts/Supplemental/Ayuthaya.ttf", 0),
                 ("/System/Library/Fonts/Thonburi.ttc", 0)]
_FONT_BOLD = [("/usr/share/fonts/truetype/tlwg/Loma-Bold.ttf", 0),
              ("/System/Library/Fonts/Thonburi.ttc", 1),
              ("/System/Library/Fonts/Supplemental/Ayuthaya.ttf", 0)]

def _load_font(candidates, size):
    for path, index in candidates:
        try:
            return ImageFont.truetype(path, size, index=index)
        except Exception:
            continue
    print("⚠️ ไม่พบฟอนต์ไทยสำหรับวาดปฏิทิน ข้อความไทยจะแสดงไม่ถูกต้อง")
    return ImageFont.load_default()

def _fit_text(text, font, max_width):
    if font.getlength(text) <= max_width:
        return text
    while len(text) > 1 and font.getlength(text + "…") > max_width:
        text = text[:-1]
    return text + "…"

def _member_of_list(list_name):
    return next((m for m in MEMBERS if m in list_name), None)

THAI_MONTH_SHORT = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
                    "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
_CATEGORY_RE = re.compile(r"^\[(.+?)\]\s*(.*)$", re.S)

def split_category(name: str):
    m = _CATEGORY_RE.match(name or "")
    return (m.group(1), m.group(2)) if m else ("", name or "")

def split_desc(desc: str):
    """แยกหมายเหตุออกจากบรรทัด 'สั่งโดย: <@id>' ที่บอทต่อท้ายไว้"""
    desc = desc or ""
    m = REQUESTER_RE.search(desc)
    requester_id = int(m.group(1)) if m else None
    note = GROUP_RE.sub("", ASSIGNEES_RE.sub("", re.split(r"\n*---\nสั่งโดย:", desc)[0])).strip()
    return note, requester_id

def format_due_time(due: datetime) -> str:
    return "ทั้งวัน" if (due.hour, due.minute) == (23, 59) else due.strftime("%H:%M")

async def get_calendar_items(member_filter: str = None, names: list = None) -> list:
    """งานที่ยังไม่เสร็จและมี due date"""
    lists = await get_lists()
    done_ids = {v for k, v in lists.items() if "เสร็จ" in k}
    list_id_to_name = {v: k for k, v in lists.items()}
    items = []
    for card in await get_all_cards():
        if card["idList"] in done_ids or not card.get("due"):
            continue
        try:
            due = datetime.fromisoformat(card["due"].replace("Z", "+00:00")).astimezone(TZ)
        except Exception:
            continue
        member = _member_of_list(list_id_to_name.get(card["idList"], ""))
        if member_filter and member != member_filter:
            continue
        if names is not None and member is not None and member not in names:
            continue
        category, title = split_category(card["name"])
        note, requester_id = split_desc(card.get("desc"))
        items.append({
            "name": card["name"], "title": title, "category": category,
            "member": member, "due": due, "note": note,
            "requester_id": requester_id, "url": card.get("url", ""),
            "assignees": parse_assignees(card.get("desc")),
        })
    items.sort(key=lambda i: i["due"])
    return items

def _wrap_text(text, font, max_width, max_lines=2):
    """ตัดบรรทัดทีละตัวอักษร (ภาษาไทยไม่มีช่องว่างคั่นคำ) ถ้าเกินจำนวนบรรทัดให้ปิดด้วย …"""
    lines, cur = [], ""
    for ch in text:
        if font.getlength(cur + ch) <= max_width:
            cur += ch
            continue
        lines.append(cur)
        cur = ch
        if len(lines) == max_lines:
            break
    else:
        if cur:
            lines.append(cur)
        return lines or [""]
    last = lines[max_lines - 1]
    while len(last) > 1 and font.getlength(last + "…") > max_width:
        last = last[:-1]
    lines[max_lines - 1] = last + "…"
    return lines

def render_month_png(year: int, month: int, items: list, member_filter: str = None, names: list = None) -> bytes:
    weeks = pycal.Calendar(firstweekday=6).monthdayscalendar(year, month)
    W, MARGIN, HEADER, DOW_H, CELL_H = 1800, 36, 130, 52, 235
    col_w = (W - 2 * MARGIN) // 7
    H = MARGIN + HEADER + DOW_H + CELL_H * len(weeks) + MARGIN
    img = Image.new("RGB", (W, H), (250, 250, 252))
    d = ImageDraw.Draw(img)
    f_title, f_sub = _load_font(_FONT_BOLD, 58), _load_font(_FONT_REGULAR, 26)
    f_dow, f_day = _load_font(_FONT_BOLD, 26), _load_font(_FONT_BOLD, 28)
    f_item, f_small = _load_font(_FONT_REGULAR, 21), _load_font(_FONT_REGULAR, 19)

    today = datetime.now(TZ).date()
    d.text((MARGIN, MARGIN), f"{THAI_MONTHS[month - 1]} {year + 543}", font=f_title, fill=(30, 30, 40))
    sub = f"เฉพาะงานของ {member_filter}" if member_filter else "ทุกคน"
    d.text((MARGIN, MARGIN + 78), f"ปฏิทินงาน • {sub} • เฉพาะงานที่ยังไม่เสร็จ • ดูรายละเอียดเต็มจากเมนูด้านล่างรูป",
           font=f_sub, fill=(110, 110, 125))

    lx = W - MARGIN
    for name in reversed([n for n in MEMBER_COLORS if names is None or n in names]):
        w = int(f_small.getlength(name)) + 38
        lx -= w
        d.ellipse((lx, MARGIN + 18, lx + 16, MARGIN + 34), fill=MEMBER_COLORS[name])
        d.text((lx + 22, MARGIN + 12), name, font=f_small, fill=(70, 70, 85))

    top = MARGIN + HEADER
    for i, label in enumerate(THAI_DOW_SHORT_SUN_FIRST):
        x = MARGIN + i * col_w
        d.rectangle((x, top, x + col_w, top + DOW_H), fill=(236, 237, 243))
        tw = d.textlength(label, font=f_dow)
        d.text((x + (col_w - tw) / 2, top + 9), label, font=f_dow, fill=(70, 70, 85))

    by_day = {}
    for it in items:
        if it["due"].year == year and it["due"].month == month:
            by_day.setdefault(it["due"].day, []).append(it)

    line_h, pad, gap, text_w = 25, 5, 4, col_w - 34
    grid_top = top + DOW_H
    for r, week in enumerate(weeks):
        for c, day in enumerate(week):
            x, y = MARGIN + c * col_w, grid_top + r * CELL_H
            d.rectangle((x, y, x + col_w, y + CELL_H), fill=(255, 255, 255), outline=(222, 224, 232))
            if day == 0:
                d.rectangle((x + 1, y + 1, x + col_w - 1, y + CELL_H - 1), fill=(246, 247, 250))
                continue
            is_today = (year, month, day) == (today.year, today.month, today.day)
            if is_today:
                d.rectangle((x + 1, y + 1, x + col_w - 1, y + CELL_H - 1), fill=(240, 246, 255))
                d.ellipse((x + 7, y + 6, x + 47, y + 46), fill=(66, 133, 244))
            num = str(day)
            nw = d.textlength(num, font=f_day)
            d.text((x + 27 - nw / 2 if is_today else x + 14, y + 10), num, font=f_day,
                   fill=(255, 255, 255) if is_today else (50, 50, 65))

            boxes = []
            for it in by_day.get(day, []):
                label = it["title"] if it["due"].strftime("%H:%M") == "23:59" else f"{it['due'].strftime('%H:%M')} {it['title']}"
                lines = _wrap_text(label, f_item, text_w, 2)
                boxes.append((it, lines, pad * 2 + line_h * len(lines)))
            avail = CELL_H - 54 - 6
            fit, used = 0, 0
            for _, _, h in boxes:
                if used + h > avail:
                    break
                used += h + gap
                fit += 1
            if fit < len(boxes):  # เหลือที่ไม่พอ ต้องเว้นที่บรรทัด "+N งาน"
                while fit > 0 and sum(b[2] + gap for b in boxes[:fit]) + 24 > avail:
                    fit -= 1
            iy = y + 54
            for it, lines, h in boxes[:fit]:
                color = MEMBER_COLORS.get(it["member"], UNASSIGNED_COLOR)
                d.rounded_rectangle((x + 6, iy, x + col_w - 6, iy + h), radius=6,
                                    fill=tuple(int(v * 0.18 + 255 * 0.82) for v in color))
                d.rectangle((x + 6, iy, x + 11, iy + h), fill=color)
                for k, ln in enumerate(lines):
                    d.text((x + 18, iy + pad + k * line_h), ln, font=f_item, fill=(40, 40, 55))
                iy += h + gap
            if fit < len(boxes):
                d.text((x + 14, iy + 1), f"+{len(boxes) - fit} งาน (ดูจากเมนู)", font=f_small, fill=(90, 90, 110))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

async def build_calendar_message(year: int, month: int, member_filter: str = None, names: list = None):
    items = await get_calendar_items(member_filter, names)
    loop = asyncio.get_running_loop()
    png = await loop.run_in_executor(None, partial(render_month_png, year, month, items, member_filter, names))
    return discord.File(io.BytesIO(png), filename=f"calendar-{year}-{month:02d}.png"), items

async def build_calendar_file(year: int, month: int, member_filter: str = None, names: list = None) -> discord.File:
    file, _ = await build_calendar_message(year, month, member_filter, names)
    return file

def _text_color_for(bg):
    lum = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
    return (35, 35, 45) if lum > 165 else (255, 255, 255)

def _week_start_of(day):
    return day - timedelta(days=day.weekday())  # จันทร์

def render_week_png(week_start, items: list, member_filter: str = None, names: list = None) -> bytes:
    days = [week_start + timedelta(days=i) for i in range(7)]
    by_day = {dd: [] for dd in days}
    for it in items:
        if it["due"].date() in by_day:
            by_day[it["due"].date()].append(it)

    W, MARGIN, HEADER, COL_HEAD = 1800, 36, 130, 128
    BLOCK_H, GAP, MAX_PER_DAY, TITLE_LINES = 150, 8, 8, 4
    col_w = (W - 2 * MARGIN) // 7
    n = max([min(len(v), MAX_PER_DAY) for v in by_day.values()] + [0])
    has_over = any(len(v) > MAX_PER_DAY for v in by_day.values())
    body_h = max(n * (BLOCK_H + GAP) + (34 if has_over else 8), 340)
    H = MARGIN + HEADER + COL_HEAD + body_h + MARGIN

    img = Image.new("RGB", (W, H), (250, 250, 252))
    d = ImageDraw.Draw(img)
    f_title, f_sub = _load_font(_FONT_BOLD, 58), _load_font(_FONT_REGULAR, 26)
    f_dow, f_num = _load_font(_FONT_BOLD, 26), _load_font(_FONT_BOLD, 54)
    f_item, f_time = _load_font(_FONT_REGULAR, 22), _load_font(_FONT_BOLD, 20)
    f_small = _load_font(_FONT_REGULAR, 19)

    d0, d6 = days[0], days[6]
    if d0.month == d6.month:
        rng = f"{d0.day} – {d6.day} {THAI_MONTHS[d0.month - 1]} {d6.year + 543}"
    else:
        rng = f"{d0.day} {THAI_MONTH_SHORT[d0.month - 1]} – {d6.day} {THAI_MONTH_SHORT[d6.month - 1]} {d6.year + 543}"
    d.text((MARGIN, MARGIN), rng, font=f_title, fill=(30, 30, 40))
    sub = f"เฉพาะงานของ {member_filter}" if member_filter else "ทุกคน"
    d.text((MARGIN, MARGIN + 78), f"ปฏิทินงานรายสัปดาห์ • {sub} • เฉพาะงานที่ยังไม่เสร็จ • ดูรายละเอียดเต็มจากเมนูด้านล่างรูป",
           font=f_sub, fill=(110, 110, 125))
    lx = W - MARGIN
    for name in reversed([n for n in MEMBER_COLORS if names is None or n in names]):
        w = int(f_small.getlength(name)) + 38
        lx -= w
        d.ellipse((lx, MARGIN + 18, lx + 16, MARGIN + 34), fill=MEMBER_COLORS[name])
        d.text((lx + 22, MARGIN + 12), name, font=f_small, fill=(70, 70, 85))

    top = MARGIN + HEADER
    today = datetime.now(TZ).date()
    text_w = col_w - 34
    total_items = 0
    for i, day in enumerate(days):
        x = MARGIN + i * col_w
        is_today = day == today
        d.rectangle((x, top, x + col_w, top + COL_HEAD + body_h),
                    fill=(240, 246, 255) if is_today else (255, 255, 255), outline=(222, 224, 232))
        d.rectangle((x, top, x + col_w, top + COL_HEAD), fill=(236, 237, 243), outline=(222, 224, 232))
        label = THAI_DOW_SHORT_SUN_FIRST[(day.weekday() + 1) % 7]
        lw = d.textlength(label, font=f_dow)
        d.text((x + (col_w - lw) / 2, top + 10), label, font=f_dow, fill=(70, 70, 85))
        num = str(day.day)
        nw = d.textlength(num, font=f_num)
        cx, cy = x + col_w / 2, top + 88
        if is_today:
            d.ellipse((cx - 36, cy - 34, cx + 36, cy + 38), fill=(66, 133, 244))
        d.text((cx - nw / 2, cy - 32), num, font=f_num, fill=(255, 255, 255) if is_today else (40, 40, 55))

        day_items = by_day[day]
        total_items += len(day_items)
        y = top + COL_HEAD + 8
        for it in day_items[:MAX_PER_DAY]:
            color = MEMBER_COLORS.get(it["member"], UNASSIGNED_COLOR)
            fg = _text_color_for(color)
            d.rounded_rectangle((x + 6, y, x + col_w - 6, y + BLOCK_H), radius=9, fill=color)
            head = f"{format_due_time(it['due'])} • {it['member'] or 'ยังไม่ระบุ'}"
            d.text((x + 16, y + 8), _fit_text(head, f_time, text_w), font=f_time, fill=fg)
            for k, ln in enumerate(_wrap_text(it["title"], f_item, text_w, TITLE_LINES)):
                d.text((x + 16, y + 38 + k * 26), ln, font=f_item, fill=fg)
            y += BLOCK_H + GAP
        if len(day_items) > MAX_PER_DAY:
            d.text((x + 14, y + 2), f"+{len(day_items) - MAX_PER_DAY} งาน (ดูจากเมนู)", font=f_small, fill=(90, 90, 110))

    if total_items == 0:
        msg = "สัปดาห์นี้ไม่มีงานที่ครบกำหนด"
        mw = d.textlength(msg, font=f_sub)
        d.text(((W - mw) / 2, top + COL_HEAD + body_h / 2 - 14), msg, font=f_sub, fill=(120, 120, 135))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

async def build_week_message(week_start, member_filter: str = None, names: list = None):
    items = await get_calendar_items(member_filter, names)
    loop = asyncio.get_running_loop()
    png = await loop.run_in_executor(None, partial(render_week_png, week_start, items, member_filter, names))
    return discord.File(io.BytesIO(png), filename=f"calendar-week-{week_start.isoformat()}.png"), items

def _week_caption(week_start, member_filter):
    d0, d6 = week_start, week_start + timedelta(days=6)
    who = f" • {member_filter}" if member_filter else ""
    return (f"📅 **ปฏิทินงาน สัปดาห์ {d0.day} {THAI_MONTH_SHORT[d0.month - 1]} – "
            f"{d6.day} {THAI_MONTH_SHORT[d6.month - 1]} {d6.year + 543}**{who}")

def _shift_month(year, month, delta):
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1

def _calendar_caption(year, month, member_filter):
    who = f" • {member_filter}" if member_filter else ""
    return f"📅 **ปฏิทินงาน {THAI_MONTHS[month - 1]} {year + 543}**{who}"

def _item_line(it, with_category=True):
    cat = f"[{it['category']}] " if with_category and it["category"] else ""
    return f"{format_due_time(it['due'])} {AVATARS.get(it['member'], '⚪')} {cat}{it['title']}"

def build_day_embed(year: int, month: int, day: int, day_items: list) -> discord.Embed:
    dow = THAI_DOW_FULL[datetime(year, month, day).weekday()]
    embed = discord.Embed(
        title=f"📅 วัน{dow}ที่ {day} {THAI_MONTHS[month - 1]} {year + 543}",
        description=f"{len(day_items)} งานที่ครบกำหนด" if day_items else "วันนี้ไม่มีงานที่ครบกำหนด",
        color=0x4285F4
    )
    total_chars, shown = 0, 0
    for it in day_items:
        parts = [f"ผู้รับผิดชอบ: {AVATARS.get(it['member'], '⚪')} {it['member'] or 'ยังไม่ระบุ'}"]
        co = [display_assignee(n) for n in it.get("assignees", []) if n != it["member"]]
        if co:
            parts[0] += f" • ร่วมกับ: {', '.join(co)}"
        if it["category"]:
            parts[0] += f" • หมวด: {it['category']}"
        parts[0] += f" • ครบ {format_due_time(it['due'])}"
        if it["requester_id"]:
            parts.append(f"สั่งโดย: <@{it['requester_id']}>")
        if it["note"]:
            note = it["note"] if len(it["note"]) <= 300 else it["note"][:297] + "…"
            parts.append(f"หมายเหตุ: {note}")
        if it["url"]:
            parts.append(f"[เปิดใน Trello]({it['url']})")
        value = "\n".join(parts)
        name = f"{format_due_time(it['due'])} • {it['title']}"
        name = name if len(name) <= 250 else name[:247] + "…"
        if total_chars + len(value) + len(name) > 5200 or shown >= 20:
            break
        embed.add_field(name=name, value=value, inline=False)
        total_chars += len(value) + len(name)
        shown += 1
    if shown < len(day_items):
        embed.set_footer(text=f"และอีก {len(day_items) - shown} งาน — ดูทั้งหมดที่ปุ่ม 📋 รายการทั้งเดือน")
    return embed

def build_agenda_chunks(items: list) -> list:
    """รายการงานเป็นตัวอักษร แบ่งเป็นก้อนละไม่เกิน ~3500 ตัวอักษร"""
    if not items:
        return ["ไม่มีงานที่ครบกำหนดในช่วงนี้"]
    chunks, cur, last_date = [], "", None
    for it in items:
        text = ""
        if it["due"].date() != last_date:
            last_date = it["due"].date()
            dow = THAI_DOW_SHORT_SUN_FIRST[(it["due"].weekday() + 1) % 7]
            text += f"\n**{dow} {it['due'].day} {THAI_MONTH_SHORT[it['due'].month - 1]}**\n"
        line = _item_line(it)
        text += "• " + (line if len(line) <= 140 else line[:137] + "…") + "\n"
        if len(cur) + len(text) > 3500:
            chunks.append(cur)
            cur = ""
        cur += text
    if cur:
        chunks.append(cur)
    return chunks

class DaySelect(discord.ui.Select):
    def __init__(self, day_counts: dict):
        options = []
        for day in sorted(day_counts)[:25]:
            dow = THAI_DOW_SHORT_SUN_FIRST[(day.weekday() + 1) % 7]
            options.append(discord.SelectOption(
                label=f"{dow} {day.day} {THAI_MONTH_SHORT[day.month - 1]} — {day_counts[day]} งาน",
                value=day.isoformat()
            ))
        super().__init__(placeholder="เลือกวัน เพื่อดูรายละเอียดงาน...", options=options, row=1)

class CalendarView(discord.ui.View):
    def __init__(self, mode: str, year: int, month: int, week_start, member_filter: str, items: list, names: list = None):
        super().__init__(timeout=600)
        self.names = names
        self.mode, self.year, self.month = mode, year, month
        self.week_start, self.member_filter = week_start, member_filter
        self._sync(items)

    def _range_items(self, items: list) -> list:
        if self.mode == "week":
            end = self.week_start + timedelta(days=6)
            return [i for i in items if self.week_start <= i["due"].date() <= end]
        return [i for i in items if i["due"].year == self.year and i["due"].month == self.month]

    def _caption(self) -> str:
        if self.mode == "week":
            return _week_caption(self.week_start, self.member_filter)
        return _calendar_caption(self.year, self.month, self.member_filter)

    async def _build(self):
        if self.mode == "week":
            return await build_week_message(self.week_start, self.member_filter, self.names)
        return await build_calendar_message(self.year, self.month, self.member_filter, self.names)

    def _sync(self, items: list):
        week = self.mode == "week"
        self.btn_prev.label = "◀ สัปดาห์ก่อน" if week else "◀ เดือนก่อน"
        self.btn_today.label = "สัปดาห์นี้" if week else "เดือนนี้"
        self.btn_next.label = "สัปดาห์หน้า ▶" if week else "เดือนหน้า ▶"
        self.btn_mode.label = "📅 ดูรายเดือน" if week else "🗓️ ดูรายสัปดาห์"
        self.btn_list.label = "📋 รายการสัปดาห์" if week else "📋 รายการทั้งเดือน"
        for child in list(self.children):
            if isinstance(child, DaySelect):
                self.remove_item(child)
        counts = {}
        for it in self._range_items(items):
            counts[it["due"].date()] = counts.get(it["due"].date(), 0) + 1
        if counts:
            select = DaySelect(counts)
            select.callback = self._on_day
            self.add_item(select)

    async def _on_day(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            day = datetime.strptime(interaction.data["values"][0], "%Y-%m-%d").date()
            items = await get_calendar_items(self.member_filter, self.names)
            day_items = [i for i in items if i["due"].date() == day]
            await interaction.followup.send(
                embed=build_day_embed(day.year, day.month, day.day, day_items), ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(f"❌ เกิดข้อผิดพลาด: {e}", ephemeral=True)

    async def _refresh(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            file, items = await self._build()
            self._sync(items)
            await interaction.edit_original_response(content=self._caption(), attachments=[file], view=self)
        except Exception as e:
            await interaction.followup.send(f"❌ เกิดข้อผิดพลาด: {e}", ephemeral=True)

    @discord.ui.button(label="◀ เดือนก่อน", style=discord.ButtonStyle.secondary, row=0)
    async def btn_prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.mode == "week":
            self.week_start -= timedelta(days=7)
        else:
            self.year, self.month = _shift_month(self.year, self.month, -1)
        await self._refresh(interaction)

    @discord.ui.button(label="เดือนนี้", style=discord.ButtonStyle.primary, row=0)
    async def btn_today(self, interaction: discord.Interaction, button: discord.ui.Button):
        now = datetime.now(TZ)
        if self.mode == "week":
            self.week_start = _week_start_of(now.date())
        else:
            self.year, self.month = now.year, now.month
        await self._refresh(interaction)

    @discord.ui.button(label="เดือนหน้า ▶", style=discord.ButtonStyle.secondary, row=0)
    async def btn_next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.mode == "week":
            self.week_start += timedelta(days=7)
        else:
            self.year, self.month = _shift_month(self.year, self.month, 1)
        await self._refresh(interaction)

    @discord.ui.button(label="🗓️ ดูรายสัปดาห์", style=discord.ButtonStyle.success, row=0)
    async def btn_mode(self, interaction: discord.Interaction, button: discord.ui.Button):
        today = datetime.now(TZ).date()
        if self.mode == "week":
            mid = self.week_start + timedelta(days=3)
            self.mode, self.year, self.month = "month", mid.year, mid.month
        else:
            self.mode = "week"
            if (self.year, self.month) == (today.year, today.month):
                self.week_start = _week_start_of(today)
            else:
                self.week_start = _week_start_of(datetime(self.year, self.month, 1).date())
        await self._refresh(interaction)

    @discord.ui.button(label="📋 รายการทั้งเดือน", style=discord.ButtonStyle.secondary, row=0)
    async def btn_list(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            items = self._range_items(await get_calendar_items(self.member_filter, self.names))
            chunks = build_agenda_chunks(items)
            for n, chunk in enumerate(chunks[:5]):
                embed = discord.Embed(description=chunk, color=0x4285F4)
                if n == 0:
                    embed.title = self._caption().replace("**", "")
                if n == 4 and len(chunks) > 5:
                    embed.set_footer(text="แสดงบางส่วน — เลือกดูรายวันจากเมนูเพื่อดูครบ")
                await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ เกิดข้อผิดพลาด: {e}", ephemeral=True)

VIEW_CHOICES = [
    app_commands.Choice(name="รายเดือน", value="month"),
    app_commands.Choice(name="รายสัปดาห์", value="week"),
]

@tree.command(name="calendar", description="ดูปฏิทินงาน รายเดือนหรือรายสัปดาห์ (เลือกดูเฉพาะคนได้)")
@app_commands.describe(ชื่อ="ดูเฉพาะงานของคนนี้ (เว้นว่าง = ทุกคน)", มุมมอง="รายเดือน (ค่าเริ่มต้น) หรือรายสัปดาห์")
@app_commands.choices(ชื่อ=MEMBER_CHOICES, มุมมอง=VIEW_CHOICES)
async def calendar_command(interaction: discord.Interaction,
                           ชื่อ: app_commands.Choice[str] = None,
                           มุมมอง: app_commands.Choice[str] = None):
    await interaction.response.defer()
    member_filter = ชื่อ.value if ชื่อ is not None else None
    mode = มุมมอง.value if มุมมอง is not None else "month"
    now = datetime.now(TZ)
    try:
        view = CalendarView(mode, now.year, now.month, _week_start_of(now.date()), member_filter, [],
                            scope_names(interaction.guild_id))
        file, items = await view._build()
        view._sync(items)
        await interaction.followup.send(content=view._caption(), file=file, view=view)
    except Exception as e:
        await interaction.followup.send(f"❌ เกิดข้อผิดพลาด: {e}")

def build_week_embed(items: list, week_start, overdue_count: int) -> discord.Embed:
    week_end = week_start + timedelta(days=6)
    embed = discord.Embed(
        title=f"🗓️ งานสัปดาห์นี้ ({week_start.day}/{week_start.month} – {week_end.day}/{week_end.month})",
        description=f"{len(items)} งานที่ครบกำหนดในสัปดาห์นี้" if items else "สัปดาห์นี้ไม่มีงานที่ครบกำหนด 🎉",
        color=0x4285F4, timestamp=datetime.now(TZ)
    )
    for i in range(7):
        day = week_start + timedelta(days=i)
        day_items = [it for it in items if it["due"].date() == day]
        if not day_items and not items:
            continue
        lines = []
        for it in day_items:
            t = "ทั้งวัน" if (it["due"].hour, it["due"].minute) == (23, 59) else it["due"].strftime("%H:%M")
            lines.append(f"{t} {AVATARS.get(it['member'], '⚪')} {it['name']}")
        value = "\n".join(lines) or "—"
        if len(value) > 1000:
            value = value[:997] + "…"
        embed.add_field(name=f"{THAI_DOW_FULL[i]} {day.day}/{day.month}", value=value, inline=False)
    if overdue_count:
        embed.set_footer(text=f"⚠️ มีงานเลยกำหนดค้างอยู่ {overdue_count} งาน ดูได้ที่ /overdue")
    return embed

async def post_weekly_digest(channel, names: list = None):
    now = datetime.now(TZ)
    week_start = now.date() - timedelta(days=now.weekday())
    week_end = week_start + timedelta(days=6)
    all_items = await get_calendar_items(None, names)
    week_items = [i for i in all_items if week_start <= i["due"].date() <= week_end]
    overdue = sum(1 for i in all_items if i["due"] < now)
    embed = build_week_embed(week_items, week_start, overdue)
    file, _ = await build_week_message(week_start, None, names)
    embed.set_image(url=f"attachment://{file.filename}")
    await channel.send(embed=embed, file=file)

async def weekly_calendar_digest():
    await client.wait_until_ready()
    last_sent_date = None
    while not client.is_closed():
        now = datetime.now(TZ)
        if now.weekday() == 0 and now.hour == WEEKLY_DIGEST_HOUR and last_sent_date != now.date():
            last_sent_date = now.date()
            for _gid, cid, names in notify_targets():
                try:
                    channel = client.get_channel(cid) or await client.fetch_channel(cid)
                    await post_weekly_digest(channel, names)
                    print(f"✓ ส่งสรุปปฏิทินสัปดาห์แล้ว (channel {cid})")
                except Exception as e:
                    print(f"weekly digest error (channel {cid}): {e}")
        await asyncio.sleep(60)

# ══════════════════════════════════════════════════════════════════
# ตรวจสอบรูปก่อนใช้งาน — ช่อง REVIEW_CHANNEL_ID
# ══════════════════════════════════════════════════════════════════
REVIEW_CHANNEL_ID = 1447896847267004420
REVIEWER_NAMES = ["โดม", "พี", "ไอซ์", "กาย"]
REVIEW_APPROVE_EMOJI = "✅"
REVIEW_REJECT_EMOJI = "❌"
REVIEW_ESCALATE_SECONDS = 300  # 5 นาที
REVIEW_QUORUM = 2  # ต้องมีคนตรวจ (ไม่นับ NOVA และไม่นับคนโพสต์เอง) อย่างน้อยกี่คนถึงจะปิดรีวิว

# message_id -> {"posted_at", "poster_id", "channel_id", "last_notified", "votes": {name: "approve"/"reject"}}
pending_reviews: dict = {}

async def notify_reviewers(message: discord.Message, target_names: list = None):
    if target_names is None:
        target_names = [n for n in REVIEWER_NAMES if MEMBER_DISCORD_IDS.get(n) != message.author.id]
    for name in target_names:
        uid = MEMBER_DISCORD_IDS.get(name)
        if not uid:
            continue
        try:
            user = client.get_user(uid) or await client.fetch_user(uid)
            embed = discord.Embed(
                title="🖼️ มีรูปใหม่รอตรวจสอบ",
                description=f"โพสต์โดย {message.author.mention} ในช่อง {message.channel.mention}",
                color=0xF5A623, timestamp=datetime.now(TZ)
            )
            if message.attachments:
                embed.set_image(url=message.attachments[0].url)
            embed.add_field(name="ลิงก์ข้อความ", value=message.jump_url, inline=False)
            embed.set_footer(text=f"กด ✅ ผ่าน หรือ ❌ ต้องแก้ไข ที่ข้อความในช่องได้เลย (ต้องมีคนตรวจครบ {REVIEW_QUORUM} คนถึงจะสรุปผล)")
            await user.send(embed=embed)
        except Exception as e:
            print(f"notify_reviewers error ({name}): {e}")
    if message.id in pending_reviews:
        pending_reviews[message.id]["last_notified"] = datetime.now(TZ)

@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if message.channel.id != REVIEW_CHANNEL_ID:
        return
    if not any((a.content_type or "").startswith("image/") for a in message.attachments):
        return
    try:
        await message.add_reaction(REVIEW_APPROVE_EMOJI)
        await message.add_reaction(REVIEW_REJECT_EMOJI)
    except Exception as e:
        print(f"review react error: {e}")
    pending_reviews[message.id] = {
        "posted_at": datetime.now(TZ),
        "poster_id": message.author.id,
        "channel_id": message.channel.id,
        "last_notified": None,
        "votes": {},
    }
    await notify_reviewers(message)

@client.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if client.user is not None and payload.user_id == client.user.id:
        return  # ไม่นับปฏิกิริยาที่ NOVA ติดให้เองตอนแรก
    if str(payload.emoji) not in (REVIEW_APPROVE_EMOJI, REVIEW_REJECT_EMOJI):
        return
    reviewer_name = DISCORD_ID_TO_MEMBER.get(payload.user_id)
    if reviewer_name not in REVIEWER_NAMES:
        return  # ต้องเป็นคนตรวจที่กำหนดไว้เท่านั้น ถึงจะนับว่าตรวจแล้ว

    pending_info = pending_reviews.get(payload.message_id)
    if pending_info is not None and payload.user_id == pending_info["poster_id"]:
        return  # คนโพสต์เองกดปฏิกิริยาบนงานตัวเอง ไม่นับว่าตรวจ ไม่ต้องทำอะไร

    # เลือกได้แค่อันเดียวต่อคน — ลบปฏิกิริยาฝั่งตรงข้ามของคนเดิมออกเสมอ
    # (ทำก่อนเช็ค pending_reviews เพราะคลิกที่ 2 ของคนเดิมอาจเกิดหลังรีวิวถูกปิดไปแล้ว
    # ถ้าเช็ค pending_reviews ก่อนจะ return ทิ้งไปเลยโดยไม่ทันได้ลบปฏิกิริยาฝั่งตรงข้าม)
    other_emoji = REVIEW_REJECT_EMOJI if str(payload.emoji) == REVIEW_APPROVE_EMOJI else REVIEW_APPROVE_EMOJI
    try:
        channel = client.get_channel(payload.channel_id) or await client.fetch_channel(payload.channel_id)
        message = await channel.fetch_message(payload.message_id)
        member = payload.member
        if member is None and message.guild is not None:
            member = await message.guild.fetch_member(payload.user_id)
        if member is not None:
            await message.remove_reaction(other_emoji, member)
    except Exception as e:
        print(f"remove opposite reaction error: {e}")

    if payload.message_id not in pending_reviews:
        return  # ตรวจครบไปแล้วก่อนหน้านี้ แค่ลบปฏิกิริยาฝั่งตรงข้ามให้ก็พอ

    info = pending_reviews[payload.message_id]
    info["votes"][reviewer_name] = "approve" if str(payload.emoji) == REVIEW_APPROVE_EMOJI else "reject"

    if len(info["votes"]) < REVIEW_QUORUM:
        return  # ยังไม่ครบจำนวนคนตรวจขั้นต่ำ รอคนต่อไป

    info = pending_reviews.pop(payload.message_id)
    overall_approved = all(v == "approve" for v in info["votes"].values())
    voters_desc = "\n".join(
        f"{AVATARS.get(n,'')} {n}: {'ผ่าน ✅' if v == 'approve' else 'ต้องแก้ไข ❌'}"
        for n, v in info["votes"].items()
    )
    try:
        poster = client.get_user(info["poster_id"]) or await client.fetch_user(info["poster_id"])
        embed = discord.Embed(
            title=f"ผลตรวจสอบรูปของคุณ: {'ผ่าน ✅' if overall_approved else 'ต้องแก้ไข ❌'}",
            description=voters_desc,
            color=0x2ECC71 if overall_approved else 0xE74C3C,
            timestamp=datetime.now(TZ)
        )
        await poster.send(embed=embed)
    except Exception as e:
        print(f"notify poster review outcome error: {e}")

@client.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
    pending_reviews.pop(payload.message_id, None)

async def review_escalation():
    await client.wait_until_ready()
    while not client.is_closed():
        try:
            now = datetime.now(TZ)
            for msg_id, info in list(pending_reviews.items()):
                last = info["last_notified"] or info["posted_at"]
                if (now - last).total_seconds() < REVIEW_ESCALATE_SECONDS:
                    continue
                channel = client.get_channel(info["channel_id"])
                if not channel:
                    channel = await client.fetch_channel(info["channel_id"])
                try:
                    message = await channel.fetch_message(msg_id)
                except Exception:
                    pending_reviews.pop(msg_id, None)
                    continue
                poster_name = DISCORD_ID_TO_MEMBER.get(info["poster_id"])
                remaining = [
                    n for n in REVIEWER_NAMES
                    if n != poster_name and n not in info["votes"]
                ]
                if not remaining:
                    continue
                await notify_reviewers(message, target_names=remaining)
        except Exception as e:
            print(f"review escalation error: {e}")
        await asyncio.sleep(60)

_background_tasks_started = False

@client.event
async def on_ready():
    global _background_tasks_started
    await tree.sync()
    print(f"✅ NOVA พร้อมใช้งานแล้ว: {client.user}")
    # on_ready อาจถูกเรียกซ้ำได้ทุกครั้งที่ Discord gateway reconnect
    # กันไม่ให้สร้าง background task ซ้อนกันหลายชุดตลอดอายุ process
    if _background_tasks_started:
        return
    _background_tasks_started = True
    print(f"⏰ แจ้งเตือนทุกวัน เวลา 10:00, 12:30, 18:00 น.")
    client.loop.create_task(daily_notify())
    client.loop.create_task(deadline_alert())
    client.loop.create_task(review_escalation())
    client.loop.create_task(weekly_calendar_digest())
    print("🚨 เปิดระบบแจ้งเตือน deadline ล่วงหน้า 24 ชั่วโมงแล้ว")
    print("☀️ เปิดระบบ DM สรุปงานส่วนตัว 10:00 / 12:30 / 18:00 น. แล้ว")
    print("🖼️ เปิดระบบแจ้งเตือนตรวจสอบรูปแล้ว")
    print("🗓️ เปิดระบบสรุปปฏิทินสัปดาห์ทุกเช้าวันจันทร์แล้ว")

async def main():
    client.add_dynamic_items(DoneCardButton)
    client.add_view(QuickDoneView([{"id": "0", "name": "-"}], persistent=True))
    async with client:
        await client.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
