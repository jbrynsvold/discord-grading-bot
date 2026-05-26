import os
import discord
from discord import app_commands
from supabase import create_client, Client
from dotenv import load_dotenv
from datetime import date

load_dotenv()
TOKEN       = os.getenv("EVALUATE_BOT_TOKEN")   # separate bot token — see setup notes
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

def pct(val):
    if val is None:
        return "N/A"
    return f"{float(val):+.1f}%"

# ===========================================================================
# GIGASCORE — computed live from mv_card_metrics
# ===========================================================================

def compute_giga_score(m: dict) -> tuple[int, dict]:
    """
    Returns (total_score, component_breakdown).
    Mirrors the four-component formula in generate_candidates.py:
      - 52w range position  (0-30 pts)
      - Liquidity           (0-25 pts)
      - Volume acceleration (0-25 pts)
      - Price momentum      (0-20 pts)
    """
    pct_range     = fv(m.get("pct_of_52w_range"))
    sale_30d      = fv(m.get("sale_count_30d")) or 0
    sale_90d      = fv(m.get("sale_count_90d")) or 0
    sale_3d       = fv(m.get("sale_count_3d"))  or 0
    avg_3d        = fv(m.get("avg_price_3d"))
    avg_90d       = fv(m.get("avg_price_90d"))
    avg_30_90d    = fv(m.get("avg_price_30_90d"))
    has_90d       = bool(m.get("has_90d_data"))
    coalesce_3d   = avg_3d if avg_3d is not None else fv(m.get("current_price"))

    # Component 1 — 52w range position
    if pct_range is None:
        c1 = 0
    elif pct_range <= 10: c1 = 30
    elif pct_range <= 20: c1 = 25
    elif pct_range <= 30: c1 = 20
    elif pct_range <= 45: c1 = 12
    elif pct_range <= 60: c1 = 5
    else: c1 = 0

    # Component 2 — Liquidity (sale_count_90d)
    liq = sale_90d if has_90d else sale_30d * 3
    if   liq >= 200: c2 = 25
    elif liq >= 100: c2 = 21
    elif liq >= 50:  c2 = 17
    elif liq >= 20:  c2 = 12
    elif liq >= 10:  c2 = 7
    elif liq >= 3:   c2 = 3
    else:            c2 = 0

    # Component 3 — Volume acceleration (GREATEST of three comparisons)
    def vel_pts(ratio):
        if ratio is None: return 0
        if ratio >= 1.5: return 25
        if ratio >= 1.2: return 18
        if ratio >= 0.8: return 10
        return 0

    base_90d = (sale_90d / 3.0) if has_90d and sale_90d else None
    r1 = (sale_30d / base_90d) if base_90d else None
    r2 = ((sale_3d * 10) / sale_30d) if sale_30d > 0 else None
    r3 = ((sale_3d * 30) / sale_90d) if has_90d and sale_90d > 0 else None
    c3 = max(vel_pts(r1), vel_pts(r2), vel_pts(r3))

    # Component 4 — Price momentum (avg_price_3d vs avg_price_30_90d baseline)
    baseline = avg_30_90d or avg_90d
    if coalesce_3d and baseline and baseline > 0:
        ratio = coalesce_3d / baseline
        if   ratio <= 0.90: c4 = 20   # dipping vs baseline — good entry
        elif ratio <= 1.00: c4 = 15
        elif ratio <= 1.10: c4 = 10
        elif ratio <= 1.25: c4 = 5
        else:               c4 = 0    # already run up significantly
    else:
        c4 = 0

    total = min(100, c1 + c2 + c3 + c4)
    breakdown = {
        "range_pos":   (c1, 30,  f"{pct_range:.1f}% of 52w range" if pct_range is not None else "no data"),
        "liquidity":   (c2, 25,  f"{int(liq)} sales / 90d"),
        "velocity":    (c3, 25,  f"best ratio: {max(r or 0 for r in [r1,r2,r3] if r is not None):.2f}x" if any(r is not None for r in [r1,r2,r3]) else "no data"),
        "momentum":    (c4, 20,  f"{coalesce_3d:.2f} vs {baseline:.2f} baseline" if (coalesce_3d and baseline) else "no data"),
    }
    return total, breakdown


# ===========================================================================
# HARD RULE CHECKS
# ===========================================================================

