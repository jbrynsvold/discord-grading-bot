import os
import discord
from discord import app_commands
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ===========================================================================
# SHARED HELPERS
# ===========================================================================

def format_currency(amount) -> str:
    if amount is None:
        return "N/A"
    amount = float(amount)
    if amount >= 0:
        return f"${amount:,.2f}"
    return f"-${abs(amount):,.2f}"

def fv(val):
    """Safe float conversion — returns None if null."""
    return float(val) if val is not None else None

# Categories that don't require a year for meaningful search
TCG_CATEGORIES = {"Pokemon", "Yu-Gi-Oh", "Other TCG", "Non-Sport Vintage"}

# ===========================================================================
# SELL COMMAND DATA
# ===========================================================================

PLATFORMS = {
    "ebay":            {"name": "eBay",               "fee_pct": 0.1295, "fixed_fee": 0.30, "note": "Largest buyer pool. Best for quick sales.",             "emoji": "🟦"},
    "whatnot":         {"name": "Whatnot",             "fee_pct": 0.08,   "fixed_fee": 0.00, "note": "Live auctions. Good if you have an audience.",          "emoji": "🟣"},
    "facebook":        {"name": "Facebook Groups",     "fee_pct": 0.00,   "fixed_fee": 0.00, "note": "No fees but requires BST reputation.",                  "emoji": "🔵"},
    "myslabs":         {"name": "MySlabs",             "fee_pct": 0.05,   "fixed_fee": 0.00, "note": "Low fees, growing graded card marketplace.",            "emoji": "🟤"},
    "pwcc_marketplace":{"name": "PWCC Marketplace",   "fee_pct": 0.10,   "fixed_fee": 0.00, "note": "Serious buyers, good for mid-to-high graded cards.",    "emoji": "⚫"},
    "pwcc_weekly":     {"name": "PWCC Weekly Auction", "fee_pct": 0.10,   "fixed_fee": 0.00, "note": "Auction format drives competitive bidding.",            "emoji": "🔶"},
    "goldin":          {"name": "Goldin",              "fee_pct": 0.15,   "fixed_fee": 0.00, "note": "Premium auction house for high-value cards.",           "emoji": "🟡"},
    "pwcc_premier":    {"name": "PWCC Premier",        "fee_pct": 0.12,   "fixed_fee": 0.00, "note": "Consignment for high-value cards. Curated audience.",   "emoji": "🏆"},
    "iconic":          {"name": "Iconic Auctions",     "fee_pct": 0.15,   "fixed_fee": 0.00, "note": "Boutique auction house for premium cards.",             "emoji": "💎"},
}

def get_tier(sale_price: float) -> dict:
    if sale_price < 100:
        return {"tier": "Budget",  "platforms": ["ebay", "whatnot", "facebook"],                                   "recommended": "ebay",         "advice": "eBay gives the widest buyer pool. Facebook Groups work well if you have BST rep — no fees."}
    elif sale_price < 500:
        return {"tier": "Mid",     "platforms": ["ebay", "whatnot", "myslabs", "pwcc_marketplace"],                "recommended": "myslabs",      "advice": "MySlabs has the lowest fees at this range. eBay works if you need a fast sale."}
    elif sale_price < 2000:
        return {"tier": "High",    "platforms": ["ebay", "myslabs", "pwcc_marketplace", "pwcc_weekly", "goldin"],  "recommended": "pwcc_weekly",  "advice": "PWCC Weekly brings serious bidders. Goldin worth considering for cards with strong collector demand."}
    elif sale_price < 10000:
        return {"tier": "Premium", "platforms": ["pwcc_premier", "goldin", "iconic"],                              "recommended": "pwcc_premier", "advice": "Consignment is worth it here. Get quotes from PWCC Premier and Goldin before committing.", "consignment_note": True}
    else:
        return {"tier": "Elite",   "platforms": [],                                                                "recommended": None,           "advice": "At $10k+, work directly with a broker — PWCC Premier, Goldin, Heritage Auctions, or Probstein123. Fees are negotiable.", "broker_note": True}

def calc_net(sale_price, fee_pct, fixed_fee, purchase_price, grading_cost):
    return sale_price - (sale_price * fee_pct + fixed_fee) - purchase_price - grading_cost

