import discord
from discord import app_commands
import requests
import asyncio
import time
from functools import partial
from datetime import datetime, timedelta
import pytz

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
AVATARS = {"โดม": "🟣", "ไอซ์": "🟢", "พี": "🟡", "4ก": "🔵", "กาย": "🟠"}
MEMBERS = list(AVATARS.keys())

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

async def get_active_cards():
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
        if not member:
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
        result[member].append(f"{status} {card['name']}{due_str}")
    return result

async def get_active_cards_raw() -> list:
    lists = await get_lists()
    done_ids = {v for k, v in lists.items() if "เสร็จ" in k}
    cards = await get_all_cards()
    list_id_to_name = {v: k for k, v in lists.items()}
    result = []
    for card in cards:
        if card["idList"] in done_ids:
            continue
        list_name = list_id_to_name.get(card["idList"], "")
        member = next((m for m in MEMBERS if m in list_name), None)
        if not member:
            continue
        result.append(card)
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
        if member in ln:
            cards.append({**c, "list_name": ln})
    return cards

# ── discord setup ─────────────────────────────────────────────────
intents = discord.Intents.default()
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

# ── /status ───────────────────────────────────────────────────────
@tree.command(name="status", description="ดูสรุปงานของพวกมึงตอนนี้")
async def status_command(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        active = await get_active_cards()
        embed = discord.Embed(title="📋 งานที่พวกมึงยังไม่เสร็จ",
                              color=0x7F77DD,
                              timestamp=datetime.now(TZ))
        if not active:
            embed.description = "ว้าว! ไม่มีงานค้างเลยไอ้พวกนี้ ดีมากวะ 🎉"
        else:
            for member, cards in active.items():
                val = "\n".join(f"• {c}" for c in cards) or "ไม่มีงาน"
                embed.add_field(name=f"{AVATARS.get(member,'')} {member} ({len(cards)} งาน)",
                                value=val, inline=False)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ มึงทำอะไรพังอีกแล้ว: {e}")

# หมายเหตุ: view นี้ timeout=None แต่ยังไม่ persistent ข้าม redeploy จริง
# (ต้องใช้ discord.ui.DynamicItem ถึงจะรอด restart) — ถ้าบอท redeploy
# ระหว่างที่ข้อความค้างอยู่ ปุ่มจะกดไม่ติด ต้องไปกดเสร็จผ่าน /mywork แทน
class DeadlineDoneView(discord.ui.View):
    def __init__(self, card_id: str, card_url: str):
        super().__init__(timeout=None)
        self.card_id = card_id
        self.add_item(trello_link_button(card_url))

    @discord.ui.button(label="✅ เสร็จแล้ว", style=discord.ButtonStyle.success)
    async def btn_done(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        try:
            lists = await get_lists()
            target = next((v for k, v in lists.items() if "เสร็จ" in k), None)
            if not target:
                await interaction.followup.send("❌ หา list เสร็จแล้วไม่เจอวะ", ephemeral=True)
                return
            await move_card(self.card_id, target)
            button.disabled = True
            button.label = "✅ เสร็จแล้ว!"
            await interaction.message.edit(view=self)
        except Exception as e:
            await interaction.followup.send(f"❌ พังอีกแล้ว: {e}", ephemeral=True)

# ── background: deadline alert (เช็คทุก 5 นาที) ──────────────────
async def deadline_alert():
    await client.wait_until_ready()
    channel = client.get_channel(NOTIFY_CHANNEL_ID)
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
                    member = next((m for m in MEMBERS if m in list_name), "ทีม")

                    embed = discord.Embed(
                        title="🚨 DEADLINE ใกล้มาแล้วไอ้โง่! โฟกัสได้แล้ว!",
                        color=0xE24B4A, timestamp=now
                    )
                    embed.add_field(name="งาน", value=card["name"], inline=False)
                    embed.add_field(name="ผู้รับผิดชอบ", value=f"{AVATARS.get(member,'')} {member}", inline=True)
                    embed.add_field(name="เหลือเวลา", value=f"⏳ {int(diff_hours)} ชั่วโมงเท่านั้น!", inline=True)
                    embed.add_field(name="ครบกำหนด", value=due_dt.strftime("%-d/%-m/%Y %H:%M น."), inline=True)

                    if channel:
                        view = DeadlineDoneView(card_id=card["id"], card_url=card["url"])
                        await channel.send(embed=embed, view=view)
                    alerted.add(card["id"])
                    print(f"⚠️ แจ้งเตือน: {card['name']} เหลือ {int(diff_hours)} ชั่วโมง")

                elif diff_hours <= 0:
                    alerted.discard(card["id"])

        except Exception as e:
            print(f"deadline alert error: {e}")
        await asyncio.sleep(300)

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
                try:
                    channel = client.get_channel(NOTIFY_CHANNEL_ID)
                    if not channel:
                        channel = await client.fetch_channel(NOTIFY_CHANNEL_ID)
                    active = await get_active_cards()
                    embed = discord.Embed(
                        title=f"⏰ ไอ้พวกนี้ทำงานด้วยนะ! — {now.strftime('%-H:%M น.')}",
                        color=0xBA7517,
                        timestamp=now
                    )
                    if not active:
                        embed.description = "ไม่มีงานค้างวะ ดีงาม 🎉"
                        await channel.send(embed=embed)
                    else:
                        for member, cards in active.items():
                            val = "\n".join(f"• {c}" for c in cards)
                            embed.add_field(
                                name=f"{AVATARS.get(member,'')} {member} ({len(cards)} งาน)",
                                value=val, inline=False
                            )
                        active_raw = await get_active_cards_raw()
                        if len(active_raw) > 25:
                            embed.set_footer(text=f"เมนูเลือกกดเสร็จด้านล่างแสดงได้ 25 จาก {len(active_raw)} งาน เรียงตามกำหนดส่ง")
                        view = QuickDoneView(active_raw)
                        await channel.send(embed=embed, view=view)
                    print(f"✓ แจ้งเตือนประจำเวลา {h}:{mn:02d} สำเร็จ")
                except Exception as e:
                    print(f"notify error: {e}")
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
                "❌ กรอกเวลาให้ถูกต้องก่อนไอ้โง่ เช่น 13:30 หรือ 21.25", ephemeral=True
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
                await interaction.followup.send(f"❌ หา list ของ {ctx['member']} ไม่เจอวะ", ephemeral=True)
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
            body = {"name": card_name, "idList": lists[ctx["member"]]}
            if due_iso:
                body["due"] = due_iso
            if ctx.get("note"):
                body["desc"] = ctx["note"]
            loop = asyncio.get_running_loop()
            r = await loop.run_in_executor(None, partial(
                _request_with_retry, "POST",
                f"{TRELLO_BASE}/cards",
                **{"params": {"key": TRELLO_API_KEY, "token": TRELLO_TOKEN}, "json": body}
            ))
            card = r.json()
            invalidate_cache()
            await notify_assignment(ctx["member"], f"{cat_icon} {ctx.get('category','')} — {ctx['task']}", due_display)
            embed = discord.Embed(title="✅ ยัด Card เข้า Trello แล้ว ทำซะนะ!", color=0x0052cc)
            embed.add_field(name="สมาชิก", value=f"{AVATARS.get(ctx['member'],'')} {ctx['member']}", inline=True)
            embed.add_field(name="หัวข้อ", value=f"{cat_icon} {ctx.get('category','')}", inline=True)
            embed.add_field(name="งาน", value=ctx["task"], inline=False)
            if due_display:
                embed.add_field(name="กำหนดส่ง", value=due_display, inline=True)
            if ctx.get("note"):
                embed.add_field(name="หมายเหตุ", value=ctx["note"], inline=False)
            link_view = discord.ui.View()
            link_view.add_item(trello_link_button(card["url"]))
            await interaction.edit_original_response(content=None, embed=embed, view=link_view)
        except Exception as e:
            await interaction.edit_original_response(content=f"❌ พังอีกแล้ว: {e}", view=None)

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
                "❌ กรอกวันให้ถูกต้องก่อนไอ้โง่ เช่น 15/6 หรือ 15/6/2569", ephemeral=True
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
                await interaction.followup.send(f"❌ หา list ของ {ctx['member']} ไม่เจอวะ")
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

            body = {"name": card_name, "idList": lists[ctx["member"]]}
            if due_iso:
                body["due"] = due_iso
            if ctx.get("note"):
                body["desc"] = ctx["note"]

            loop = asyncio.get_running_loop()
            r = await loop.run_in_executor(None, partial(
                _request_with_retry, "POST",
                f"{TRELLO_BASE}/cards",
                **{"params": {"key": TRELLO_API_KEY, "token": TRELLO_TOKEN}, "json": body}
            ))
            card = r.json()
            invalidate_cache()
            await notify_assignment(ctx["member"], f"{cat_icon} {ctx.get('category','')} — {ctx['task']}", due_display)

            embed = discord.Embed(title="✅ ยัด Card เข้า Trello แล้ว ทำซะนะ!", color=0x0052cc)
            embed.add_field(name="สมาชิก", value=f"{AVATARS.get(ctx['member'],'')} {ctx['member']}", inline=True)
            embed.add_field(name="หัวข้อ", value=f"{cat_icon} {ctx.get('category','')}", inline=True)
            embed.add_field(name="งาน", value=ctx["task"], inline=False)
            if due_display:
                embed.add_field(name="กำหนดส่ง", value=due_display, inline=True)
            if ctx.get("note"):
                embed.add_field(name="หมายเหตุ", value=ctx["note"], inline=False)
            link_view = discord.ui.View()
            link_view.add_item(trello_link_button(card["url"]))
            await interaction.followup.edit_message(interaction.message.id, content=None, embed=embed, view=link_view)
        except Exception as e:
            await interaction.followup.send(f"❌ พังอีกแล้ว: {e}")

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
    def __init__(self, member: str):
        options = [
            discord.SelectOption(label=f"{ic} {nm}", value=nm)
            for ic, nm in CATEGORIES
        ]
        super().__init__(placeholder="เลือกหัวข้องาน...", options=options)
        self.member = member

    async def callback(self, interaction: discord.Interaction):
        ctx = {"member": self.member, "category": self.values[0]}
        await interaction.response.send_modal(CardDetailModal(ctx=ctx))

class CategorySelectView(discord.ui.View):
    def __init__(self, member: str):
        super().__init__(timeout=60)
        self.add_item(CategorySelect(member=member))
        add_cancel_button(self)

    async def on_timeout(self):
        pass

# ── Step 1: เลือกสมาชิก ─────────────────────────────────────────
class MemberSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="🟣 โดม", value="โดม", description="Web master, Support"),
            discord.SelectOption(label="🟢 ไอซ์", value="ไอซ์", description="Video, Support, Promotion Shopee, General Affairs"),
            discord.SelectOption(label="🟡 พี",   value="พี",   description="หน้าร้าน, เบรค, ADS, Event BIC, Merchandise, Support"),
            discord.SelectOption(label="🔵 4ก",   value="4ก",   description="งานกลาง / มอบหมายร่วม"),
            discord.SelectOption(label="🟠 กาย",  value="กาย",  description="Grading, Support, Information"),
        ]
        super().__init__(placeholder="เลือกสมาชิกที่รับผิดชอบ...", options=options)

    async def callback(self, interaction: discord.Interaction):
        member = self.values[0]
        view = CategorySelectView(member=member)
        await interaction.response.edit_message(
            content=f"**ขั้นตอน 2/5** — เลือกหัวข้องาน\n👤 สมาชิก: **{member}**",
            view=view
        )

class MemberSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(MemberSelect())
        add_cancel_button(self)

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

    @discord.ui.button(label="✅ ทำเสร็จแล้วว่ะ!", style=discord.ButtonStyle.success)
    async def btn_done(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        try:
            lists = await get_lists()
            target = next((v for k, v in lists.items() if "เสร็จ" in k), None)
            if not target:
                await interaction.followup.send("❌ หา list เสร็จแล้วไม่เจอวะ")
                return
            await move_card(self.card_id, target)
            await interaction.followup.edit_message(
                interaction.message.id,
                content=f"✅ **{self.card_name}** → เสร็จแล้ว! ในที่สุดก็ทำได้ 🎉",
                view=None
            )
        except Exception as e:
            await interaction.followup.send(f"❌ พังอีกแล้ว: {e}")

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
    def __init__(self):
        options = [
            discord.SelectOption(label="🟣 โดม", value="โดม"),
            discord.SelectOption(label="🟢 ไอซ์", value="ไอซ์"),
            discord.SelectOption(label="🟡 พี",   value="พี"),
            discord.SelectOption(label="🔵 4ก",   value="4ก"),
            discord.SelectOption(label="🟠 กาย",  value="กาย"),
        ]
        super().__init__(placeholder="เลือกสมาชิก...", options=options)

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
                content=f"✅ {member} ไม่มีงานค้างแล้ว ดีงาม!", view=None
            )
            return
        view = TaskSelectView(member=member, cards=cards)
        note = cards_cap_note(len(cards))
        await interaction.response.edit_message(
            content=f"**งานของไอ้ {member}** — เลือกงานที่จะเปลี่ยนสถานะ:{note}",
            view=view
        )

