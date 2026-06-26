"""
calls_command.py
----------------------------------------
Discord bot slash command: /calls <date>

Results are ephemeral — only visible to the user who ran the command.

Setup:
  pip install discord.py psycopg2-binary
  Set env vars: DB_CONNECTION, DISCORD_TOKEN

After deploying, run once with SYNC_COMMANDS=true to register slash commands:
  SYNC_COMMANDS=true python calls_command.py
Then redeploy normally without it.
"""

import os
import re
import discord
from discord import app_commands
import psycopg2
from datetime import date, datetime, timedelta

DB_CONNECTION = os.environ["DB_CONNECTION"]
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
SYNC_COMMANDS = os.environ.get("SYNC_COMMANDS", "false").lower() == "true"

DISCORD_MSG_LIMIT = 1900

intents = discord.Intents.default()
client  = discord.Client(intents=intents)
tree    = app_commands.CommandTree(client)


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

    is_matured  = (date.today() - flag_date).days >= 30
    total       = len(rows)
    spiked      = sum(1 for r in rows if r["status"] == "spiked")
    missed      = sum(1 for r in rows if r["status"] == "missed")
    watching    = sum(1 for r in rows if r["status"] == "watching")
    hit_rate    = f"{round(spiked / total * 100)}%" if total else "—"
    mature_date = (flag_date + timedelta(days=30)).strftime("%b %d")

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

        if len(current) + len(line) > DISCORD_MSG_LIMIT:
            messages.append(current)
            current = (
                f"📋 **GC Calls — {flag_date.strftime('%b %d, %Y')} (cont.)**\n"
                f"`{'NAME':<22} {'GR':<7} {'FLAG':>7} {'30D':>7} {'NOW':>7} {'30D%':>6} {'NOW%':>6}  ST  CATALYST`\n"
            )

        current += line

    messages.append(current)
    return messages


@tree.command(name="calls", description="Show GC watchlist calls for a specific date")
@app_commands.describe(date="Date to look up in YYYY-MM-DD format (e.g. 2026-04-15)")
async def calls(interaction: discord.Interaction, date: str):
    # Validate date format
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        await interaction.response.send_message(
            "❌ Date must be `YYYY-MM-DD` — e.g. `/calls 2026-04-15`",
            ephemeral=True
        )
        return

    try:
        flag_date = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        await interaction.response.send_message("❌ Invalid date.", ephemeral=True)
        return

    from datetime import date as date_type
    if flag_date > date_type.today():
        await interaction.response.send_message("❌ Can't look up a future date.", ephemeral=True)
        return

    # Defer ephemerally — gives us time to query the DB
    await interaction.response.defer(ephemeral=True)

    try:
        rows = fetch_calls(flag_date)
    except Exception as e:
        await interaction.followup.send(f"❌ Database error: `{e}`", ephemeral=True)
        return

    messages = build_messages(flag_date, rows)

    # Send first message as followup, rest as additional followups
    for i, msg in enumerate(messages):
        await interaction.followup.send(msg, ephemeral=True)


@client.event
async def on_ready():
    if SYNC_COMMANDS:
        print("Syncing slash commands globally...")
        await tree.sync()
        print("✅ Slash commands synced — restart without SYNC_COMMANDS=true")
    else:
        print(f"GC Bot ready — logged in as {client.user}")


client.run(DISCORD_TOKEN)