# ===========================================================================
# GRADE COMMAND DATA
# ===========================================================================

GRADERS = {
    "PSA": {
        "default_tier": "Value", "default_cost": 27.99,
        "tiers": {
            "Value":         (27.99,  "~95 business days", 500,   "Cheapest no-membership tier"),
            "Value Plus":    (44.99,  "~40 business days", 500,   "Faster, same value cap"),
            "Value Max":     (59.99,  "~30 business days", 1000,  "Higher value cap"),
            "Regular":       (74.99,  "~20 business days", 1500,  "Most common mid-tier"),
            "Express":       (160.00, "~10 business days", 2999,  "Fast turnaround"),
            "Super Express": (300.00, "~5 business days",  4999,  "Highest priority"),
        },
        "notes": "Highest resale premiums. Best liquidity for most sports cards.",
        "emoji": "🟦",
        "membership_note": "PSA Collectors Club ($149/yr) unlocks Value Bulk at ~$21.99/card (20+ cards)",
    },
    "BGS": {
        "default_tier": "Base", "default_cost": 14.95,
        "tiers": {
            "Base":     (14.95,  "~75 days", None, "No membership needed. Sub-grades included free."),
            "Standard": (34.95,  "~45 days", None, "Best balance of cost and speed"),
            "Express":  (79.95,  "~15 days", None, "Fast turnaround"),
            "Priority": (124.95, "~5 days",  None, "Fastest BGS service"),
        },
        "notes": "Sub-grades free on every card. Best for modern chrome/autos. Black Label (quad 10s) commands huge premiums.",
        "emoji": "⚫",
        "membership_note": "No annual membership required.",
    },
    "SGC": {
        "default_tier": "Standard", "default_cost": 15.00,
        "tiers": {
            "Standard":  (15.00, "~15-20 business days", 1500, "Best value for speed. No upcharges on modern cards."),
            "Immediate": (40.00, "~1-2 business days",   1500, "Fastest turnaround in the industry"),
        },
        "notes": "Fast turnaround. Great for vintage and budget submissions. Free auto grade on cards that receive a 10.",
        "emoji": "🟤",
        "membership_note": "No membership required.",
    },
    "CGC": {
        "default_tier": "Economy", "default_cost": 17.00,
        "tiers": {
            "Bulk":        (14.00,  "~80 days", 500,   "25-card minimum required"),
            "Economy":     (17.00,  "~40 days", 1000,  "No minimum. Best single-card budget option."),
            "Express":     (50.00,  "~10 days", 3000,  "Fast and mid-range"),
            "WalkThrough": (150.00, "~2 days",  10000, "Fastest CGC service"),
        },
        "notes": "Competitive pricing. Strong for TCG (Pokemon, MTG). Free account; paid members save 10-20%.",
        "emoji": "🟡",
        "membership_note": "Free account available. Associate/Premium: 10% off. Elite: 20% off. Starts at $25/yr.",
    },
}

def get_grader_rec(raw, psa9, psa10, grading_score, vintage):
    if vintage: return "PSA"
    if grading_score >= 8.0 and psa10 and psa10 > 200: return "PSA"
    if psa10 and psa10 < 100: return "SGC"
    if psa10 and psa10 < 300: return "CGC"
    return "PSA"