class MemberForStatusView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(MemberForStatusSelect())
        add_cancel_button(self)

    async def on_timeout(self):
        pass

@tree.command(name="changestatus", description="เปลี่ยนสถานะงานแบบเลือก dropdown")
async def changestatus_command(interaction: discord.Interaction):
    if not await require_graphics_team(interaction):
        return
    view = MemberForStatusView()
    await interaction.response.send_message(
        "**🔄 เปลี่ยนสถานะงาน** — เลือกไอ้คนที่ทำงานเสร็จก่อนเลย",
        view=view, ephemeral=True
    )

@tree.command(name="newcard", description="สร้างงานใหม่แบบ step-by-step")
async def newcard_command(interaction: discord.Interaction):
    view = MemberSelectView()
    await interaction.response.send_message(
        "**📋 สร้างงานใหม่** — งานนี้จะให้ใครทำวะ?",
        view=view, ephemeral=True
    )

# ── /mywork: ดูงานตัวเอง + ปุ่มกดเสร็จ ──────────────────────────
class QuickDoneSelect(discord.ui.Select):
    def __init__(self, cards: list):
        options = [
            discord.SelectOption(label=c["name"][:90], value=c["id"])
            for c in sort_by_due(cards)[:25]
        ]
        super().__init__(placeholder="เลือกงานที่ทำเสร็จแล้ว...", options=options)

    async def callback(self, interaction: discord.Interaction):
        card_id = self.values[0]
        card_name = next(o.label for o in self.options if o.value == card_id)
        await interaction.response.defer()
        try:
            lists = await get_lists()
            done_list = next((v for k, v in lists.items() if "เสร็จ" in k), None)
            await move_card(card_id, done_list)
            await interaction.edit_original_response(
                content=f"✅ **{card_name}** → เสร็จแล้ว! ในที่สุดก็ทำได้ 🎉", view=None
            )
        except Exception as e:
            await interaction.edit_original_response(content=f"❌ พังอีกแล้ว: {e}", view=None)

