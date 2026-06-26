"""
calls_command.py
----------------------------------------
Discord bot slash commands:
  /calls <date>           — calls for a specific date (YYYY-MM-DD)
  /calls today            — today's flags (still watching)

Optional filters:
  sport   — e.g. NBA, MLB, NFL, Pokemon, One Piece
  status  — hits, misses, watching

Results are ephemeral — only visible to the user who ran the command.

Setup:
  pip install discord.py psycopg2-binary
  Set env vars: DB_CONNECTION, DISCORD_TOKEN

One-time slash command sync:
  Set SYNC_COMMANDS=true in Railway env vars, deploy, wait for
  "Slash commands synced" in logs, then remove and redeploy.
"""

import os
import re
import discord
from discord import app_commands
from typing import Optional
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


def fetch_calls(flag_date: date, sport_filter: Optional[str], status_filter: Optional[str]):
    conn = get_connection()
    cur  = conn.cursor()

    sport_clause  = "AND LOWER(cw.sport) = LOWER(%s)" if sport_filter else ""
    status_clause = ""
    if status_filter == "hits":
        status_clause = "AND cw.status = 'spiked'"
    elif status_filter == "misses":
        status_clause = "AND cw.status = 'missed'"
    elif status_filter == "watching":
        status_clause = "AND cw.status = 'watching'"

    query = f"""
        SELECT
            c.canonical_name,
            cw.card_number,
            cw.grade,
            cw.sport,
            cw.current_price      AS flag_price,
            cw.price_at_30d,
            cw.pct_change_30d,
            cw.predicted_catalyst,
            cw.giga_score,
            cw.status,
            m.current_price       AS live_price
        FROM sports.candidate_watchlist cw
        JOIN cards c ON c.id = cw.card_id
        LEFT JOIN sports.mv_card_metrics m
               ON m.card_id = cw.card_id AND m.grade = cw.grade
        WHERE cw.flagged_date = %s
        {sport_clause}
        {status_clause}

        UNION ALL

        SELECT
            c.canonical_name,
            cw.card_number,
            cw.grade,
            cw.title              AS sport,
            cw.current_price,
            cw.price_at_30d,
            cw.pct_change_30d,
            cw.predicted_catalyst,
            cw.giga_score,
            cw.status,
            m.current_price
        FROM tcg.candidate_watchlist cw
        JOIN cards c ON c.id = cw.card_id
        LEFT JOIN sports.mv_card_metrics m
               ON m.card_id = cw.card_id AND m.grade = cw.grade
        WHERE cw.flagged_date = %s
        {'AND LOWER(cw.title) = LOWER(%s)' if sport_filter else ''}
        {status_clause}

        ORDER BY giga_score DESC;
    """

    params = [flag_date]
    if sport_filter:
        params.append(sport_filter)
    params.append(flag_date)
    if sport_filter:
        params.append(sport_filter)

    cur.execute(query, params)
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


def build_messages(flag_date: date, rows: list, is_today: bool,
                   sport_filter: Optional[str], status_filter: Optional[str]) -> list[str]:

    filter_str = ""
    if sport_filter:
        filter_str += f"  •  {sport_filter}"
    if status_filter:
        filter_str += f"  •  {status_filter} only"

    if not rows:
        label = "Today's Flags" if is_today else flag_date.strftime('%b %d, %Y')
        return [f"📋 **GC Calls — {label}**{filter_str}\nNo calls found."]

    is_matured  = (date.today() - flag_date).days >= 30
    total       = len(rows)
    spiked      = sum(1 for r in rows if r["status"] == "spiked")
    missed      = sum(1 for r in rows if r["status"] == "missed")
    watching    = sum(1 for r in rows if r["status"] == "watching")
    hit_rate    = f"{round(spiked / total * 100)}%" if total else "—"
    mature_date = (flag_date + timedelta(days=30)).strftime("%b %d")

    if is_today:
        maturity_str = f"⏳ Matures {mature_date}  •  {watching} watching"
        date_label   = f"Today's Flags — {flag_date.strftime('%b %d, %Y')}"
    elif is_matured:
        maturity_str = f"✅ Matured  •  Hit rate: **{hit_rate}**"
        date_label   = flag_date.strftime('%b %d, %Y')
    else:
        maturity_str = f"⏳ Matures {mature_date}  •  30d prices pending"
        date_label   = flag_date.strftime('%b %d, %Y')

    header = (
        f"📋 **GC Calls — {date_label}**{filter_str}\n"
        f"🟢 {spiked} hit  🔴 {missed} missed  🟡 {watching} watching  •  {total} total\n"
        f"{maturity_str}\n"
        f"{'─' * 40}\n"
    )

    messages = []
    current  = header

    for r in rows:
        flag_price = r["flag_price"]
        p30        = r["price_at_30d"]
        live       = r["live_price"]

        canonical  = str(r["canonical_name"] or r.get("player_name", "Unknown"))
        card_num   = f"#{r['card_number']}" if r["card_number"] else ""
        grade      = str(r["grade"] or "")
        score      = r["giga_score"]

        # Line 1: card identity
        id_line = f"`{canonical[:55]} {card_num}  {grade} `\n"

        # Line 2: prices
        if is_today:
            price_line = (
                f"Flag: **{fmt_price(flag_price)}**  •  "
                f"Now: **{fmt_price(live)}** ({fmt_pct_from(flag_price, live)})  "
                f"{status_icon(r['status'])}\n\n"
            )
        else:
            price_line = (
                f"Flag: **{fmt_price(flag_price)}**  →  "
                f"30d: **{fmt_price(p30)}** ({fmt_pct(r['pct_change_30d'])})  →  "
                f"Now: **{fmt_price(live)}** ({fmt_pct_from(flag_price, live)})  "
                f"{status_icon(r['status'])}\n\n"
            )

        block = id_line + price_line

        if len(current) + len(block) > DISCORD_MSG_LIMIT:
            messages.append(current)
            current = (
                f"📋 **GC Calls — {date_label} (cont.)**{filter_str}\n"
            )

        current += block

    messages.append(current)
    return messages


@tree.command(name="calls", description="Show GC watchlist calls for a date or today's flags")
@app_commands.describe(
    date="Date in YYYY-MM-DD format, or 'today' for today's flags",
    sport="Filter by sport: NBA, MLB, NFL, Pokemon, One Piece, etc.",
    status="Filter by outcome: hits, misses, watching"
)
@app_commands.choices(status=[
    app_commands.Choice(name="Hits only",    value="hits"),
    app_commands.Choice(name="Misses only",  value="misses"),
    app_commands.Choice(name="Watching",     value="watching"),
])
async def calls(
    interaction: discord.Interaction,
    date: str,
    sport: Optional[str] = None,
    status: Optional[app_commands.Choice[str]] = None,
):
    is_today = date.strip().lower() == "today"

    if is_today:
        flag_date = datetime.utcnow().date()
    else:
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            await interaction.response.send_message(
                "❌ Date must be `YYYY-MM-DD` or `today` — e.g. `/calls 2026-04-15`",
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
            await interaction.response.send_message(
                "❌ Can't look up a future date.", ephemeral=True
            )
            return

    await interaction.response.defer(ephemeral=True)

    status_val = status.value if status else None

    try:
        rows = fetch_calls(flag_date, sport, status_val)
    except Exception as e:
        await interaction.followup.send(f"❌ Database error: `{e}`", ephemeral=True)
        return

    messages = build_messages(flag_date, rows, is_today, sport, status_val)
    for msg in messages:
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