def should_grade(raw, psa9, psa10, grading_cost, grading_score, psa9_mult):
    if not raw or not psa9:
        return None, "Not enough price data to make a recommendation.", False

    total_cost = raw + grading_cost
    psa10_mult_actual = (psa10 / total_cost) if (psa10 and total_cost > 0) else 0
    uplift = psa9 - raw - grading_cost
    hard_to_grade = psa9_mult and psa9_mult >= 5.0

    warning = ""
    if hard_to_grade:
        warning = f"\n⚠️ PSA 9 is {psa9_mult:.1f}x raw — this card is historically difficult to grade. High risk of low grade or rejection. Verify condition carefully before submitting."

    # Strong yes: PSA 10 is 2.5x+ of total investment
    if psa10_mult_actual >= 2.5:
        reason = f"PSA 10 ({format_currency(psa10)}) is {psa10_mult_actual:.1f}x your total cost ({format_currency(total_cost)}). Strong grading candidate.{warning}"
        return True, reason, hard_to_grade

    # Clear no: PSA 9 doesn't cover costs
    if uplift < 0:
        reason = f"PSA 9 nets you {format_currency(uplift)} after grading cost. Sell raw.{warning}"
        return False, reason, hard_to_grade

    # Marginal: use grading score as tiebreaker
    if uplift < 30:
        if grading_score >= 50:
            reason = f"Marginal uplift ({format_currency(uplift)}) but grading score of {grading_score:.0f}/100 suggests card grades well. Proceed if condition is strong.{warning}"
            return True, reason, hard_to_grade
        else:
            reason = f"Upside of only {format_currency(uplift)} and grading score of {grading_score:.0f}/100 is below average. Sell raw.{warning}"
            return False, reason, hard_to_grade

    # Clear yes
    reason = f"PSA 9 uplift of {format_currency(uplift)} over raw justifies the ${grading_cost:.2f} grading cost.{warning}"
    return True, reason, hard_to_grade

# ===========================================================================
# Bot setup
# ===========================================================================

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

@client.event
async def on_ready():
    await tree.sync()
    print(f"[OK] CardBot is online as {client.user}")

# ===========================================================================
# /sell
# ===========================================================================

@tree.command(name="sell", description="Calculate net profit and get platform recommendations for selling a card")
@app_commands.describe(
    sale_price="Expected sale price in USD",
    purchase_price="What you paid for the card (default: 0)",
    grading_cost="Grading cost if applicable (default: 0)",
)
async def sell(interaction: discord.Interaction, sale_price: float, purchase_price: float = 0.0, grading_cost: float = 0.0):
    await interaction.response.defer()
    tier_data = get_tier(sale_price)

    if tier_data.get("broker_note"):
        embed = discord.Embed(title=f"💎 Elite Tier — ${sale_price:,.0f}", description=tier_data["advice"], color=0xFFD700)
        embed.add_field(name="Who to Contact", value="• **PWCC Premier** — pwccmarketplace.com\n• **Goldin** — goldin.co\n• **Heritage Auctions** — ha.com\n• **Probstein123** — probstein123.com", inline=False)
        embed.set_footer(text="Fees and terms are negotiable at this level. Get quotes from multiple houses.")
        await interaction.followup.send(embed=embed)
        return

    color_map = {"Budget": 0x57F287, "Mid": 0x5865F2, "High": 0xFEE75C, "Premium": 0xED4245}
    embed = discord.Embed(title=f"💰 Sell Analysis — ${sale_price:,.2f}", color=color_map.get(tier_data["tier"], 0x5865F2))
    parts = [f"**Sale Price:** ${sale_price:,.2f}"]
    if purchase_price > 0: parts.append(f"**Paid:** ${purchase_price:,.2f}")
    if grading_cost > 0: parts.append(f"**Grading:** ${grading_cost:,.2f}")
    embed.description = "  ·  ".join(parts)

    lines = []
    for key in tier_data["platforms"]:
        p = PLATFORMS[key]
        net = calc_net(sale_price, p["fee_pct"], p["fixed_fee"], purchase_price, grading_cost)
        fee_amt = sale_price * p["fee_pct"] + p["fixed_fee"]
        star = " ⭐" if key == tier_data["recommended"] else ""
        fee_str = "No fees" if p["fee_pct"] == 0 and p["fixed_fee"] == 0 else (f"{p['fee_pct']*100:.1f}% + ${p['fixed_fee']:.2f}" if p["fixed_fee"] > 0 else f"{p['fee_pct']*100:.1f}%")
        lines.append(f"{p['emoji']} **{p['name']}**{star}\n  Fee: {fee_str} (${fee_amt:,.2f})  →  Net: **{format_currency(net)}**\n  _{p['note']}_")

    embed.add_field(name=f"📊 Platform Breakdown ({tier_data['tier']} Tier)", value="\n\n".join(lines), inline=False)

    if tier_data["recommended"]:
        bp = PLATFORMS[tier_data["recommended"]]
        best_net = calc_net(sale_price, bp["fee_pct"], bp["fixed_fee"], purchase_price, grading_cost)
        embed.add_field(name="⭐ Recommendation", value=f"**{bp['name']}** — nets you **{format_currency(best_net)}**\n{tier_data['advice']}", inline=False)

    if tier_data.get("consignment_note"):
        embed.add_field(name="📋 Consignment Tip", value="Get quotes from multiple houses before committing. Rates shown are standard — some are negotiable.", inline=False)

    embed.set_footer(text="Fees are estimates. Always verify current rates before selling.")
    await interaction.followup.send(embed=embed)