def check_hard_rules(m: dict) -> list[dict]:
    """
    Returns list of rule results: {name, passed, reason}
    HR2 — velocity gate (card must be trading at or above seasonal pace)
    HR3 — trend gate (card must not be in steep drawdown)
    HR4 — recency gate (must have sold within 14 days)
    """
    rules = []
    sale_30d   = fv(m.get("sale_count_30d")) or 0
    sale_90d   = fv(m.get("sale_count_90d")) or 0
    sale_3d    = fv(m.get("sale_count_3d"))  or 0
    has_90d    = bool(m.get("has_90d_data"))
    days_since = fv(m.get("days_since_last_sale"))
    avg_3d     = fv(m.get("avg_price_3d"))
    avg_30d    = fv(m.get("avg_price_30d"))
    current    = fv(m.get("current_price"))

    # HR2 — velocity: 30d pace vs 90d baseline
    base_90d = (sale_90d / 3.0) if has_90d and sale_90d else None
    if base_90d and base_90d > 0:
        vel_ratio = sale_30d / base_90d
        if vel_ratio >= 0.5:
            rules.append({"name": "HR2 velocity", "passed": True,
                          "reason": f"trading at {vel_ratio:.1f}x seasonal pace"})
        else:
            rules.append({"name": "HR2 velocity", "passed": False,
                          "reason": f"only {vel_ratio:.1f}x seasonal pace — below minimum threshold"})
    else:
        rules.append({"name": "HR2 velocity", "passed": True,
                      "reason": "insufficient 90d data — not blocked"})

    # HR3 — trend: not in >15% drawdown vs 30d avg
    price_ref = avg_3d or current
    if price_ref and avg_30d and avg_30d > 0:
        trend_pct = ((price_ref - avg_30d) / avg_30d) * 100
        if trend_pct > -15:
            rules.append({"name": "HR3 trend", "passed": True,
                          "reason": f"{trend_pct:+.1f}% vs 30d avg — not in drawdown"})
        else:
            rules.append({"name": "HR3 trend", "passed": False,
                          "reason": f"{trend_pct:+.1f}% vs 30d avg — steep drawdown"})
    else:
        rules.append({"name": "HR3 trend", "passed": True,
                      "reason": "insufficient data — not blocked"})

    # HR4 — recency: sold within 14 days
    if days_since is not None:
        if days_since <= 14:
            rules.append({"name": "HR4 recency", "passed": True,
                          "reason": f"last sale {int(days_since)}d ago"})
        else:
            rules.append({"name": "HR4 recency", "passed": False,
                          "reason": f"last sale {int(days_since)}d ago — market gone quiet"})
    else:
        rules.append({"name": "HR4 recency", "passed": False,
                      "reason": "no sale date on record"})

    return rules


# ===========================================================================
# VERDICT
# ===========================================================================

def get_verdict(score: int, rules: list[dict], already_spiked: bool,
                pct_of_range: float | None) -> tuple[str, int, str]:
    """
    Returns (verdict_label, embed_color, reasoning).
    """
    all_pass = all(r["passed"] for r in rules)
    failed   = [r for r in rules if not r["passed"]]

    if already_spiked:
        return (
            "Sell / Pass",
            0xED4245,
            "This card has already spiked in our records. The move has been made — don't chase it.",
        )

    if pct_of_range is not None and pct_of_range > 110:
        return (
            "Sell / Pass",
            0xED4245,
            f"Price is currently {pct_of_range:.0f}% of its 52-week range — trading well above its historical high. Not a buy at this level.",
        )

    if not all_pass:
        reasons = "; ".join(r["reason"] for r in failed)
        return (
            "Pass",
            0x888780,
            f"Doesn't clear our criteria. Failed: {reasons}.",
        )

    if score >= 65:
        return (
            "Buy",
            0x1D9E75,
            "Passes all hard rules with a strong GigaScore. Good setup — near lows, liquid, momentum building.",
        )
    if score >= 55:
        return (
            "Hold",
            0xEF9F27,
            "Passes hard rules but GigaScore is middling. Worth watching — not a strong entry right now.",
        )
    return (
        "Pass",
        0x888780,
        f"GigaScore of {score} is below our threshold (55). Not enough setup to recommend.",
    )


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

EVALUATE_SELECT = (
    "player_name, set_name, set_year, sport, card_number, variation, "
    "insert_set, canonical_name, is_rookie, card_id, grade, "
    "current_price, avg_price_3d, avg_price_30d, avg_price_90d, "
    "avg_price_30_90d, sale_count_3d, sale_count_30d, sale_count_90d, "
    "pct_of_52w_range, high_52w, low_52w, has_90d_data, "
    "days_since_last_sale, last_sale_date"
)

