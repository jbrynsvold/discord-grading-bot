import os
import discord
from discord import app_commands
from supabase import create_client, Client
from dotenv import load_dotenv
from datetime import date

load_dotenv()
TOKEN        = os.getenv("EVALUATE_BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ===========================================================================
# HELPERS
# ===========================================================================

def fv(val):
    return float(val) if val is not None else None

def fmt(amount):
    if amount is None:
        return "N/A"
    return f"${float(amount):,.2f}"

# ===========================================================================
# GIGASCORE
# ===========================================================================

def compute_giga_score(m: dict) -> tuple[int, dict]:
    pct_range  = fv(m.get("pct_of_52w_range"))
    sale_30d   = fv(m.get("sale_count_30d")) or 0
    sale_90d   = fv(m.get("sale_count_90d")) or 0
    sale_3d    = fv(m.get("sale_count_3d"))  or 0
    avg_3d     = fv(m.get("avg_price_3d"))
    avg_90d    = fv(m.get("avg_price_90d"))
    avg_30_90d = fv(m.get("avg_price_30_90d"))
    has_90d    = bool(m.get("has_90d_data"))
    coalesce_3d = avg_3d if avg_3d is not None else fv(m.get("current_price"))

    if pct_range is None:   c1 = 0
    elif pct_range <= 10:   c1 = 30
    elif pct_range <= 20:   c1 = 25
    elif pct_range <= 30:   c1 = 20
    elif pct_range <= 45:   c1 = 12
    elif pct_range <= 60:   c1 = 5
    else:                   c1 = 0

    liq = sale_90d if has_90d else sale_30d * 3
    if   liq >= 200: c2 = 25
    elif liq >= 100: c2 = 21
    elif liq >= 50:  c2 = 17
    elif liq >= 20:  c2 = 12
    elif liq >= 10:  c2 = 7
    elif liq >= 3:   c2 = 3
    else:            c2 = 0

    def vel_pts(r):
        if r is None: return 0
        if r >= 1.5:  return 25
        if r >= 1.2:  return 18
        if r >= 0.8:  return 10
        return 0

    base_90d = (sale_90d / 3.0) if has_90d and sale_90d else None
    r1 = (sale_30d / base_90d)       if base_90d            else None
    r2 = ((sale_3d * 10) / sale_30d) if sale_30d > 0        else None
    r3 = ((sale_3d * 30) / sale_90d) if has_90d and sale_90d else None
    c3 = max(vel_pts(r1), vel_pts(r2), vel_pts(r3))

    baseline = avg_30_90d or avg_90d
    if coalesce_3d and baseline and baseline > 0:
        ratio = coalesce_3d / baseline
        if   ratio <= 0.90: c4 = 20
        elif ratio <= 1.00: c4 = 15
        elif ratio <= 1.10: c4 = 10
        elif ratio <= 1.25: c4 = 5
        else:               c4 = 0
    else:
        c4 = 0

    total = min(100, c1 + c2 + c3 + c4)
    breakdown = {
        "range_pos": (c1, 30, f"{pct_range:.1f}% of 52w range" if pct_range is not None else "no data"),
        "liquidity": (c2, 25, f"{int(liq)} sales / 90d"),
        "velocity":  (c3, 25, f"best ratio: {max(r or 0 for r in [r1,r2,r3] if r is not None):.2f}x" if any(r is not None for r in [r1,r2,r3]) else "no data"),
        "momentum":  (c4, 20, f"{coalesce_3d:.2f} vs {baseline:.2f} baseline" if (coalesce_3d and baseline) else "no data"),
    }
    return total, breakdown


# ===========================================================================
# HARD RULES
# ===========================================================================

def check_hard_rules(m: dict) -> list[dict]:
    rules = []
    sale_30d   = fv(m.get("sale_count_30d")) or 0
    sale_90d   = fv(m.get("sale_count_90d")) or 0
    has_90d    = bool(m.get("has_90d_data"))
    days_since = fv(m.get("days_since_last_sale"))
    avg_3d     = fv(m.get("avg_price_3d"))
    avg_30d    = fv(m.get("avg_price_30d"))
    current    = fv(m.get("current_price"))

    base_90d = (sale_90d / 3.0) if has_90d and sale_90d else None
    if base_90d and base_90d > 0:
        vel = sale_30d / base_90d
        rules.append({"name": "HR2 velocity", "passed": vel >= 0.5,
                      "reason": f"trading at {vel:.1f}x seasonal pace" if vel >= 0.5 else f"only {vel:.1f}x seasonal pace — below minimum threshold"})
    else:
        rules.append({"name": "HR2 velocity", "passed": True, "reason": "insufficient 90d data — not blocked"})

    price_ref = avg_3d or current
    if price_ref and avg_30d and avg_30d > 0:
        trend_pct = ((price_ref - avg_30d) / avg_30d) * 100
        rules.append({"name": "HR3 trend", "passed": trend_pct > -15,
                      "reason": f"{trend_pct:+.1f}% vs 30d avg — not in drawdown" if trend_pct > -15 else f"{trend_pct:+.1f}% vs 30d avg — steep drawdown"})
    else:
        rules.append({"name": "HR3 trend", "passed": True, "reason": "insufficient data — not blocked"})

    if days_since is not None:
        rules.append({"name": "HR4 recency", "passed": days_since <= 14,
                      "reason": f"last sale {int(days_since)}d ago" if days_since <= 14 else f"last sale {int(days_since)}d ago — market gone quiet"})
    else:
        rules.append({"name": "HR4 recency", "passed": False, "reason": "no sale date on record"})

    return rules


# ===========================================================================
# VERDICT
# ===========================================================================

def build_fail_reason(failed, score, card, trend_pct=None):
    failed_names = {r["name"] for r in failed}
    days_since = int(float(card.get("days_since_last_sale") or 0))
    sale_30d   = int(card.get("sale_count_30d") or 0)
    pct_range  = float(card.get("pct_of_52w_range") or 0)

    if failed_names == {"HR3 trend"}:
        if sale_30d >= 20:
            return "Lots of selling pressure right now — volume is there but the price is heading down. Wait for the trend to reverse."
        return "The price is in a meaningful decline right now. We avoid flagging cards that are actively falling — the setup needs to stabilize first."

    if failed_names == {"HR2 velocity"}:
        if pct_range > 60:
            return "Volume has dropped off and the card is near its yearly high. Low conviction at an expensive price — not a good combination."
        if pct_range <= 20:
            return "The price is near its yearly low, which is attractive, but trading has slowed way down. Hard to call a setup without consistent buyer interest."
        return "Trading volume is below where we need it. The card hasn't done much lately — check back if activity picks up."

    if failed_names == {"HR4 recency"}:
        if days_since <= 30:
            return f"The card hasn't sold in {days_since} days. Could be a temporary lull — worth checking back in a week or two."
        if pct_range <= 20:
            return f"Price is near its yearly low but the card has gone completely quiet — {days_since} days without a sale. The setup could be interesting if it wakes up."
        return f"No sales in {days_since} days. The market has lost interest for now. Re-evaluate if it starts trading again."

    if failed_names == {"HR2 velocity", "HR4 recency"}:
        return f"This card has gone quiet — {sale_30d} sales in 30 days and the last one was {days_since} days ago. We need to see active trading before we can evaluate the setup."

    if failed_names == {"HR3 trend", "HR4 recency"}:
        return f"Price has been declining and the card hasn't sold in {days_since} days. Two red flags at once — not a setup we'd act on right now."

    if failed_names == {"HR2 velocity", "HR3 trend"}:
        tp = trend_pct or 0
        if tp < -20 and sale_30d < 5:
            return "Volume has collapsed and the price is in a real decline. This card is in a rough spot — wait for both to stabilize."
        return "Volume is thin and price is drifting down. Neither signal is extreme, but together they make this a pass for now."

    if len(failed) >= 3:
        return "This card isn't trading actively, volume is thin, and the price trend is negative. None of our baseline criteria are met right now."

    if not failed:
        tp = trend_pct or 0
        if pct_range <= 20 and tp > 5:
            return "Price is near its yearly low and starting to move — but volume isn't there yet to confirm the setup. Worth watching."
        if pct_range <= 20:
            return "The price is near its yearly low which is encouraging, but trading activity is too thin to call a setup. Could be worth watching if volume picks up."
        if pct_range <= 45:
            return "Nothing stands out here. The card is trading in the middle of its yearly range with no strong momentum in either direction."
        if pct_range <= 60:
            return f"The card has moved up from its low but hasn't broken out. At {pct_range:.0f}% of its range with a score of {score}/100, the setup isn't compelling right now."
        return f"The card is in the upper portion of its yearly range at {pct_range:.0f}%. Not an ideal entry — we'd rather buy this closer to its low."

    return "Doesn't meet our criteria right now."


def get_verdict(score, rules, already_spiked, pct_of_range, card=None, trend_pct=None):
    all_pass = all(r["passed"] for r in rules)
    failed   = [r for r in rules if not r["passed"]]
    card     = card or {}

    if already_spiked:
        return "Avoid", 0xED4245, "This card has already made its move. If you're holding — consider taking profits now. If you're looking to buy — the window has passed."
    if pct_of_range is not None and pct_of_range > 80:
        return "Sell", 0xED4245, f"At {pct_of_range:.0f}% of its yearly range, this card is near its yearly high. If you're holding — this is a reasonable exit point. If you're looking to buy — wait for a pullback before entering."
    if not all_pass:
        return "Skip", 0x888780, build_fail_reason(failed, score, card, trend_pct)
    if score >= 65:
        if pct_of_range is not None and pct_of_range <= 20 and (trend_pct or 0) > 5:
            return "Buy", 0x1D9E75, "Strong setup. Price near its yearly low, volume is healthy, and momentum is building. All criteria met."
        if pct_of_range is not None and (sale_30d := int(card.get("sale_count_30d") or 0)) >= 20 and pct_of_range <= 60:
            return "Buy", 0x1D9E75, f"High conviction setup — strong volume, all rules pass, and still room to run at {pct_of_range:.0f}% of its range."
        range_str = f"sitting at just {pct_of_range:.0f}% of its yearly range" if pct_of_range is not None else "near its yearly low"
        return "Buy", 0x1D9E75, f"Everything checks out. Active trading, healthy volume, and {range_str} — this is the kind of setup we look for."
    if score >= 55:
        if pct_of_range is not None and pct_of_range <= 20:
            return "Watch", 0xEF9F27, f"Passes all our checks and sitting near its yearly low — that's promising. Score is {score}/100, just short of a buy signal. Worth keeping on your radar."
        if (trend_pct or 0) > 5:
            return "Watch", 0xEF9F27, f"Momentum is building and all checks pass, but the score is {score}/100 — we look for 65+ to call a buy. Could get there if the trend continues."
        return "Watch", 0xEF9F27, f"Passes our activity checks with a score of {score}/100. Not quite a buy yet, but the setup is developing. Watch for a stronger signal."
    return "Skip", 0x888780, build_fail_reason(failed, score, card, trend_pct)


# ===========================================================================
# BOT SETUP
# ===========================================================================

intents = discord.Intents.default()
client  = discord.Client(intents=intents)
tree    = app_commands.CommandTree(client)

@client.event
async def on_ready():
    await tree.sync()
    print(f"[OK] EvaluateBot is online as {client.user}")


# ===========================================================================
# /evaluate
# ===========================================================================

@tree.command(name="evaluate", description="Should you buy, hold, or pass on this card? GIGA scores it against our full criteria.")
@app_commands.describe(
    player="Player or character name",
    set_name="Set name",
    card_number="Card number — required when multiple cards share the same variation (e.g. GG45, 201)",
    variation="Parallel or variation (e.g. Base, Full Art, Gold Prizm)",
    grade="Grade — default: Raw",
)
async def evaluate(
    interaction: discord.Interaction,
    player: str,
    set_name: str,
    variation: str,
    grade: str = "Raw",
    card_number: str = None,
):
    await interaction.response.defer(ephemeral=True)

    try:
        print(f"[DEBUG] evaluate_card: player={player!r} set={set_name!r} grade={grade!r} variation={variation!r} card_number={card_number!r}")
        result = supabase.rpc("evaluate_card", {
            "p_player":      player,
            "p_set":         set_name,
            "p_grade":       grade,
            "p_variation":   variation or "",
            "p_card_number": card_number or "",
        }).execute()
    except Exception as e:
        await interaction.followup.send(f"[ERROR] Database error: {e}")
        return

    if not result.data:
        await interaction.followup.send(
            f"No card found for **{player}** in **{set_name}** ({grade}).\n"
            "Try a partial name, different grade, or add a card number to narrow it down."
        )
        return

    # -----------------------------------------------------------------------
    # DISAMBIGUATION — if multiple cards match, require card_number
    # -----------------------------------------------------------------------
    if len(result.data) > 1:
        lines = []
        for c in result.data:
            num   = c.get("card_number", "?")
            var   = c.get("variation") or "Base"
            price = fv(c.get("current_price"))
            price_str = f" — {fmt(price)}" if price else ""
            canon = c.get("canonical_name", "")
            lines.append(f"• **#{num}** {var}{price_str}  _{canon}_")

        embed = discord.Embed(
            title="🔎 Multiple matches — add a card number",
            description=(
                f"Found **{len(result.data)} cards** matching **{player}** · {set_name} · {variation} · {grade}.\n"
                f"Re-run `/evaluate` and fill in the **card_number** field to get the right one:\n\n"
                + "\n".join(lines)
            ),
            color=0x5865F2,
        )
        embed.set_footer(text="Copy the # from the list above into the card_number field.")
        await interaction.followup.send(embed=embed)
        return

    # -----------------------------------------------------------------------
    # SINGLE MATCH — full evaluation
    # -----------------------------------------------------------------------
    print(f"[DEBUG] RPC returned {len(result.data)} rows")
    card = result.data[0]
    card_id    = card.get("card_id")
    card_grade = card.get("grade", grade)

    try:
        score, breakdown = compute_giga_score(card)
        rules = check_hard_rules(card)

        watchlist_entry = None
        try:
            r = supabase.rpc("get_watchlist_entry", {"p_card_id": card_id, "p_grade": card_grade}).execute()
            if r.data:
                watchlist_entry = r.data[0]
        except Exception:
            pass

        already_spiked = False
        spike_info = None
        try:
            r = supabase.rpc("get_spike_entry", {"p_card_id": card_id, "p_grade": card_grade}).execute()
            if r.data:
                s = r.data[0]
                spike_date = s.get("spike_start_date")
                if spike_date:
                    from datetime import datetime
                    days_ago = (date.today() - datetime.strptime(str(spike_date), "%Y-%m-%d").date()).days
                    if days_ago <= 60 and s.get("resolution", "") not in ("faded", "resolved"):
                        already_spiked = True
                        spike_info = s
        except Exception:
            pass

        card_name     = card.get("player_name", player)
        set_display   = card.get("set_name", set_name)
        card_num      = card.get("card_number")
        variation_val = card.get("variation")
        insert_val    = card.get("insert_set")
        is_rookie     = card.get("is_rookie", False)

        subtitle = set_display
        if card_num:      subtitle += f" #{card_num}"
        if variation_val: subtitle += f" · {variation_val}"
        if insert_val:    subtitle += f" · {insert_val}"
        subtitle += f" · {card_grade}"
        if is_rookie:     subtitle += " · 🌟 RC"

        current  = fv(card.get("current_price"))
        avg_30d  = fv(card.get("avg_price_30d"))
        avg_90d  = fv(card.get("avg_price_90d"))
        low_52w  = fv(card.get("low_52w"))
        high_52w = fv(card.get("high_52w"))
        sale_30d = card.get("sale_count_30d") or 0

        trend_str = "N/A"
        if current and avg_30d and avg_30d > 0:
            trend_str = f"{((current - avg_30d) / avg_30d) * 100:+.1f}%"

        pct_range = fv(card.get("pct_of_52w_range"))

        trend_pct_val = None
        price_ref = fv(card.get("avg_price_3d")) or fv(card.get("current_price"))
        if price_ref and avg_30d and avg_30d > 0:
            trend_pct_val = ((price_ref - avg_30d) / avg_30d) * 100

        verdict, color, reasoning = get_verdict(score, rules, already_spiked, pct_range, card, trend_pct_val)

        rule_plain = {
            "HR2 velocity": "Trading volume is too low right now",
            "HR4 recency":  f"Hasn't sold in {int(fv(card.get('days_since_last_sale')) or 0)} days — market gone quiet",
        }
        flag_lines   = [f"⚠️ {rule_plain.get(r['name'], r['reason'])}" for r in rules if not r["passed"] and r["name"] in rule_plain]
        failed_rules = [r for r in rules if not r["passed"]]
        if failed_rules and score >= 65:
            score_label = "Strong setup — blocked by rule"
        elif failed_rules and score >= 55:
            score_label = "Average setup — blocked by rule"
        else:
            score_label = "Strong" if score >= 65 else "Average" if score >= 55 else "Weak"

        icon = {"Buy": "✅", "Watch": "🟡", "Skip": "⬜", "Avoid": "❌", "Sell": "🔴"}.get(verdict, "")

        total_90d_sales = fv(card.get("sale_count_90d")) or 0
        low_data = total_90d_sales < 3
        low_data_warning = "\n\n⚠️ _Limited sales data for this card — treat this result with caution._" if low_data else ""

        embed = discord.Embed(
            title=f"{icon} {verdict} — {card_name}",
            description=(
                f"{subtitle}\n\n_{reasoning}_"
                + (f"\n\n" + "\n".join(flag_lines) if flag_lines else "")
                + low_data_warning
                + (f"\n\n📋 **On GIGA watchlist since {watchlist_entry.get('flagged_date')}**" if watchlist_entry else "")
            ),
            color=color,
        )

        if spike_info:
            sp = spike_info
            embed.add_field(name="⚠️ Already spiked",
                            value=f"Peaked at {fmt(sp.get('peak_spike_price'))} (+{sp.get('price_change_pct', 0):.0f}%) on {sp.get('spike_start_date')}. Don't chase it.",
                            inline=False)

        embed.add_field(name="Current price", value=f"**{fmt(current)}**", inline=True)
        embed.add_field(name="30d avg",       value=fmt(avg_30d),           inline=True)
        embed.add_field(name="90d avg",       value=fmt(avg_90d),           inline=True)
        embed.add_field(name="Year range",    value=f"{fmt(low_52w)} – {fmt(high_52w)}", inline=True)
        embed.add_field(name="Sales / 30d",   value=str(sale_30d),          inline=True)
        embed.add_field(name="Trend",         value=trend_str,              inline=True)
        embed.add_field(name="GIGA Score",    value=f"**{score} / 100** — {score_label}", inline=False)
        embed.set_footer(text="GIGA · Not financial advice")

        image_url = card.get("image_url")
        if image_url:
            embed.set_thumbnail(url=image_url)

        await interaction.followup.send(embed=embed)

    except Exception as e:
        import traceback
        print(f"[ERROR] embed build crashed: {e}")
        print(traceback.format_exc())
        await interaction.followup.send(f"[ERROR] Something went wrong: {e}")


# ===========================================================================
# AUTOCOMPLETE — all queries against sports.mv_card_metrics
# ===========================================================================

@evaluate.autocomplete("player")
async def eval_player_autocomplete(interaction: discord.Interaction, current: str):
    if len(current) < 2:
        return []
    try:
        result = supabase.rpc("search_player_names", {"p_search": current, "p_limit": 25}).execute()
        choices = []
        for row in (result.data or []):
            name = row.get("result_name", "")
            if not name:
                continue
            choices.append(app_commands.Choice(name=name[:100], value=name))
        return choices
    except Exception as e:
        print(f"[ERROR] eval_player_autocomplete: {e}")
        return []


@evaluate.autocomplete("set_name")
async def eval_set_autocomplete(interaction: discord.Interaction, current: str):
    try:
        player_val = interaction.namespace.player or ""
        result = supabase.rpc("search_set_names",
                              {"p_player": player_val, "p_search": current or "", "p_limit": 25}).execute()
        choices = []
        for row in (result.data or []):
            name = row.get("set_name", "")
            year = row.get("set_year", "?")
            if not name:
                continue
            choices.append(app_commands.Choice(name=f"{name} ({year})"[:100], value=name))
        return choices
    except Exception as e:
        print(f"[ERROR] eval_set_autocomplete: {e}")
        return []


@evaluate.autocomplete("grade")
async def eval_grade_autocomplete(interaction: discord.Interaction, current: str):
    try:
        player_val = interaction.namespace.player or ""
        set_val    = interaction.namespace.set_name or ""
        result = supabase.rpc("search_grades",
                              {"p_player": player_val, "p_set": set_val,
                               "p_search": current or "", "p_limit": 25}).execute()

        grade_order = ["Raw", "PSA 9", "PSA 10", "BGS 9", "BGS 9.5", "BGS 10",
                       "SGC 9", "SGC 9.5", "SGC 10", "CGC 9", "CGC 9.5", "CGC 10"]
        seen = {}
        for row in (result.data or []):
            g = (row.get("grade") or "").strip()
            if g and g not in seen:
                seen[g] = row.get("current_price")

        def sort_key(g):
            try: return grade_order.index(g)
            except ValueError: return len(grade_order)

        choices = []
        for g in sorted(seen.keys(), key=sort_key):
            price = seen[g]
            price_str = f" — ${float(price):.0f}" if price else ""
            choices.append(app_commands.Choice(name=f"{g}{price_str}", value=g))
            if len(choices) >= 25:
                break
        return choices
    except Exception as e:
        print(f"[ERROR] eval_grade_autocomplete: {e}")
        return []


@evaluate.autocomplete("variation")
async def eval_variation_autocomplete(interaction: discord.Interaction, current: str):
    try:
        player_val      = interaction.namespace.player or ""
        set_val         = interaction.namespace.set_name or ""
        card_number_val = interaction.namespace.card_number or ""

        result = supabase.rpc("search_variations", {
            "p_player":      player_val,
            "p_set":         set_val,
            "p_search":      current or "",
            "p_limit":       25,
            "p_card_number": card_number_val,
        }).execute()

        choices = []
        for row in (result.data or []):
            val = (row.get("variation") or "").strip()
            if not val:
                continue
            low  = row.get("price_low")
            high = row.get("price_high")
            if low and high and float(low) != float(high):
                price_str = f" — ${float(low):.0f}–${float(high):.0f}"
            elif low:
                price_str = f" — ${float(low):.0f}"
            else:
                price_str = ""
            choices.append(app_commands.Choice(name=f"{val}{price_str}"[:100], value=val))
            if len(choices) >= 25:
                break
        return choices
    except Exception as e:
        print(f"[ERROR] eval_variation_autocomplete: {e}")
        return []


# ===========================================================================
# RUN
# ===========================================================================

client.run(TOKEN)