# ===========================================================================
# /grade — with autocomplete on player and set_name
# ===========================================================================

@tree.command(name="grade", description="Look up a card and get a grading company comparison + recommendation")
@app_commands.describe(
    player="Player or character name — start typing for suggestions",
    set_name="Set name — start typing for filtered suggestions",
    card_number="Optional: card number to narrow results (e.g. 4, 025, SWSH001)",
    is_vintage="Is this a vintage card (pre-1980)?",
    override_tier="Optional: use a faster tier (e.g. Express, Regular) for paid members",
)
@app_commands.choices(is_vintage=[
    app_commands.Choice(name="No (Modern)", value=0),
    app_commands.Choice(name="Yes (Vintage, pre-1980)", value=1),
])
async def grade(
    interaction: discord.Interaction,
    player: str,
    set_name: str,
    card_number: str = None,
    is_vintage: int = 0,
    override_tier: str = None,
):
    await interaction.response.defer()

    try:
        # Check if TCG card — year not required for these
        sport_check = supabase.table("mv_grade_premiums") \
            .select("sport") \
            .ilike("player_name", f"%{player}%") \
            .limit(1).execute()

        sport = sport_check.data[0]["sport"] if sport_check.data else None
        is_tcg = sport in TCG_CATEGORIES

        query = supabase.table("mv_grade_premiums") \
            .select("player_name, set_name, set_year, card_number, variation, is_rookie, sport, "
                    "raw_price, psa9_price, psa10_price, grading_score, "
                    "raw_to_psa9_mult, raw_to_psa10_mult, psa9_to_psa10_mult, "
                    "bgs9_price, bgs95_price, bgs10_price, "
                    "sgc9_price, sgc95_price, sgc10_price, "
                    "cgc9_price, cgc95_price, cgc10_price, cgc10_pristine_price") \
            .ilike("player_name", f"%{player}%") \
            .ilike("set_name", f"%{set_name}%")

        if card_number:
            query = query.ilike("card_number", f"%{card_number}%")

        result = query.limit(1).execute()

    except Exception as e:
        await interaction.followup.send(f"[ERROR] Database query failed: {e}")
        return

    if not result.data:
        await interaction.followup.send(
            f"No card found for **{player}** in **{set_name}**.\n"
            f"Try adjusting the name or set — partial matches work. Use card number to narrow results if needed."
        )
        return

    card = result.data[0]
    raw   = fv(card.get("raw_price"))
    psa9  = fv(card.get("psa9_price"))
    psa10 = fv(card.get("psa10_price"))
    gs    = fv(card.get("grading_score")) or 0.0
    vintage = bool(is_vintage)

    # BGS
    bgs9  = fv(card.get("bgs9_price"))
    bgs95 = fv(card.get("bgs95_price"))
    bgs10 = fv(card.get("bgs10_price"))

    # SGC
    sgc9  = fv(card.get("sgc9_price"))
    sgc95 = fv(card.get("sgc95_price"))
    sgc10 = fv(card.get("sgc10_price"))

    # CGC
    cgc9   = fv(card.get("cgc9_price"))
    cgc95  = fv(card.get("cgc95_price"))
    cgc10  = fv(card.get("cgc10_price"))
    cgc10p = fv(card.get("cgc10_pristine_price"))

    # Price multipliers needed for recommendation logic
    psa9_mult  = fv(card.get("raw_to_psa9_mult"))
    psa10_mult = fv(card.get("raw_to_psa10_mult"))
    p9p10_mult = fv(card.get("psa9_to_psa10_mult"))

    rec_grader = get_grader_rec(raw, psa9, psa10, gs, vintage)
    grading_cost_default = GRADERS[rec_grader]["default_cost"]
    grade_it, grade_reason, hard_to_grade = should_grade(raw, psa9, psa10, grading_cost_default, gs, psa9_mult)

    color = 0x57F287 if grade_it else (0xED4245 if grade_it is False else 0x5865F2)
    embed = discord.Embed(
        title=f"🔎 Grade Analysis — {card['player_name']}",
        description=(
            f"{card['set_year']} {card['set_name']} #{card.get('card_number', '?')}"
            + (f" · {card['variation']}" if card.get('variation') else "")
            + (" · 🌟 Rookie" if card.get('is_rookie') else "")
        ),
        color=color,
    )

    embed.add_field(
        name="💵 Price Snapshot (Raw vs PSA)",
        value=(
            f"Raw: **{format_currency(raw)}**\n"
            f"PSA 9: **{format_currency(psa9)}**" + (f" ({psa9_mult:.1f}x raw)" if psa9_mult else "") + "\n"
            f"PSA 10: **{format_currency(psa10)}**" + (f" ({psa10_mult:.1f}x raw)" if psa10_mult else "") + "\n"
            f"PSA 9 → PSA 10 jump: **{f'{p9p10_mult:.1f}x' if p9p10_mult else 'N/A'}**"
        ),
        inline=False,
    )

    # Grading score (0-100 scale)
    score_label = (
        "🟢 Excellent — strong candidate for high grade" if gs >= 70
        else "🟡 Average — grade outcome uncertain" if gs >= 40
        else "🔴 Low — higher risk of poor grade"
    )
    embed.add_field(name="📊 Grading Score", value=f"**{gs:.0f} / 100**\n{score_label}", inline=True)
    grade_display = "✅ **Yes**" if grade_it else ("❌ **No**" if grade_it is False else "⚠️ **Unclear**")
    embed.add_field(name="🎯 Should You Grade?", value=f"{grade_display}\n{grade_reason}", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=False)

    # Grader comparison — one field per grader to stay under Discord's 1024 char limit
    embed.add_field(name="🏢 Grader Comparison", value="Cheapest no-membership tier shown. Use `override_tier` for faster options.", inline=False)

    for gk, gd in GRADERS.items():
        tier_name = override_tier if (override_tier and override_tier in gd["tiers"]) else gd["default_tier"]
        cost, turnaround, max_val, _ = gd["tiers"][tier_name]
        max_str = f"max ${max_val:,}" if max_val else "no cap"
        rec_tag = " ⭐" if gk == rec_grader else ""

        if gk == "PSA":
            uplift = (psa9 - raw - cost) if (psa9 and raw) else None
            price_str = (
                f"PSA 9: **{format_currency(psa9)}** · PSA 10: **{format_currency(psa10)}**\n"
                f"Uplift (PSA 9 vs raw): **{format_currency(uplift)}**"
            )
        elif gk == "BGS":
            if any([bgs9, bgs95, bgs10]):
                best = bgs95 or bgs9 or bgs10
                best_label = "BGS 9.5" if bgs95 else ("BGS 10" if bgs10 else "BGS 9")
                uplift = (best - raw - cost) if (best and raw) else None
                price_str = (
                    f"BGS 9: **{format_currency(bgs9)}** · 9.5: **{format_currency(bgs95)}** · 10: **{format_currency(bgs10)}**\n"
                    f"Uplift ({best_label} vs raw): **{format_currency(uplift)}**"
                )
            else:
                uplift = (psa9 - raw - cost) if (psa9 and raw) else None
                price_str = f"_No BGS sales in DB — PSA proxy_\nEst. uplift: **{format_currency(uplift)}**"
        elif gk == "SGC":
            if any([sgc9, sgc95, sgc10]):
                best = sgc10 or sgc95 or sgc9
                best_label = "SGC 10" if sgc10 else ("SGC 9.5" if sgc95 else "SGC 9")
                uplift = (best - raw - cost) if (best and raw) else None
                price_str = (
                    f"SGC 9: **{format_currency(sgc9)}** · 9.5: **{format_currency(sgc95)}** · 10: **{format_currency(sgc10)}**\n"
                    f"Uplift ({best_label} vs raw): **{format_currency(uplift)}**"
                )
            else:
                uplift = (psa9 - raw - cost) if (psa9 and raw) else None
                price_str = f"_No SGC sales in DB — PSA proxy_\nEst. uplift: **{format_currency(uplift)}**"
        elif gk == "CGC":
            if any([cgc9, cgc95, cgc10, cgc10p]):
                best = cgc10p or cgc10 or cgc95 or cgc9
                best_label = "CGC 10 Pristine" if cgc10p else ("CGC 10" if cgc10 else ("CGC 9.5" if cgc95 else "CGC 9"))
                uplift = (best - raw - cost) if (best and raw) else None
                pristine_str = f" · Pristine: **{format_currency(cgc10p)}**" if cgc10p else ""
                price_str = (
                    f"CGC 9: **{format_currency(cgc9)}** · 9.5: **{format_currency(cgc95)}** · 10: **{format_currency(cgc10)}**{pristine_str}\n"
                    f"Uplift ({best_label} vs raw): **{format_currency(uplift)}**"
                )
            else:
                uplift = (psa9 - raw - cost) if (psa9 and raw) else None
                price_str = f"_No CGC sales in DB — PSA proxy_\nEst. uplift: **{format_currency(uplift)}**"

        field_value = (
            f"Cost: **${cost:.2f}** · {turnaround}\n"
            f"{price_str}"
        )
        embed.add_field(name=f"{gd['emoji']} {gk}{rec_tag}", value=field_value, inline=True)

    embed.add_field(
        name="💳 Membership Savings",
        value=(
            "**PSA:** Collectors Club $149/yr → ~$21.99/card bulk\n"
            "**BGS:** No membership required\n"
            "**SGC:** No membership required\n"
            "**CGC:** Free acct full price · $25+/yr → 10-20% off"
        ),
        inline=False,
    )

    if not override_tier:
        embed.add_field(name="💡 Tip", value="Re-run with `override_tier` set to e.g. `Express` or `Regular` to see costs for a faster tier.", inline=False)

    embed.set_footer(text="Prices from DB (30-day median sales). Grading costs as of early 2026 — verify on grader websites before submitting.")
    await interaction.followup.send(embed=embed)