@tree.command(name="evaluate", description="Should you buy, hold, or pass on this card? GIGA scores it against our full criteria.")
@app_commands.describe(
    player="Player or character name — start typing for suggestions",
    set_name="Set name — start typing for filtered suggestions",
    grade="Grade (e.g. Raw, PSA 10, BGS 9.5) — default: Raw",
    card_number="Optional: card number to narrow results",
    variation="Parallel or variation (e.g. Base, Blue Refractor, Gold Prizm)",
)
async def evaluate(
    interaction: discord.Interaction,
    player: str,
    set_name: str,
    grade: str = "Raw",
    variation: str = "Base",
    card_number: str = None,
):
    await interaction.response.defer(ephemeral=True)

    # --- Card lookup (same 3-pass fallback as grade bot) ---
    try:
        def base_query(player_field="player_name"):
            q = (
                supabase.table("mv_card_metrics")
                .select(EVALUATE_SELECT)
                .ilike(player_field, f"%{player}%")
                .ilike("set_name", f"%{set_name}%")
                .ilike("grade", f"%{grade}%")
            )
            q = q.ilike("variation", f"%{variation}%")
            if card_number:
                q = q.ilike("card_number", f"%{card_number}%")
            return q

        result = base_query().limit(3).execute()

        # Pass 2: relax grade but keep variation filter
        if not result.data:
            q2 = (
                supabase.table("mv_card_metrics")
                .select(EVALUATE_SELECT)
                .ilike("player_name", f"%{player}%")
                .ilike("set_name", f"%{set_name}%")
                .ilike("variation", f"%{variation}%")
            )
            if card_number:
                q2 = q2.ilike("card_number", f"%{card_number}%")
            result = q2.limit(3).execute()

        # Pass 3: canonical_name fallback
        if not result.data:
            result = base_query("canonical_name").limit(3).execute()

    except Exception as e:
        await interaction.followup.send(f"[ERROR] Database error: {e}")
        return

    if not result.data:
        await interaction.followup.send(
            f"No card found for **{player}** in **{set_name}** ({grade}).\n"
            "Try a partial name, different grade, or add a card number to narrow it down."
        )
        return

    # If multiple grades came back, prefer the requested grade
    card = result.data[0]
    if len(result.data) > 1:
        grade_lower = grade.lower()
        for row in result.data:
            if row.get("grade", "").lower() == grade_lower:
                card = row
                break

    card_id   = card.get("card_id")
    card_grade = card.get("grade", grade)

    # --- Compute GigaScore ---
    score, breakdown = compute_giga_score(card)

    # --- Hard rules ---
    rules = check_hard_rules(card)

    # --- Watchlist check (watching status only) ---
    watchlist_entry = None
    try:
        for schema in ("tcg", "sports"):
            r = (
                supabase.table(f"{schema}.candidate_watchlist")
                .select("flagged_date, status, predicted_catalyst, call_type, call_horizon_days")
                .eq("card_id", card_id)
                .ilike("grade", f"%{card_grade}%")
                .eq("status", "watching")
                .order("flagged_date", desc=True)
                .limit(1)
                .execute()
            )
            if r.data:
                watchlist_entry = r.data[0]
                break
    except Exception:
        pass  # watchlist check is best-effort

    # --- Spike library check ---
    already_spiked = False
    spike_info = None
    try:
        r = (
            supabase.table("spike_library")
            .select("spike_start_date, peak_spike_price, price_change_pct, resolution")
            .eq("card_id", card_id)
            .ilike("grade", f"%{card_grade}%")
            .order("spike_start_date", desc=True)
            .limit(1)
            .execute()
        )
        if r.data:
            s = r.data[0]
            # Only flag as "already spiked" if the spike is recent (within 60 days)
            # and not resolved/faded
            spike_date = s.get("spike_start_date")
            resolution = s.get("resolution", "")
            if spike_date:
                from datetime import datetime
                days_ago = (date.today() - datetime.strptime(spike_date, "%Y-%m-%d").date()).days
                if days_ago <= 60 and resolution not in ("faded", "resolved"):
                    already_spiked = True
                    spike_info = s
    except Exception:
        pass  # spike check is best-effort

    # --- Verdict ---
    pct_range = fv(card.get("pct_of_52w_range"))
    verdict, color, reasoning = get_verdict(score, rules, already_spiked, pct_range)

    # --- Build embed ---
    card_name  = card.get("player_name", player)
    set_display = card.get("set_name", set_name)
    card_num   = card.get("card_number")
    variation_val = card.get("variation")
    insert_val = card.get("insert_set")
    is_rookie  = card.get("is_rookie", False)

    subtitle = f"{set_display}"
    if card_num:   subtitle += f" #{card_num}"
    if variation_val: subtitle += f" · {variation_val}"
    if insert_val: subtitle += f" · {insert_val}"
    subtitle += f" · {card_grade}"
    if is_rookie:  subtitle += " · 🌟 RC"

    embed = discord.Embed(
        title=f"GIGA Evaluate — {card_name}",
        description=subtitle,
        color=color,
    )

    # Verdict field
    verdict_icons = {
        "Buy":          "✅",
        "Hold":         "🟡",
        "Pass":         "⬜",
        "Sell / Pass":  "❌",
    }
    icon = verdict_icons.get(verdict, "")
    embed.add_field(
        name="Verdict",
        value=f"{icon} **{verdict}**\n{reasoning}",
        inline=False,
    )

    # Watchlist flag (only if watching)
    if watchlist_entry:
        flagged = watchlist_entry.get("flagged_date", "")
        catalyst = watchlist_entry.get("predicted_catalyst", "")
        call_type = watchlist_entry.get("call_type", "")
        horizon = watchlist_entry.get("call_horizon_days")
        wl_str = f"📋 **On GIGA watchlist since {flagged}**"
        if catalyst:  wl_str += f" · {catalyst.replace('_', ' ')}"
        if call_type: wl_str += f" · {call_type.replace('_', ' ')}"
        if horizon:   wl_str += f" · {horizon}d horizon"
        embed.add_field(name="\u200b", value=wl_str, inline=False)

    # Spike warning
    if spike_info:
        sp = spike_info
        embed.add_field(
            name="⚠️ Recent spike on record",
            value=(
                f"Started {sp.get('spike_start_date')} · "
                f"Peak {fmt(sp.get('peak_spike_price'))} · "
                f"+{sp.get('price_change_pct', 0):.0f}%"
            ),
            inline=False,
        )

    # Price snapshot
    current   = fv(card.get("current_price"))
    avg_30d   = fv(card.get("avg_price_30d"))
    avg_90d   = fv(card.get("avg_price_90d"))
    low_52w   = fv(card.get("low_52w"))
    high_52w  = fv(card.get("high_52w"))
    trend_str = "N/A"
    if current and avg_30d and avg_30d > 0:
        trend_val = ((current - avg_30d) / avg_30d) * 100
        trend_str = f"{trend_val:+.1f}%"

    embed.add_field(
        name="Price",
        value=(
            f"Current: **{fmt(current)}**\n"
            f"30d avg: {fmt(avg_30d)}\n"
            f"90d avg: {fmt(avg_90d)}\n"
            f"52w: {fmt(low_52w)} – {fmt(high_52w)}\n"
            f"vs 30d avg: {trend_str}"
        ),
        inline=True,
    )

    # GigaScore breakdown
    comp_lines = []
    labels = {
        "range_pos": "52w range",
        "liquidity": "Liquidity",
        "velocity":  "Velocity",
        "momentum":  "Momentum",
    }
    for key, (pts, max_pts, detail) in breakdown.items():
        bar_filled = round(pts / max_pts * 8) if max_pts else 0
        bar = "█" * bar_filled + "░" * (8 - bar_filled)
        comp_lines.append(f"`{bar}` {labels[key]}: **{pts}/{max_pts}**")

    embed.add_field(
        name=f"GigaScore: {score}/100",
        value="\n".join(comp_lines),
        inline=True,
    )

    # Hard rules
    rule_lines = []
    for r in rules:
        icon_r = "✓" if r["passed"] else "✗"
        rule_lines.append(f"`{icon_r}` {r['name']} — {r['reason']}")
    embed.add_field(
        name="Hard rules",
        value="\n".join(rule_lines),
        inline=False,
    )

    embed.set_footer(text="GIGA platform · Prices from 30-day median sales · Not financial advice")
    await interaction.followup.send(embed=embed)


