"""
calls_command.py
----------------------------------------
Discord bot slash commands:
  /calls <date>           — calls for a specific date (YYYY-MM-DD)
  /calls today            — today's flags (still watching)

Only shows A+B-tier calls (split_part(pattern_match, ' - ', 3) = 'A+B') —
these are the only calls we're putting our name on publicly.

Optional filter:
  sport   — e.g. NBA, MLB, NFL, Pokemon, One Piece

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


def fetch_calls(flag_date: date, sport_filter: Optional[str]):
    conn = get_connection()
    cur  = conn.cursor()

    sport_clause_sports = "AND LOWER(cw.sport) = LOWER(%s)" if sport_filter else ""
    sport_clause_tcg    = "AND LOWER(cw.title) = LOWER(%s)" if sport_filter else ""

    # A+B filter is hard-coded, not optional — these are the only calls
    # we publicly stand behind. flag_price_3d / res_price_3d / pct_change_res_3d
    # replace the old (now-dropped) price_at_30d / pct_change_30d columns.
    # call_horizon_days and data_sufficiency come along so maturity is judged
    # per-row instead of assuming a flat 30 days for every call.
    query = f"""
        SELECT
            c.canonical_name,
            cw.card_number,
            cw.grade,
            cw.current_price      AS flag_price,
            cw.flag_price_3d,
            cw.res_price_3d,
            cw.pct_change_res_3d,
            cw.data_sufficiency,
            cw.call_horizon_days,
            cw.flagged_date,
            cw.giga_score,
            cw.status,
            COALESCE(m.avg_price_3d, m.avg_price_7d, m.avg_price_30d, m.current_price) AS live_price
        FROM sports.candidate_watchlist cw
        JOIN cards c ON c.id = cw.card_id
        LEFT JOIN sports.mv_card_metrics m
               ON m.card_id = cw.card_id AND m.grade = cw.grade
        WHERE cw.flagged_date = %s
          AND split_part(cw.pattern_match, ' - ', 3) = 'A+B'
        {sport_clause_sports}

        UNION ALL

        SELECT
            c.canonical_name,
            cw.card_number,
            cw.grade,
            cw.current_price,
            cw.flag_price_3d,
            cw.res_price_3d,
            cw.pct_change_res_3d,
            cw.data_sufficiency,
            cw.call_horizon_days,
            cw.flagged_date,
            cw.giga_score,
            cw.status,
            COALESCE(m.avg_price_3d, m.avg_price_7d, m.avg_price_30d, m.current_price)
        FROM tcg.candidate_watchlist cw
        JOIN cards c ON c.id = cw.card_id
        LEFT JOIN sports.mv_card_metrics m
               ON m.card_id = cw.card_id AND m.grade = cw.grade
        WHERE cw.flagged_date = %s
          AND split_part(cw.pattern_match, ' - ', 3) = 'A+B'
        {sport_clause_tcg}

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


def row_maturity(r, today: date):
    """
    Per-row maturity, using this call's own call_horizon_days instead of a
    flat 30 days — long-horizon calls (offseason sports flips, TCG
    long_hold/set_swing) can run 60-365 days, and treating them as due at
    30 days would falsely call them matured, same bug we fixed at the DB
    level for res_price_3d/data_sufficiency.
    Returns (is_due, mature_date).
    """
    horizon = r.get("call_horizon_days") or 30
    mature_date = r["flagged_date"] + timedelta(days=horizon)
    return today >= mature_date, mature_date


