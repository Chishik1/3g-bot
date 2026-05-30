import discord
from discord import app_commands
import requests
import asyncio
from datetime import datetime
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
NOTIFY_TIMES = [(10, 30), (12, 30), (18, 30)]  # ชั่วโมง, นาที
 
def _get_lists_sync():
    r = requests.get(f"{TRELLO_BASE}/boards/{TRELLO_BOARD_ID}/lists",
                     params={"key": TRELLO_API_KEY, "token": TRELLO_TOKEN})
    return {lst["name"]: lst["id"] for lst in r.json()}
 
async def get_lists():
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _get_lists_sync)
 
def _find_card_sync(name):
    r = requests.get(f"{TRELLO_BASE}/boards/{TRELLO_BOARD_ID}/cards",
                     params={"key": TRELLO_API_KEY, "token": TRELLO_TOKEN})
    cards = r.json()
    name_lower = name.lower()
    exact = [c for c in cards if c["name"].lower() == name_lower]
    if exact:
        return exact[0]
    matched = [c for c in cards if name_lower in c["name"].lower()]
    return matched[0] if matched else None
 
async def find_card(name):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(_find_card_sync, name))
 
def _move_card_sync(card_id, list_id):
    r = requests.put(f"{TRELLO_BASE}/cards/{card_id}",
                     params={"key": TRELLO_API_KEY, "token": TRELLO_TOKEN},
                     json={"idList": list_id})
    return r.json()
 
async def move_card(card_id, list_id):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(_move_card_sync, card_id, list_id))
 
async def get_active_cards():
    lists = await get_lists()
    done_ids = {v for k, v in lists.items() if "เสร็จ" in k}
    r = requests.get(f"{TRELLO_BASE}/boards/{TRELLO_BOARD_ID}/cards",
                     params={"key": TRELLO_API_KEY, "token": TRELLO_TOKEN})
    cards = r.json()
    list_id_to_name = {v: k for k, v in lists.items()}
    result = {}
    for card in cards:
        if card["idList"] in done_ids:
            continue
        list_name = list_id_to_name.get(card["idList"], "?")
        member = next((m for m in ["โดม", "ไอซ์", "พี"] if m in list_name), None)
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
 
def parse_due(กำหนดส่ง: str):
    parts = กำหนดส่ง.strip().replace(" ", "").split("/")
    d, m = int(parts[0]), int(parts[1])
    y = int(parts[2]) - 543 if len(parts) >= 3 and int(parts[2]) > 100 else (
        int(parts[2]) if len(parts) >= 3 else datetime.now(TZ).year
    )
    time_part = "23:59"
    if len(parts) >= 4:
        time_part = parts[3].replace(".", ":")
    elif len(parts) == 3 and "." in parts[2]:
        sub = parts[2].split(".")
        y = int(sub[0]) - 543 if int(sub[0]) > 100 else int(sub[0])
        time_part = sub[1] + ":" + (sub[2] if len(sub) > 2 else "00")
    h, mn = map(int, time_part.split(":"))
    local_dt = TZ.localize(datetime(y, m, d, h, mn))
    return local_dt.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
 
intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
 