class QuickDoneView(discord.ui.View):
    def __init__(self, cards: list):
        super().__init__(timeout=120)
        if cards:
            self.add_item(QuickDoneSelect(cards))

    async def on_timeout(self):
        pass

MEMBER_CHOICES = [
    app_commands.Choice(name="โดม", value="โดม"),
    app_commands.Choice(name="ไอซ์", value="ไอซ์"),
    app_commands.Choice(name="พี", value="พี"),
    app_commands.Choice(name="4ก", value="4ก"),
    app_commands.Choice(name="กาย", value="กาย"),
]

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
                "❌ บอทไม่รู้จักว่ามึงคือใคร ลองระบุพารามิเตอร์ ชื่อ เอาเอง", ephemeral=True
            )
            return
    try:
        cards = await fetch_member_cards(member)
        embed = discord.Embed(
            title=f"{AVATARS.get(member,'')} งานของไอ้ {member} ที่ยังค้างอยู่",
            color=0x7F77DD
        )
        if not cards:
            embed.description = "ไม่มีงานค้างวะ ดีงาม 🎉"
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
        await interaction.followup.send(f"❌ พังอีกแล้ว: {e}", ephemeral=True)

# ── DM สรุปงานตอนเช้า ─────────────────────────────────────────────
MEMBER_DISCORD_IDS = {
    "โดม": 1447848699718799390,
    "ไอซ์": 1447848730781945876,
    "พี":   1448155823623639162,
    "กาย":  1519904425672446073,
}
DISCORD_ID_TO_MEMBER = {v: k for k, v in MEMBER_DISCORD_IDS.items()}
MORNING_DM_TIME = (10, 0)

# ── การมอบหมายงานใหม่: DM แจ้งทันที (4ก = DM ทุกคนในทีมแยกกัน) ────
async def notify_assignment(member: str, task_text: str, due_display: str = None):
    targets = list(MEMBER_DISCORD_IDS.keys()) if member == "4ก" else [member]
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
            await user.send(embed=embed)
        except Exception as e:
            print(f"notify_assignment error ({name}): {e}")

# ── จำกัดสิทธิ์: /editcard, /changestatus ใช้ได้เฉพาะทีมกราฟิก ────
def is_graphics_team(interaction: discord.Interaction) -> bool:
    return interaction.user.id in MEMBER_DISCORD_IDS.values()

async def require_graphics_team(interaction: discord.Interaction) -> bool:
    if not is_graphics_team(interaction):
        await interaction.response.send_message(
            "❌ คำสั่งนี้ใช้ได้เฉพาะทีมกราฟิกเท่านั้นวะ", ephemeral=True
        )
        return False
    return True