def build_messages(flag_date: date, rows: list, is_today: bool,
                   sport_filter: Optional[str]) -> list[str]:

    filter_str = f"  •  {sport_filter}" if sport_filter else ""
    today = date.today()

    if not rows:
        label = "Today's Flags" if is_today else flag_date.strftime('%b %d, %Y')
        return [f"📋 **GC Calls (A+B) — {label}**{filter_str}\nNo calls found."]

    total       = len(rows)
    spiked      = sum(1 for r in rows if r["status"] == "spiked")
    missed      = sum(1 for r in rows if r["status"] == "missed")
    watching    = sum(1 for r in rows if r["status"] == "watching")
    resolved    = spiked + missed
    hit_rate    = f"{round(spiked / resolved * 100)}%" if resolved else "—"

    if is_today:
        maturity_str = f"{watching} still watching"
        date_label    = f"Today's Flags — {flag_date.strftime('%b %d, %Y')}"
    elif resolved == total:
        maturity_str = f"✅ All matured  •  Hit rate: **{hit_rate}**"
        date_label    = flag_date.strftime('%b %d, %Y')
    elif resolved > 0:
        maturity_str = f"⏳ {resolved}/{total} matured so far  •  Hit rate on matured: **{hit_rate}**"
        date_label    = flag_date.strftime('%b %d, %Y')
    else:
        maturity_str = "⏳ None matured yet — horizons vary by call, see below"
        date_label    = flag_date.strftime('%b %d, %Y')

    header = (
        f"📋 **GC Calls (A+B) — {date_label}**{filter_str}\n"
        f"🟢 {spiked} hit  🔴 {missed} missed  🟡 {watching} watching  •  {total} total\n"
        f"{maturity_str}\n"
        f"{'─' * 40}\n"
    )

    messages = []
    current  = header

    for r in rows:
        flag_price = r["flag_price"]
        resolved_price = r["res_price_3d"] if r["data_sufficiency"] == "ok" else None
        live       = r["live_price"]
        is_due, mature_date = row_maturity(r, today)

        canonical = str(r["canonical_name"] or "Unknown")
        card_num  = f"#{r['card_number']}" if r["card_number"] else ""
        grade     = str(r["grade"] or "")

        id_line = f"`{canonical[:58]} {card_num}  {grade}`\n"

        if is_today or r["status"] == "watching":
            due_str = "matures today" if is_due else f"matures {mature_date.strftime('%b %d')}"
            price_line = (
                f"Flag: **{fmt_price(flag_price)}**  •  "
                f"Now: **{fmt_price(live)}** ({fmt_pct_from(flag_price, live)})  "
                f"{status_icon(r['status'])}  •  ⏳ {due_str}\n\n"
            )
        elif resolved_price is not None:
            price_line = (
                f"Flag: **{fmt_price(flag_price)}**  →  "
                f"Resolved: **{fmt_price(resolved_price)}** ({fmt_pct(r['pct_change_res_3d'])})  →  "
                f"Now: **{fmt_price(live)}** ({fmt_pct_from(flag_price, live)})  "
                f"{status_icon(r['status'])}\n\n"
            )
        else:
            price_line = (
                f"Flag: **{fmt_price(flag_price)}**  •  "
                f"Now: **{fmt_price(live)}** ({fmt_pct_from(flag_price, live)})  "
                f"{status_icon(r['status'])}  •  ⚠️ insufficient sales data to confirm outcome\n\n"
            )

        block = id_line + price_line

        if len(current) + len(block) > DISCORD_MSG_LIMIT:
            messages.append(current)
            current = f"📋 **GC Calls (A+B) — {date_label} (cont.)**{filter_str}\n"

        current += block

    messages.append(current)
    return messages


@tree.command(name="calls", description="Show GC A+B watchlist calls for a date or today's flags")
@app_commands.describe(
    date="Date in YYYY-MM-DD format, or 'today' for today's flags",
    sport="Filter by sport: NBA, MLB, NFL, Pokemon, One Piece, etc.",
)
async def calls(
    interaction: discord.Interaction,
    date: str,
    sport: Optional[str] = None,
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
        if flag_date < datetime(2026, 6, 8).date():
            await interaction.response.send_message(
                "❌ Calls are only available from June 8, 2026 onward.", ephemeral=True
            )
            return

        if flag_date > date_type.today():
            await interaction.response.send_message(
                "❌ Can't look up a future date.", ephemeral=True
            )
            return

    await interaction.response.defer(ephemeral=True)

    try:
        rows = fetch_calls(flag_date, sport)
    except Exception as e:
        await interaction.followup.send(f"❌ Database error: `{e}`", ephemeral=True)
        return

    messages = build_messages(flag_date, rows, is_today, sport)
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