@tree.command(name="status", description="ดูสรุปงานของทีมตอนนี้")
async def status_command(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        active = await get_active_cards()
        embed = discord.Embed(title="📋 สรุปงานทีมตอนนี้",
                              color=0x7F77DD,
                              timestamp=datetime.now(TZ))
        if not active:
            embed.description = "ไม่มีงานค้างอยู่ในขณะนี้ 🎉"
        else:
            avatars = {"โดม": "🟣", "ไอซ์": "🟢", "พี": "🟡"}
            for member, cards in active.items():
                val = "\n".join(f"• {c}" for c in cards) or "ไม่มีงาน"
                embed.add_field(name=f"{avatars.get(member,'')} {member} ({len(cards)} งาน)",
                                value=val, inline=False)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ เกิดข้อผิดพลาด: {e}")
 
async def deadline_alert():
    await client.wait_until_ready()
    channel = client.get_channel(NOTIFY_CHANNEL_ID)
    alerted = set()
    while not client.is_closed():
        try:
            now = datetime.now(TZ)
            r = requests.get(f"{TRELLO_BASE}/boards/{TRELLO_BOARD_ID}/cards",
                             params={"key": TRELLO_API_KEY, "token": TRELLO_TOKEN})
            lists = await get_lists()
            done_ids = {v for k, v in lists.items() if "เสร็จ" in k}
            list_id_to_name = {v: k for k, v in lists.items()}
 
            for card in r.json():
                if not card.get("due"):
                    continue
                if card["idList"] in done_ids:
                    continue
                if card["id"] in alerted:
                    continue
 
                due_dt = datetime.fromisoformat(card["due"].replace("Z", "+00:00")).astimezone(TZ)
                diff_hours = (due_dt - now).total_seconds() / 3600
 
                if 0 < diff_hours <= 24:
                    list_name = list_id_to_name.get(card["idList"], "")
                    member = next((m for m in ["โดม", "ไอซ์", "พี"] if m in list_name), "ทีม")
                    avatars = {"โดม": "🟣", "ไอซ์": "🟢", "พี": "🟡"}
 
                    embed = discord.Embed(title="🚨 ใกล้ถึง Deadline แล้ว!", color=0xE24B4A, timestamp=now)
                    embed.add_field(name="งาน", value=card["name"], inline=False)
                    embed.add_field(name="ผู้รับผิดชอบ", value=f"{avatars.get(member,'')} {member}", inline=True)
                    embed.add_field(name="เหลือเวลา", value=f"⏳ {int(diff_hours)} ชั่วโมง", inline=True)
                    embed.add_field(name="ครบกำหนด", value=due_dt.strftime("%-d/%-m/%Y %H:%M น."), inline=True)
                    embed.add_field(name="ลิงก์", value=f"[เปิดใน Trello]({card['url']})", inline=False)
 
                    if channel:
                        await channel.send(embed=embed)
                    alerted.add(card["id"])
                    print(f"⚠️ แจ้งเตือน: {card['name']} เหลือ {int(diff_hours)} ชั่วโมง")
 
                elif diff_hours <= 0:
                    alerted.discard(card["id"])
 
        except Exception as e:
            print(f"deadline alert error: {e}")
        await asyncio.sleep(300)  # เช็คทุก 5 นาที
 
async def daily_notify():
    await client.wait_until_ready()
    channel = client.get_channel(NOTIFY_CHANNEL_ID)
    while not client.is_closed():
        now = datetime.now(TZ)
        for h, mn in NOTIFY_TIMES:
            if now.hour == h and now.minute == mn:
                try:
                    active = await get_active_cards()
                    embed = discord.Embed(
                        title=f"⏰ อัปเดตงานทีม — {now.strftime('%-H:%M น.')}",
                        color=0xBA7517,
                        timestamp=now
                    )
                    if not active:
                        embed.description = "ไม่มีงานค้างอยู่ 🎉"
                    else:
                        avatars = {"โดม": "🟣", "ไอซ์": "🟢", "พี": "🟡"}
                        for member, cards in active.items():
                            val = "\n".join(f"• {c}" for c in cards)
                            embed.add_field(
                                name=f"{avatars.get(member,'')} {member} ({len(cards)} งาน)",
                                value=val, inline=False
                            )
                    if channel:
                        await channel.send(embed=embed)
                    else:
                        print(f"⚠️ ไม่พบ channel ID {NOTIFY_CHANNEL_ID}")
                except Exception as e:
                    print(f"notify error: {e}")
                await asyncio.sleep(61)
                break
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
        time_str = self.เวลา.value.strip().replace(".", ":")
        try:
            h, mn = map(int, time_str.split(":"))
            time_str = f"{h}:{mn:02d}"
        except Exception:
            await interaction.response.send_message("❌ รูปแบบเวลาไม่ถูกต้อง เช่น 13:30 หรือ 8.00", ephemeral=True)
            return
        # Modal ต้อง respond ก่อน แล้วค่อย followup
        await interaction.response.edit_message(
            content=f"**กำลังสร้าง card...** ⏳\n📅 เวลา: **{time_str} น.**",
            view=None
        )
        ctx = self.ctx
        try:
            lists = await get_lists()
            if ctx["member"] not in lists:
                await interaction.followup.send(f"❌ ไม่พบ list ของ {ctx['member']}", ephemeral=True)
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
            loop = asyncio.get_event_loop()
            r = await loop.run_in_executor(None, partial(
                requests.post,
                f"{TRELLO_BASE}/cards",
                **{"params": {"key": TRELLO_API_KEY, "token": TRELLO_TOKEN}, "json": body}
            ))
            card = r.json()
            avatars = {"โดม": "🟣", "ไอซ์": "🟢", "พี": "🟡"}
            embed = discord.Embed(title="✅ สร้าง Card สำเร็จ!", color=0x0052cc)
            embed.add_field(name="สมาชิก", value=f"{avatars.get(ctx['member'],'')} {ctx['member']}", inline=True)
            embed.add_field(name="หัวข้อ", value=f"{cat_icon} {ctx.get('category','')}", inline=True)
            embed.add_field(name="งาน", value=ctx["task"], inline=False)
            if due_display:
                embed.add_field(name="กำหนดส่ง", value=due_display, inline=True)
            if ctx.get("note"):
                embed.add_field(name="หมายเหตุ", value=ctx["note"], inline=False)
            embed.add_field(name="ลิงก์", value=f"[เปิดใน Trello]({card['url']})", inline=False)
            await interaction.edit_original_response(content=None, embed=embed, view=None)
        except Exception as e:
            await interaction.edit_original_response(content=f"❌ {e}", view=None)
 
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
            await interaction.response.send_message("❌ รูปแบบวันไม่ถูกต้อง เช่น 15/6 หรือ 15/6/2569", ephemeral=True)
 
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
 
    def _make_btn(self, label):
        btn = discord.ui.Button(
            label=label,
            style=discord.ButtonStyle.secondary if label != "ไม่ระบุ" else discord.ButtonStyle.danger
        )
        async def cb(interaction: discord.Interaction, _label=label):
            await self._submit(interaction, _label)
        btn.callback = cb
        return btn
 
    async def _submit(self, interaction: discord.Interaction, time_str: str):
        await interaction.response.defer()
        ctx = self.ctx
        try:
            lists = await get_lists()
            if ctx["member"] not in lists:
                await interaction.followup.send(f"❌ ไม่พบ list ของ {ctx['member']}")
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
 
            r = requests.post(f"{TRELLO_BASE}/cards",
                              params={"key": TRELLO_API_KEY, "token": TRELLO_TOKEN},
                              json=body)
            card = r.json()
 
            avatars = {"โดม": "🟣", "ไอซ์": "🟢", "พี": "🟡"}
            embed = discord.Embed(title="✅ สร้าง Card สำเร็จ!", color=0x0052cc)
            embed.add_field(name="สมาชิก", value=f"{avatars.get(ctx['member'],'')} {ctx['member']}", inline=True)
            embed.add_field(name="หัวข้อ", value=f"{cat_icon} {ctx.get('category','')}", inline=True)
            embed.add_field(name="งาน", value=ctx["task"], inline=False)
            if due_display:
                embed.add_field(name="กำหนดส่ง", value=due_display, inline=True)
            if ctx.get("note"):
                embed.add_field(name="หมายเหตุ", value=ctx["note"], inline=False)
            embed.add_field(name="ลิงก์", value=f"[เปิดใน Trello]({card['url']})", inline=False)
            await interaction.followup.edit_message(interaction.message.id, content=None, embed=embed, view=None)
        except Exception as e:
            await interaction.followup.send(f"❌ {e}")
 
# ── Step 3: เลือกวันที่ ──────────────────────────────────────────
class DateSelectView(discord.ui.View):
    def __init__(self, ctx: dict):
        super().__init__(timeout=120)
        self.ctx = ctx
        from datetime import timedelta
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
 
# ── Step 1: เลือกสมาชิก ─────────────────────────────────────────
class MemberSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="🟣 โดม", value="โดม", description="เว็บไซต์ & เกรดดิ้ง"),
            discord.SelectOption(label="🟢 ไอซ์", value="ไอซ์", description="วิดีโอ, เว็บ & หน้าร้าน"),
            discord.SelectOption(label="🟡 พี",   value="พี",   description="หน้าร้าน & อีเว้นท์"),
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
 