# ===========================================================================
# Autocomplete handlers
# ===========================================================================

@grade.autocomplete("player")
async def player_autocomplete(interaction: discord.Interaction, current: str):
    if len(current) < 2:
        return []
    try:
        result = supabase.table("mv_grade_premiums") \
            .select("player_name, sport") \
            .ilike("player_name", f"%{current}%") \
            .limit(50).execute()
        seen = set()
        choices = []
        for row in result.data:
            name = row["player_name"]
            if name not in seen:
                seen.add(name)
                sport = row.get("sport", "")
                # Add sport tag to label so users can distinguish e.g. "Charizard (Pokemon)"
                label = f"{name} ({sport})" if sport and len(name) + len(sport) < 95 else name
                choices.append(app_commands.Choice(name=label, value=name))
            if len(choices) >= 25:
                break
        return choices
    except Exception as e:
        print(f"[ERROR] player_autocomplete: {e}")
        return []

@grade.autocomplete("set_name")
async def set_autocomplete(interaction: discord.Interaction, current: str):
    try:
        player_val = interaction.namespace.player

        query = supabase.table("mv_grade_premiums").select("set_name, set_year")

        # Filter by player if entered
        if player_val and len(player_val) >= 2:
            query = query.ilike("player_name", f"%{player_val}%")

        # Filter by what they're typing in the set field
        if current and len(current) >= 1:
            query = query.ilike("set_name", f"%{current}%")

        result = query.limit(100).execute()

        seen = set()
        choices = []
        for row in result.data:
            label = f"{row['set_name']} ({row['set_year']})"
            val   = row["set_name"]
            if val not in seen:
                seen.add(val)
                choices.append(app_commands.Choice(name=label, value=val))
            if len(choices) >= 25:
                break
        return choices
    except Exception as e:
        print(f"[ERROR] set_autocomplete: {e}")
        return []


# ===========================================================================
# Run
# ===========================================================================

client.run(TOKEN)