# ===========================================================================
# AUTOCOMPLETE — same pattern as grade bot
# ===========================================================================

@evaluate.autocomplete("player")
async def eval_player_autocomplete(interaction: discord.Interaction, current: str):
    if len(current) < 2:
        return []
    try:
        # Use RPC function that queries sports + tcg schemas via trigram indexes
        result = supabase.rpc(
            "search_player_names",
            {"p_search": current, "p_limit": 25}
        ).execute()

        choices = []
        for row in (result.data or []):
            name = row.get("result_name", "")
            if not name:
                continue
            label = name if len(name) <= 100 else name[:97] + "..."
            choices.append(app_commands.Choice(name=label, value=name))
        return choices
    except Exception as e:
        print(f"[ERROR] eval_player_autocomplete: {e}")
        return []


@evaluate.autocomplete("set_name")
async def eval_set_autocomplete(interaction: discord.Interaction, current: str):
    try:
        player_val = interaction.namespace.player
        # Join cards -> card_sets for fast indexed lookup
        query = (
            supabase.table("cards")
            .select("player_name, canonical_name, card_sets(name, year)")
        )
        if player_val and len(player_val) >= 2:
            query = query.ilike("player_name", f"%{player_val}%")
        if current and len(current) >= 1:
            query = query.ilike("card_sets.name", f"%{current}%")
        result = query.limit(100).execute()

        from collections import Counter
        rows_by_set = {}
        set_count = Counter()
        for row in result.data:
            cs = row.get("card_sets") or {}
            set_name = cs.get("name")
            year = cs.get("release_year", "?")
            if not set_name:
                continue
            set_count[set_name] += 1
            if set_name not in rows_by_set:
                rows_by_set[set_name] = year

        seen = set()
        choices = []
        for set_name, year in rows_by_set.items():
            if set_name in seen:
                continue
            seen.add(set_name)
            label = f"{set_name} ({year})"
            if len(label) > 100:
                label = label[:97] + "..."
            choices.append(app_commands.Choice(name=label, value=set_name))
            if len(choices) >= 25:
                break
        return choices
    except Exception as e:
        print(f"[ERROR] eval_set_autocomplete: {e}")
        return []