async def morning_dm():
    await client.wait_until_ready()
    last_dm_date = None
    while not client.is_closed():
        now = datetime.now(TZ)
        today = now.date()
        if now.hour == MORNING_DM_TIME[0] and now.minute == MORNING_DM_TIME[1] and last_dm_date != today:
            last_dm_date = today
            for member, uid in MEMBER_DISCORD_IDS.items():
                try:
                    cards = await fetch_member_cards(member)
                    user = await client.fetch_user(uid)
                    embed = discord.Embed(
                        title=f"☀️ ตื่นได้แล้วไอ้ {member}! มาดูงานซะ",
                        description="งานมึงวันนี้" if cards else "ไม่มีงานค้างวะ ดีงาม 🎉",
                        color=0xBA7517, timestamp=now
                    )
                    for c in cards:
                        due = ""
                        if c.get("due"):
                            try:
                                dd = datetime.fromisoformat(c["due"].replace("Z", "+00:00")).astimezone(TZ)
                                due = f" (ครบ {dd.strftime('%-d/%-m %H:%M')} น.)"
                            except Exception:
                                pass
                        embed.add_field(name=c["name"], value=f"{c['list_name']}{due}", inline=False)
                    await user.send(embed=embed)
                    print(f"✓ ส่ง DM เช้าให้ {member} แล้ว")
                except Exception as e:
                    print(f"morning dm error ({member}): {e}")
        await asyncio.sleep(30)

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
            title="💀 งานที่เลยกำหนดแล้วไอ้พวกขี้ลืม!",
            color=0xFF0000, timestamp=now
        )
        if not overdue_items:
            embed.description = "ไม่มีงานเลยกำหนดเลยวะ ดีงาม 🎉"
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
            view = QuickDoneView([item["card"] for item in overdue_items])
            await interaction.followup.send(embed=embed, view=view)
            return
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ พังอีกแล้ว: {e}")

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
        matches = [
            c for c in cards
            if keyword in c.get("name", "").lower() or keyword in (c.get("desc") or "").lower()
        ]
        matches = sort_by_due(matches)

        embed = discord.Embed(
            title=f"🔍 ผลค้นหา: \"{คำค้น}\"",
            color=0x7F77DD, timestamp=datetime.now(TZ)
        )
        if not matches:
            embed.description = "ไม่เจอ card ที่ตรงกับคำนี้เลยวะ"
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
        await interaction.followup.send(f"❌ พังอีกแล้ว: {e}")

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
            await interaction.response.edit_message(content=f"❌ พังอีกแล้ว: {e}", view=None)

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
            await interaction.followup.send(f"❌ พังอีกแล้ว: {e}")

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
                content=f"❌ หา list ของ {new_member} ไม่เจอวะ", view=None
            )
            return
        await move_card(self.card["id"], target)
        await notify_assignment(new_member, self.card["name"])
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
            content=f"**{self.card['name']}**\nย้ายให้ใครวะ?", view=view
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
            content=f"**{card['name']}**\nจะแก้อะไรวะ?", view=view
        )

class EditCardSelectView(discord.ui.View):
    def __init__(self, cards: list):
        super().__init__(timeout=60)
        self.add_item(EditCardSelect(cards))
        add_cancel_button(self)

    async def on_timeout(self):
        pass

class EditMemberSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=f"{AVATARS.get(m,'')} {m}", value=m)
            for m in MEMBERS
        ]
        super().__init__(placeholder="เลือกสมาชิก...", options=options)

    async def callback(self, interaction: discord.Interaction):
        member = self.values[0]
        cards = await fetch_member_cards(member)
        if not cards:
            await interaction.response.edit_message(
                content=f"✅ {member} ไม่มีงานค้างวะ", view=None
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

@tree.command(name="editcard", description="แก้ไขงานที่จดผิด")
async def editcard_command(interaction: discord.Interaction):
    if not await require_graphics_team(interaction):
        return
    view = EditMemberSelectView()
    await interaction.response.send_message(
        "**✏️ แก้ไขงาน** — งานของใครวะ?",
        view=view, ephemeral=True
    )

@client.event
async def on_ready():
    await tree.sync()
    print(f"✅ Bot พร้อมใช้งาน: {client.user}")
    print(f"⏰ แจ้งเตือนทุกวัน เวลา 10:00, 12:30, 18:00 น.")
    client.loop.create_task(daily_notify())
    client.loop.create_task(deadline_alert())
    client.loop.create_task(morning_dm())
    print("🚨 เปิดระบบแจ้งเตือน deadline ล่วงหน้า 24 ชั่วโมงแล้ว")
    print("☀️ เปิดระบบ DM สรุปงานตอนเช้า 10:00 น. แล้ว")

async def main():
    async with client:
        await client.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
