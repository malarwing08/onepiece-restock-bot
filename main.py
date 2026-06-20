import os, json, asyncio, threading
from datetime import datetime
from flask import Flask
import discord
from discord.ext import commands, tasks
from playwright.async_api import async_playwright

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
CHECK_EVERY_MINUTES = int(os.getenv("CHECK_EVERY_MINUTES", "10"))

SEEN_FILE = "seen_alerts.json"

SEARCHES = {
    "Walmart Springfield MO": [
        "https://www.walmart.com/search?q=pokemon%20cards",
        "https://www.walmart.com/search?q=one%20piece%20cards",
        "https://www.walmart.com/search?q=one%20piece%20tcg",
    ],
    "Target Springfield MO": [
        "https://www.target.com/s?searchTerm=pokemon+cards",
        "https://www.target.com/s?searchTerm=one+piece+cards",
        "https://www.target.com/s?searchTerm=one+piece+tcg",
    ],
    "Barnes & Noble Springfield MO": [
        "https://www.barnesandnoble.com/s/pokemon%20cards",
        "https://www.barnesandnoble.com/s/one%20piece%20cards",
        "https://www.barnesandnoble.com/s/one%20piece%20tcg",
    ],
    "Premium Bandai Online": [
        "https://p-bandai.com/us/brand/onepiececardgame",
        "https://p-bandai.com/us/shop/bandaicardshop/",
    ],
}

KEYWORDS = [
    "pokemon", "pokémon", "one piece", "op-", "op01", "op02", "op03", "op04",
    "op05", "op06", "op07", "op08", "op09", "op10", "op11", "op12", "op13",
    "op14", "op15", "booster", "elite trainer", "etb", "starter deck",
    "double pack", "trading card", "premium card collection"
]

BAD_WORDS = [
    "out of stock", "sold out", "unavailable", "currently unavailable",
    "pre-order closed", "notify me when available"
]

GOOD_WORDS = [
    "in stock", "pickup", "available", "add to cart", "buy online",
    "ship it", "shipping", "free pickup", "ready", "pre-order", "preorder"
]

app = Flask(__name__)

@app.route("/")
def home():
    return "Restock bot running."

def run_web():
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)

def load_seen():
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r") as f:
        return set(json.load(f))

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)

def relevant(text):
    text = text.lower()
    return any(k in text for k in KEYWORDS)

def bad_stock(text):
    text = text.lower()
    return any(w in text for w in BAD_WORDS)

def good_stock(text):
    text = text.lower()
    return any(w in text for w in GOOD_WORDS)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

async def scan_page(page, store, url):
    results = []

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(7000)

        body = await page.locator("body").inner_text()
        body_low = body.lower()

        if "premium bandai" not in store.lower():
            local_check = [
                "springfield", "65804", "65807", "65802", "65803",
                "65806", "pickup", "in store", "available nearby"
            ]
            if not any(x in body_low for x in local_check):
                return []

        if bad_stock(body_low):
            return []

        if not good_stock(body_low):
            return []

        links = await page.locator("a").evaluate_all("""
            els => els.slice(0, 500).map(a => ({
                text: a.innerText || "",
                href: a.href || ""
            }))
        """)

        for item in links:
            name = " ".join(item["text"].split())
            link = item["href"]

            if not name or not link or len(name) < 8:
                continue

            if not relevant(name):
                continue

            if "premium bandai" in store.lower():
                status = "Online order/preorder may be open"
                location = "Online — Premium Bandai USA"
            else:
                status = "Possible Springfield, MO in-store pickup/restock"
                location = "Springfield, Missouri area"

            results.append({
                "store": store,
                "name": name[:250],
                "link": link,
                "status": status,
                "location": location
            })

    except Exception as e:
        print(f"Error scanning {store}: {e}")

    return results

async def scan_all():
    all_items = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )

        context = await browser.new_context(
            user_agent="Mozilla/5.0 AppleWebKit/537.36 Chrome Safari"
        )

        page = await context.new_page()

        for store, urls in SEARCHES.items():
            for url in urls:
                items = await scan_page(page, store, url)
                all_items.extend(items)

        await browser.close()

    return all_items

async def send_alert(channel, item):
    embed = discord.Embed(
        title=f"🚨 RESTOCK ALERT: {item['name']}",
        description="@everyone",
        color=0x00ff66,
        timestamp=datetime.utcnow()
    )

    embed.add_field(name="Store", value=item["store"], inline=False)
    embed.add_field(name="Status", value=item["status"], inline=False)
    embed.add_field(name="Location", value=item["location"], inline=False)
    embed.add_field(name="Link", value=f"[Open page]({item['link']})", inline=False)

    await channel.send(content="@everyone", embed=embed)

@tasks.loop(minutes=CHECK_EVERY_MINUTES)
async def restock_checker():
    channel = bot.get_channel(CHANNEL_ID)

    if not channel:
        print("Channel not found. Check CHANNEL_ID.")
        return

    seen = load_seen()
    items = await scan_all()

    for item in items:
        key = f"{item['store']}|{item['name']}|{item['link']}"

        if key in seen:
            continue

        seen.add(key)
        await send_alert(channel, item)
        await asyncio.sleep(2)

    save_seen(seen)

@bot.command()
async def scan(ctx):
    await ctx.send("Scanning now...")
    items = await scan_all()

    if not items:
        await ctx.send("No Springfield/Premium Bandai restocks found right now.")
        return

    for item in items[:10]:
        await send_alert(ctx.channel, item)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

    if not restock_checker.is_running():
        restock_checker.start()

threading.Thread(target=run_web).start()
bot.run(DISCORD_TOKEN)