# ── เปลี่ยนสถานะงานแบบ dropdown ────────────────────────────────
class TaskStatusSelect(discord.ui.Select):
    def __init__(self, member: str, cards: list):
        options = [
            discord.SelectOption(
                label=c["name"][:80],
                value=c["id"],
                description=c.get("list_name", "")[:50]
            ) for c in cards[:25]
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
 
    @discord.ui.button(label="✅ ทำเสร็จแล้ว!", style=discord.ButtonStyle.success)
    async def btn_done(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        try:
            lists = await get_lists()
            target = next((v for k, v in lists.items() if "เสร็จ" in k), None)
            if not target:
                await interaction.followup.send("❌ ไม่พบ list เสร็จแล้ว")
                return
            await move_card(self.card_id, target)
            await interaction.followup.edit_message(
                interaction.message.id,
                content=f"✅ **{self.card_name}** → เสร็จแล้ว! 🎉",
                view=None
            )
        except Exception as e:
            await interaction.followup.send(f"❌ {e}")
 
class TaskSelectView(discord.ui.View):
    def __init__(self, member: str, cards: list):
        super().__init__(timeout=60)
        self.add_item(TaskStatusSelect(member=member, cards=cards))
 
class MemberForStatusSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="🟣 โดม", value="โดม"),
            discord.SelectOption(label="🟢 ไอซ์", value="ไอซ์"),
            discord.SelectOption(label="🟡 พี",   value="พี"),
        ]
        super().__init__(placeholder="เลือกสมาชิก...", options=options)
 
    async def callback(self, interaction: discord.Interaction):
        member = self.values[0]
        lists = await get_lists()
        done_ids = {v for k, v in lists.items() if "เสร็จ" in k}
        list_id_to_name = {v: k for k, v in lists.items()}
        r = requests.get(f"{TRELLO_BASE}/boards/{TRELLO_BOARD_ID}/cards",
                         params={"key": TRELLO_API_KEY, "token": TRELLO_TOKEN})
        all_cards = r.json()
        cards = [
            {**c, "list_name": list_id_to_name.get(c["idList"], "")}
            for c in all_cards
            if member in list_id_to_name.get(c["idList"], "") or
               list_id_to_name.get(c["idList"], "") in ["🔄 กำลังทำ", "กำลังทำ"]
            if c["idList"] not in done_ids
        ]
        if not cards:
            await interaction.response.edit_message(
                content=f"✅ {member} ไม่มีงานค้างอยู่แล้ว!", view=None
            )
            return
        view = TaskSelectView(member=member, cards=cards)
        await interaction.response.edit_message(
            content=f"**งานของ {member}** — เลือกงานที่ต้องการเปลี่ยนสถานะ:",
            view=view
        )
 