@evaluate.autocomplete("grade")
async def eval_grade_autocomplete(interaction: discord.Interaction, current: str):
    """Autocomplete grade options — only hits mv_card_metrics once player+set known."""
    try:
        player_val = interaction.namespace.player
        set_val    = interaction.namespace.set_name
        # By the time grade is being typed we have player+set — safe to hit mv_card_metrics
        # with specific filters so it uses the index properly
        query = (
            supabase.table("mv_card_metrics")
            .select("grade, current_price")
        )
        if player_val and len(player_val) >= 2:
            query = query.ilike("player_name", f"%{player_val}%")
        if set_val and len(set_val) >= 2:
            query = query.ilike("set_name", f"%{set_val}%")
        if current and len(current) >= 1:
            query = query.ilike("grade", f"%{current}%")
        result = query.limit(100).execute()

        # Sort grades in a logical order
        grade_order = ["Raw", "PSA 9", "PSA 10", "BGS 9", "BGS 9.5", "BGS 10",
                       "SGC 9", "SGC 9.5", "SGC 10", "CGC 9", "CGC 9.5", "CGC 10"]

        seen = {}
        for row in result.data:
            g = row.get("grade", "").strip()
            if not g or g in seen:
                continue
            seen[g] = fv(row.get("current_price"))

        # Sort by known order, then alphabetically for unknowns
        def sort_key(g):
            try:
                return grade_order.index(g)
            except ValueError:
                return len(grade_order)

        choices = []
        for g in sorted(seen.keys(), key=sort_key):
            price = seen[g]
            price_str = f" — ${price:.0f}" if price else ""
            label = f"{g}{price_str}"
            choices.append(app_commands.Choice(name=label, value=g))
            if len(choices) >= 25:
                break
        return choices
    except Exception as e:
        print(f"[ERROR] eval_grade_autocomplete: {e}")
        return []


@evaluate.autocomplete("variation")
async def eval_variation_autocomplete(interaction: discord.Interaction, current: str):
    try:
        player_val = interaction.namespace.player
        set_val    = interaction.namespace.set_name
        # player+set already known here — filtered query hits index
        query = (
            supabase.table("mv_card_metrics")
            .select("variation, current_price")
            .not_.is_("variation", "null")
        )
        if player_val and len(player_val) >= 2:
            query = query.ilike("player_name", f"%{player_val}%")
        if set_val and len(set_val) >= 2:
            query = query.ilike("set_name", f"%{set_val}%")
        if current and len(current) >= 1:
            query = query.ilike("variation", f"%{current}%")
        result = query.limit(100).execute()

        seen = set()
        choices = []
        for row in result.data:
            val = (row.get("variation") or "").strip()
            if not val or val in seen:
                continue
            seen.add(val)
            price = fv(row.get("current_price"))
            price_str = f" — ${price:.0f}" if price else ""
            label = f"{val}{price_str}"
            if len(label) > 100:
                label = label[:97] + "..."
            choices.append(app_commands.Choice(name=label, value=val))
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
