import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import asyncio
from flask import Flask
from threading import Thread

# ---------- إعدادات ----------
INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.members = True

bot = commands.Bot(command_prefix="!", intents=INTENTS)

PRODUCTS_FILE = "products.json"
CONFIG_FILE = "config.json"

# ---------- وظائف المنتجات ----------
def load_products():
    try:
        with open(PRODUCTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        initial = {"last_id": 0, "products": {}}
        with open(PRODUCTS_FILE, "w", encoding="utf-8") as f:
            json.dump(initial, f, ensure_ascii=False, indent=2)
        return initial

def save_products(data):
    with open(PRODUCTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

PRODUCTS = load_products()

def load_config():
    cfg = {"store_channel_id": 0, "ticket_category_id": 0}
    try:
        if os.getenv("STORE_CHANNEL_ID"):
            cfg["store_channel_id"] = int(os.getenv("STORE_CHANNEL_ID"))
        if os.getenv("TICKET_CATEGORY_ID"):
            cfg["ticket_category_id"] = int(os.getenv("TICKET_CATEGORY_ID"))
    except:
        pass
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            file_cfg = json.load(f)
            if file_cfg.get("store_channel_id"):
                cfg["store_channel_id"] = int(file_cfg["store_channel_id"])
            if file_cfg.get("ticket_category_id"):
                cfg["ticket_category_id"] = int(file_cfg["ticket_category_id"])
    except FileNotFoundError:
        pass
    return cfg

CFG = load_config()

def is_admin_member(member: discord.Member):
    perms = member.guild_permissions
    return perms.administrator or perms.manage_guild or perms.manage_messages

# ---------- Views و Modals ----------
class AddProductModal(discord.ui.Modal, title="إضافة منتج جديد"):
    name = discord.ui.TextInput(label="اسم المنتج", placeholder="مثال: سماعات بلوتوث", max_length=100)
    description = discord.ui.TextInput(label="تفاصيل المنتج", style=discord.TextStyle.long, placeholder="اكتب وصف المنتج", max_length=1000)
    image_url = discord.ui.TextInput(label="رابط الصورة (يمكن تركه فارغًا)", required=False, placeholder="رابط مباشر لصورة (jpg/png)")
    price = discord.ui.TextInput(label="السعر", placeholder="مثال: 150 ريال", max_length=50)

    def __init__(self, invoker: discord.Member, admin_channel_id: int):
        super().__init__()
        self.invoker = invoker
        self.admin_channel_id = admin_channel_id

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin_member(self.invoker):
            await interaction.response.send_message("ليس لديك صلاحية لإضافة منتجات.", ephemeral=True)
            return

        global PRODUCTS
        PRODUCTS["last_id"] += 1
        pid = str(PRODUCTS["last_id"])
        PRODUCTS["products"][pid] = {
            "id": pid,
            "name": self.name.value,
            "description": self.description.value,
            "image_url": self.image_url.value.strip(),
            "price": self.price.value.strip(),
            "creator_id": self.invoker.id
        }
        save_products(PRODUCTS)

        store_channel_id = CFG.get("store_channel_id", 0)
        channel = interaction.client.get_channel(store_channel_id)
        embed = discord.Embed(title=self.name.value, description=self.description.value)
        embed.set_footer(text=f"السعر: {self.price.value} • رقم المنتج: {pid}")
        if self.image_url.value.strip():
            try:
                embed.set_image(url=self.image_url.value.strip())
            except:
                pass
        view = ProductView(pid)
        if channel:
            await channel.send(embed=embed, view=view)
            await interaction.response.send_message("تم إضافة المنتج ونشره في قناة المتجر.", ephemeral=True)
        else:
            await interaction.response.send_message("حفظت المنتج محليًا لكن قناة المتجر غير معرفة. عيّن STORE_CHANNEL_ID أو /setstore.", ephemeral=True)

class ProductView(discord.ui.View):
    def __init__(self, product_id: str):
        super().__init__(timeout=None)
        self.product_id = product_id

    @discord.ui.button(label="🛒 شراء المنتج", style=discord.ButtonStyle.primary, custom_id="buy_product_button")
    async def buy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        pid = self.product_id
        guild = interaction.guild
        cfg_cat = CFG.get("ticket_category_id", 0)
        if cfg_cat == 0:
            await interaction.response.send_message("قسم التذاكر غير معرف. تواصل مع الإدارة.", ephemeral=True)
            return
        category = discord.utils.get(guild.categories, id=cfg_cat)
        if category is None:
            await interaction.response.send_message("قسم التذاكر غير موجود على هذا السيرفر. تحقق من إعدادات البوت.", ephemeral=True)
            return

        user = interaction.user
        safe_name = user.name.lower().replace(" ", "-")[:15]
        channel_name = f"ticket-{safe_name}-{pid}"
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        ticket_channel = await guild.create_text_channel(name=channel_name, overwrites=overwrites, category=category, reason=f"تذكرة شراء المنتج {pid}")
        await ticket_channel.edit(topic=json.dumps({"product_id": pid, "buyer_id": user.id}))
        prod = PRODUCTS["products"].get(pid, {})
        embed = discord.Embed(title=f"طلب شراء: {prod.get('name','غير معروف')}", description=f"العميل: {user.mention}\nالسعر: {prod.get('price','غير معروف')}")
        view = TicketView(ticket_channel.id, buyer_id=user.id)
        await ticket_channel.send(content=f"{user.mention} فتح تذكرة شراء للمنتج.", embed=embed, view=view)
        await interaction.response.send_message(f"تم فتح تذكرة في {ticket_channel.mention}", ephemeral=True)

class TicketView(discord.ui.View):
    def __init__(self, ticket_channel_id: int, buyer_id: int):
        super().__init__(timeout=None)
        self.ticket_channel_id = ticket_channel_id
        self.buyer_id = buyer_id

    @discord.ui.button(label="استلام", style=discord.ButtonStyle.success, custom_id="ticket_receive")
    async def receive(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin_member(interaction.user):
            await interaction.response.send_message("هذه الخاصية خاصة بالإداريين فقط.", ephemeral=True)
            return
        await interaction.channel.send(f"✅ تم استلام الطلب بواسطة {interaction.user.mention}.")
        await interaction.response.defer()

    @discord.ui.button(label="ترك", style=discord.ButtonStyle.secondary, custom_id="ticket_release")
    async def release(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin_member(interaction.user):
            await interaction.response.send_message("هذه الخاصية خاصة بالإداريين فقط.", ephemeral=True)
            return
        await interaction.channel.send(f"⚠️ {interaction.user.mention} تخلّى عن الاستلام. الطلب متاح لأدمن آخر.")
        await interaction.response.defer()

    @discord.ui.button(label="قفل المعاملة", style=discord.ButtonStyle.danger, custom_id="ticket_close")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.buyer_id:
            await interaction.response.send_message("فقط المشتري الذي فتح التذكرة يقدر يغلقها.", ephemeral=True)
            return
        await interaction.response.send_message("جاري إغلاق التذكرة وحذف القناة بعد 5 ثواني...", ephemeral=True)
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason=f"تذكرة مغلقة بواسطة المشتري {interaction.user}")
        except Exception as e:
            print("Error deleting ticket channel:", e)

class AdminPanelView(discord.ui.View):
    def __init__(self, admin_channel_id: int):
        super().__init__(timeout=None)
        self.admin_channel_id = admin_channel_id

    @discord.ui.button(label="➕ إضافة منتج", style=discord.ButtonStyle.primary, custom_id="admin_add_product")
    async def add_product(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin_member(interaction.user):
            await interaction.response.send_message("هذه الخاصية خاصة بالمديرين فقط.", ephemeral=True)
            return
        modal = AddProductModal(invoker=interaction.user, admin_channel_id=self.admin_channel_id)
        await interaction.response.send_modal(modal)

# ---------- أوامر سلاش ----------
@bot.tree.command(name="setupshop", description="نشر لوحة إدارة المتجر (خاص بالادارة)")
@app_commands.describe(channel="القناة التي تنعرض فيها لوحة الإدارة (مثلاً: قناة الأدمن)")
async def setupshop(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not is_admin_member(interaction.user):
        await interaction.response.send_message("هذه الأوامر خاصة بالمديرين فقط.", ephemeral=True)
        return
    target = channel or interaction.channel
    view = AdminPanelView(admin_channel_id=target.id)
    embed = discord.Embed(title="لوحة إدارة المتجر", description="اضغط زر ➕ لإضافة منتج جديد.\nبمجرد إضافة المنتج سيظهر في قناة المتجر المحددة.")
    await target.send(embed=embed, view=view)
    await interaction.response.send_message(f"تم نشر لوحة الإدارة في {target.mention}", ephemeral=True)

@bot.tree.command(name="setstore", description="تعيين قناة المتجر حيث تُعرض المنتجات (خاص بالادارة)")
@app_commands.describe(channel="قناة المتجر")
async def setstore(interaction: discord.Interaction, channel: discord.TextChannel):
    if not is_admin_member(interaction.user):
        await interaction.response.send_message("خاص بالادارة فقط.", ephemeral=True)
        return
    global CFG
    CFG["store_channel_id"] = channel.id
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(CFG, f, ensure_ascii=False, indent=2)
    await interaction.response.send_message(f"تم تعيين قناة المتجر إلى {channel.mention}", ephemeral=True)

@bot.tree.command(name="setticketcat", description="تعيين فئة التذاكر (Ticket Category) (خاص بالادارة)")
@app_commands.describe(category="فئة القنوات للتذاكر")
async def setticketcat(interaction: discord.Interaction, category: discord.CategoryChannel):
    if not is_admin_member(interaction.user):
        await interaction.response.send_message("خاص بالادارة فقط.", ephemeral=True)
        return
    global CFG
    CFG["ticket_category_id"] = category.id
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(CFG, f, ensure_ascii=False, indent=2)
    await interaction.response.send_message(f"تم تعيين فئة التذاكر إلى {category.name}", ephemeral=True)

# ---------- جاهزية البوت ----------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} app commands.")
    except Exception as e:
        print("Failed to sync commands:", e)

# -----------------------------
# 🔸 كود إبقاء البوت شغال 24/7 🔸
# -----------------------------
app_flask = Flask('')

@app_flask.route('/')
def home():
    return "البوت شغال ✅"

def run():
    app_flask.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# ---------- تشغيل البوت ----------
if __name__ == "__main__":
    token = os.getenv("DISCORD_BOT_TOKEN") or os.getenv("TOKEN") or ""
    if not token:
        print("ضع توكن البوت كمتغير بيئي DISCORD_BOT_TOKEN أو TOKEN")
        exit()

    keep_alive()  # الاستضافة 24/7
    bot.run(token)