class MemberForStatusView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(MemberForStatusSelect())
 
@tree.command(name="changestatus", description="เปลี่ยนสถานะงานแบบเลือก dropdown")
async def changestatus_command(interaction: discord.Interaction):
    view = MemberForStatusView()
    await interaction.response.send_message(
        "**🔄 เปลี่ยนสถานะงาน** — เลือกสมาชิกก่อนครับ",
        view=view, ephemeral=True
    )
 
@tree.command(name="newcard", description="สร้างงานใหม่แบบ step-by-step")
async def newcard_command(interaction: discord.Interaction):
    view = MemberSelectView()
    await interaction.response.send_message(
        "**📋 สร้างงานใหม่** — เลือกสมาชิกก่อนครับ",
        view=view, ephemeral=True
    )
 
@client.event
async def on_ready():
    await tree.sync()
    print(f"✅ Bot พร้อมใช้งาน: {client.user}")
    print(f"⏰ จะแจ้งเตือนทุกวัน เวลา 10:30, 12:30, 18:30 น.")
    client.loop.create_task(daily_notify())
    client.loop.create_task(deadline_alert())
    print('🚨 เปิดระบบแจ้งเตือน deadline ล่วงหน้า 24 ชั่วโมงแล้ว')
 
client.run(DISCORD_TOKEN)
