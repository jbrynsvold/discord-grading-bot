"""
calls_command.py
----------------------------------------
Discord bot command: !calls <YYYY-MM-DD>

Shows all GC watchlist flags from a given date as compact
text rows — no per-card embed fields, so more fits per message.

Usage:
  !calls 2026-04-15

Setup:
  pip install discord.py psycopg2-binary
  Set env vars: DB_CONNECTION, DISCORD_TOKEN
"""

import os
import re
import discord
import psycopg2
from datetime import date, datetime, timedelta

DB_CONNECTION = os.environ["DB_CONNECTION"]
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

DISCORD_MSG_LIMIT = 1900  # safe limit under 2000


def get_connection():
    conn = psycopg2.connect(DB_CONNECTION)
    conn.autocommit = True
    return conn


def fetch_calls(flag_date: date):
    conn = get_connection()
    cur  = conn.cursor()
    cur.execute("""
        SELECT
            cw.player_name,
            cw.set_name,
            cw.grade,
            cw.current_price      AS flag_price,
            cw.price_at_30d,
            cw.pct_change_30d,
            cw.predicted_catalyst,
            cw.giga_score,
            cw.status,
            m.current_price       AS live_price
        FROM sports.candidate_watchlist cw
        LEFT JOIN sports.mv_card_metrics m
               ON m.card_id = cw.card_id AND m.grade = cw.grade
        WHERE cw.flagged_date = %s

        UNION ALL

        SELECT
            cw.character_name,
            cw.set_name,
            cw.grade,
            cw.current_price,
            cw.price_at_30d,
            cw.pct_change_30d,
            cw.predicted_catalyst,
            cw.giga_score,
            cw.status,
            m.current_price
        FROM tcg.candidate_watchlist cw
        LEFT JOIN sports.mv_card_metrics m
               ON m.card_id = cw.card_id AND m.grade = cw.grade
        WHERE cw.flagged_date = %s

        ORDER BY giga_score DESC;
    """, (flag_date, flag_date))
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    cur.close()
    conn.close()
    return [dict(zip(cols, r)) for r in rows]


def fmt_price(val):
    if val is None:
        return "—"
    return f"${float(val):.2f}"


def fmt_pct(val):
    if val is None:
        return "—"
    v = float(val)
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.1f}%"


def fmt_pct_from(a, b):
    """% change from a to b."""
    if a is None or b is None or float(a) == 0:
        return "—"
    pct = (float(b) - float(a)) / float(a) * 100
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


def status_icon(status):
    return {"spiked": "🟢", "missed": "🔴", "watching": "🟡"}.get(status or "", "⚪")


def build_messages(flag_date: date, rows: list) -> list[str]:
    if not rows:
        return [f"📋 **GC Calls — {flag_date.strftime('%b %d, %Y')}**\nNo calls found for this date."]

    is_matured   = (date.today() - flag_date).days >= 30
    total        = len(rows)
    spiked       = sum(1 for r in rows if r["status"] == "spiked")
    missed       = sum(1 for r in rows if r["status"] == "missed")
    watching     = sum(1 for r in rows if r["status"] == "watching")
    hit_rate     = f"{round(spiked / total * 100)}%" if total else "—"
    mature_date  = (flag_date + timedelta(days=30)).strftime("%b %d")

    # ── Header block ──────────────────────────────────────────────────────────
    if is_matured:
        maturity_str = f"✅ Matured  •  Hit rate: **{hit_rate}**"
    else:
        maturity_str = f"⏳ Matures {mature_date}  •  30d prices pending"

    header = (
        f"📋 **GC Calls — {flag_date.strftime('%b %d, %Y')}**\n"
        f"🟢 {spiked} hit  🔴 {missed} missed  🟡 {watching} watching  •  {total} total\n"
        f"{maturity_str}\n"
        f"{'─' * 40}\n"
        f"`{'NAME':<22} {'GR':<7} {'FLAG':>7} {'30D':>7} {'NOW':>7} {'30D%':>6} {'NOW%':>6}  ST  CATALYST`\n"
    )

    # ── Card rows ─────────────────────────────────────────────────────────────
    messages = []
    current  = header

    for r in rows:
        flag_price = r["flag_price"]
        p30        = r["price_at_30d"]
        live       = r["live_price"]
        catalyst   = (r["predicted_catalyst"] or "speculation").replace("_", " ")

        name  = str(r["player_name"] or "")[:22]
        grade = str(r["grade"] or "")[:7]

        line = (
            f"`{name:<22} {grade:<7} "
            f"{fmt_price(flag_price):>7} "
            f"{fmt_price(p30):>7} "
            f"{fmt_price(live):>7} "
            f"{fmt_pct(r['pct_change_30d']):>6} "
            f"{fmt_pct_from(flag_price, live):>6}`  "
            f"{status_icon(r['status'])}  {catalyst}\n"
        )

        # If adding this line would exceed the limit, flush and start new message
        if len(current) + len(line) > DISCORD_MSG_LIMIT:
            messages.append(current)
            current = f"📋 **GC Calls — {flag_date.strftime('%b %d, %Y')} (cont.)**\n"
            current += f"`{'NAME':<22} {'GR':<7} {'FLAG':>7} {'30D':>7} {'NOW':>7} {'30D%':>6} {'NOW%':>6}  ST  CATALYST`\n"

        current += line

    messages.append(current)
    return messages


@client.event
async def on_ready():
    print(f"GC Bot ready — logged in as {client.user}")


@client.event
async def on_message(message):
    if message.author.bot:
        return

    content = message.content.strip()
    if not content.lower().startswith("!calls"):
        return

    parts = content.split()
    if len(parts) < 2:
        await message.channel.send("❌ Usage: `!calls YYYY-MM-DD`  e.g. `!calls 2026-04-15`")
        return

    date_str = parts[1]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        await message.channel.send("❌ Date must be `YYYY-MM-DD`")
        return

    try:
        flag_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        await message.channel.send("❌ Invalid date.")
        return

    if flag_date > date.today():
        await message.channel.send("❌ Can't look up a future date.")
        return

    async with message.channel.typing():
        try:
            rows = fetch_calls(flag_date)
        except Exception as e:
            await message.channel.send(f"❌ Database error: `{e}`")
            return

        for msg in build_messages(flag_date, rows):
            await message.channel.send(msg)


client.run(DISCORD_TOKEN)
