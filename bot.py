import os
import discord
from discord import app_commands
from supabase import create_client, Client
from dotenv import load_dotenv
from collections import Counter

load_dotenv()
TOKEN        = os.getenv("DISCORD_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ===========================================================================
# HELPERS
# ===========================================================================

def format_currency(amount) -> str:
    if amount is None:
        return "N/A"
    amount = float(amount)
    return f"${amount:,.2f}" if amount >= 0 else f"-${abs(amount):,.2f}"

def fv(val):
    return float(val) if val is not None else None

# ===========================================================================
# SELL DATA
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

def get_tier(sale_price):
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
# GRADE DATA
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
        "emoji": "🟦",
    },
    "BGS": {
        "default_tier": "Base", "default_cost": 14.95,
        "tiers": {
            "Base":     (14.95,  "~75 days", None, "Sub-grades included free."),
            "Standard": (34.95,  "~45 days", None, "Best balance of cost and speed"),
            "Express":  (79.95,  "~15 days", None, "Fast turnaround"),
            "Priority": (124.95, "~5 days",  None, "Fastest BGS service"),
        },
        "emoji": "⚫",
    },
    "SGC": {
        "default_tier": "Standard", "default_cost": 15.00,
        "tiers": {
            "Standard":  (15.00, "~15-20 business days", 1500, "Best value for speed."),
            "Immediate": (40.00, "~1-2 business days",   1500, "Fastest turnaround in the industry"),
        },
        "emoji": "🟤",
    },
    "CGC": {
        "default_tier": "Economy", "default_cost": 17.00,
        "tiers": {
            "Bulk":        (14.00,  "~80 days", 500,   "25-card minimum required"),
            "Economy":     (17.00,  "~40 days", 1000,  "No minimum. Best single-card budget option."),
            "Express":     (50.00,  "~10 days", 3000,  "Fast and mid-range"),
            "WalkThrough": (150.00, "~2 days",  10000, "Fastest CGC service"),
        },
        "emoji": "🟡",
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
    warning = f"\n⚠️ PSA 9 is {psa9_mult:.1f}x raw — historically difficult to grade. High risk of low grade." if hard_to_grade else ""
    if psa10_mult_actual >= 2.5:
        return True,  f"PSA 10 ({format_currency(psa10)}) is {psa10_mult_actual:.1f}x your total cost ({format_currency(total_cost)}). Strong grading candidate.{warning}", hard_to_grade
    if uplift < 0:
        return False, f"PSA 9 nets you {format_currency(uplift)} after grading cost. Sell raw.{warning}", hard_to_grade
    if uplift < 30:
        if grading_score >= 50:
            return True,  f"Marginal uplift ({format_currency(uplift)}) but grading score {grading_score:.0f}/100 suggests card grades well. Proceed if condition is strong.{warning}", hard_to_grade
        else:
            return False, f"Upside of only {format_currency(uplift)} and grading score {grading_score:.0f}/100 is below average. Sell raw.{warning}", hard_to_grade
    return True, f"PSA 9 uplift of {format_currency(uplift)} over raw justifies the ${grading_cost:.2f} grading cost.{warning}", hard_to_grade

# ===========================================================================
# BOT SETUP
# ===========================================================================

intents = discord.Intents.default()
client  = discord.Client(intents=intents)
tree    = app_commands.CommandTree(client)

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
    await interaction.response.defer(ephemeral=True)
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
    if grading_cost > 0:   parts.append(f"**Grading:** ${grading_cost:,.2f}")
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
# /grade
# ===========================================================================

GRADE_SELECT = (
    "card_id, player_name, card_number, set_name, variation, insert_set, "
    "canonical_name, is_rookie, sport, "
    "raw_price, raw_sale_count_30d, "
    "psa9_price, psa10_price, grading_score, "
    "raw_to_psa9_mult, raw_to_psa10_mult, psa9_to_psa10_mult, "
    "bgs9_price, bgs95_price, bgs10_price, "
    "sgc9_price, sgc95_price, sgc10_price, "
    "cgc9_price, cgc95_price, cgc10_price, cgc10_pristine_price"
)


def lookup_cards(player: str, set_name: str, variation: str, insert_set: str, card_number: str):
    """
    Single-pass lookup against mv_grade_premiums.
    Pass 1: player_name match (strict on variation/insert/card_number if given).
    Pass 2: relax variation/insert filters.
    Pass 3: canonical_name fallback.
    Returns list of matching rows.
    """
    def base_query(player_field: str, player_val: str):
        return (
            supabase.table("mv_grade_premiums")
            .select(GRADE_SELECT)
            .ilike(player_field, f"%{player_val}%")
            .ilike("set_name", f"%{set_name}%")
        )

    # Pass 1
    q = base_query("player_name", player)
    if variation:   q = q.ilike("variation",  f"%{variation.strip()}%")
    else:           q = q.is_("variation",    "null")
    if insert_set:  q = q.ilike("insert_set", f"%{insert_set}%")
    else:           q = q.is_("insert_set",   "null")
    if card_number: q = q.ilike("card_number", f"%{card_number}%")
    result = q.order("raw_sale_count_30d", desc=True).limit(5).execute()
    if result.data:
        return result.data

    # Pass 2 — relax variation/insert
    q = base_query("player_name", player)
    if variation:   q = q.ilike("variation",  f"%{variation.strip()}%")
    if insert_set:  q = q.ilike("insert_set", f"%{insert_set}%")
    if card_number: q = q.ilike("card_number", f"%{card_number}%")
    result = q.order("raw_sale_count_30d", desc=True).limit(5).execute()
    if result.data:
        return result.data

    # Pass 3 — canonical_name
    q = base_query("canonical_name", player)
    if card_number: q = q.ilike("card_number", f"%{card_number}%")
    result = q.order("raw_sale_count_30d", desc=True).limit(5).execute()
    return result.data or []


@tree.command(name="grade", description="Look up a card and get a grading company comparison + recommendation")
@app_commands.describe(
    player="Player or character name",
    set_name="Set name",
    card_number="Card number — required when multiple cards share the same variation (e.g. GG45, 114)",
    variation="Optional: parallel/variation — leave blank for base",
    insert_set="Optional: insert set name — leave blank for non-inserts",
    is_vintage="Is this a vintage card (pre-1980)?",
    override_tier="Optional: faster tier (e.g. Express, Regular) for paid members",
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
    variation: str = None,
    insert_set: str = None,
    is_vintage: int = 0,
    override_tier: str = None,
):
    await interaction.response.defer(ephemeral=True)

    try:
        rows = lookup_cards(player, set_name, variation, insert_set, card_number)
    except Exception as e:
        await interaction.followup.send(f"[ERROR] Database query failed: {e}")
        return

    if not rows:
        await interaction.followup.send(
            f"No card found for **{player}** in **{set_name}**.\n"
            "Try a partial name — partial matches work. For cards like 'Gengar EX', "
            "try just 'Gengar' and use card_number to narrow it down."
        )
        return

    # -----------------------------------------------------------------------
    # DISAMBIGUATION — require card_number when multiple match
    # -----------------------------------------------------------------------
    if len(rows) > 1:
        lines = []
        for c in rows:
            num       = c.get("card_number", "?")
            var       = c.get("variation") or "Base"
            ins       = f" · {c['insert_set']}" if c.get("insert_set") else ""
            raw       = fv(c.get("raw_price"))
            psa10     = fv(c.get("psa10_price"))
            price_str = f"Raw {format_currency(raw)}" if raw else "No raw price"
            psa10_str = f" · PSA 10 {format_currency(psa10)}" if psa10 else ""
            canon     = c.get("canonical_name", "")
            lines.append(f"• **#{num}** {var}{ins}  {price_str}{psa10_str}\n  _{canon}_")

        embed = discord.Embed(
            title="🔎 Multiple matches — add a card number",
            description=(
                f"Found **{len(rows)} cards** matching **{player}** in **{set_name}**.\n"
                "Re-run `/grade` and fill in the **card_number** field:\n\n"
                + "\n".join(lines)
            ),
            color=0x5865F2,
        )
        embed.set_footer(text="Copy the # from the list above into the card_number field.")
        await interaction.followup.send(embed=embed)
        return

    # -----------------------------------------------------------------------
    # SINGLE MATCH — full analysis
    # -----------------------------------------------------------------------
    card = rows[0]
    raw    = fv(card.get("raw_price"))
    psa9   = fv(card.get("psa9_price"))
    psa10  = fv(card.get("psa10_price"))
    gs     = fv(card.get("grading_score")) or 0.0
    vintage = bool(is_vintage)
    bgs9   = fv(card.get("bgs9_price"))
    bgs95  = fv(card.get("bgs95_price"))
    bgs10  = fv(card.get("bgs10_price"))
    sgc9   = fv(card.get("sgc9_price"))
    sgc95  = fv(card.get("sgc95_price"))
    sgc10  = fv(card.get("sgc10_price"))
    cgc9   = fv(card.get("cgc9_price"))
    cgc95  = fv(card.get("cgc95_price"))
    cgc10  = fv(card.get("cgc10_price"))
    cgc10p = fv(card.get("cgc10_pristine_price"))
    psa9_mult  = fv(card.get("raw_to_psa9_mult"))
    psa10_mult = fv(card.get("raw_to_psa10_mult"))
    p9p10_mult = fv(card.get("psa9_to_psa10_mult"))

    rec_grader = get_grader_rec(raw, psa9, psa10, gs, vintage)
    grading_cost_default = GRADERS["PSA"]["default_cost"]
    grade_it, grade_reason, hard_to_grade = should_grade(raw, psa9, psa10, grading_cost_default, gs, psa9_mult)

    color = 0x57F287 if grade_it else (0xED4245 if grade_it is False else 0x5865F2)
    embed = discord.Embed(
        title=f"🔎 Grade Analysis — {card['player_name']}",
        description=(
            f"{card['set_name']} #{card.get('card_number', '?')}"
            + (f" · {card['variation']}" if card.get("variation") else "")
            + (f" · 📋 {card['insert_set']}" if card.get("insert_set") else "")
            + (" · 🌟 Rookie" if card.get("is_rookie") else "")
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

    score_label = (
        "🟢 Excellent — strong candidate for high grade" if gs >= 70
        else "🟡 Average — grade outcome uncertain" if gs >= 40
        else "🔴 Low — higher risk of poor grade"
    )
    embed.add_field(name="📊 Grading Score",    value=f"**{gs:.0f} / 100**\n{score_label}", inline=True)
    grade_display = "✅ **Yes**" if grade_it else ("❌ **No**" if grade_it is False else "⚠️ **Unclear**")
    embed.add_field(name="🎯 Should You Grade?", value=f"{grade_display}\n{grade_reason}", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=False)
    embed.add_field(name="🏢 Grader Comparison", value="Cheapest no-membership tier shown. Use `override_tier` for faster options.", inline=False)

    for gk, gd in GRADERS.items():
        tier_name = override_tier if (override_tier and override_tier in gd["tiers"]) else gd["default_tier"]
        cost, turnaround, _, _ = gd["tiers"][tier_name]
        rec_tag = " ⭐" if gk == rec_grader else ""

        if gk == "PSA":
            uplift = (psa9 - raw - cost) if (psa9 and raw) else None
            price_str = (f"PSA 9: **{format_currency(psa9)}** · PSA 10: **{format_currency(psa10)}**\n"
                         f"Uplift (PSA 9 vs raw): **{format_currency(uplift)}**")
        elif gk == "BGS":
            if any([bgs9, bgs95, bgs10]):
                best = bgs95 or bgs9 or bgs10
                best_label = "BGS 9.5" if bgs95 else ("BGS 10" if bgs10 else "BGS 9")
                uplift = (best - raw - cost) if (best and raw) else None
                price_str = (f"BGS 9: **{format_currency(bgs9)}** · 9.5: **{format_currency(bgs95)}** · 10: **{format_currency(bgs10)}**\n"
                             f"Uplift ({best_label} vs raw): **{format_currency(uplift)}**")
            else:
                uplift = (psa9 - raw - cost) if (psa9 and raw) else None
                price_str = f"_No BGS sales in DB — PSA proxy_\nEst. uplift: **{format_currency(uplift)}**"
        elif gk == "SGC":
            if any([sgc9, sgc95, sgc10]):
                best = sgc10 or sgc95 or sgc9
                best_label = "SGC 10" if sgc10 else ("SGC 9.5" if sgc95 else "SGC 9")
                uplift = (best - raw - cost) if (best and raw) else None
                price_str = (f"SGC 9: **{format_currency(sgc9)}** · 9.5: **{format_currency(sgc95)}** · 10: **{format_currency(sgc10)}**\n"
                             f"Uplift ({best_label} vs raw): **{format_currency(uplift)}**")
            else:
                uplift = (psa9 - raw - cost) if (psa9 and raw) else None
                price_str = f"_No SGC sales in DB — PSA proxy_\nEst. uplift: **{format_currency(uplift)}**"
        elif gk == "CGC":
            if any([cgc9, cgc95, cgc10, cgc10p]):
                best = cgc10p or cgc10 or cgc95 or cgc9
                best_label = "CGC 10 Pristine" if cgc10p else ("CGC 10" if cgc10 else ("CGC 9.5" if cgc95 else "CGC 9"))
                uplift = (best - raw - cost) if (best and raw) else None
                pristine_str = f" · Pristine: **{format_currency(cgc10p)}**" if cgc10p else ""
                price_str = (f"CGC 9: **{format_currency(cgc9)}** · 9.5: **{format_currency(cgc95)}** · 10: **{format_currency(cgc10)}**{pristine_str}\n"
                             f"Uplift ({best_label} vs raw): **{format_currency(uplift)}**")
            else:
                uplift = (psa9 - raw - cost) if (psa9 and raw) else None
                price_str = f"_No CGC sales in DB — PSA proxy_\nEst. uplift: **{format_currency(uplift)}**"

        embed.add_field(
            name=f"{gd['emoji']} {gk}{rec_tag}",
            value=f"Cost: **${cost:.2f}** · {turnaround}\n{price_str}",
            inline=True,
        )

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
        embed.add_field(name="💡 Tip", value="Re-run with `override_tier` (e.g. `Express`) to see costs for faster tiers.", inline=False)

    embed.set_footer(text="Prices from DB (30-day median sales). Grading costs as of early 2026 — verify on grader websites before submitting.")
    await interaction.followup.send(embed=embed)


# ===========================================================================
# AUTOCOMPLETE — all against mv_grade_premiums with trigram indexes
# ===========================================================================

@grade.autocomplete("player")
async def player_autocomplete(interaction: discord.Interaction, current: str):
    if len(current) < 2:
        return []
    try:
        cur = current.lower()
        # Two separate queries so each can use its own trigram index
        # OR on two GIN indexes forces a seq scan — UNION does not
        r1 = (
            supabase.table("mv_grade_premiums")
            .select("player_name, sport, canonical_name, raw_sale_count_30d")
            .ilike("player_name", f"%{current}%")
            .order("raw_sale_count_30d", desc=True)
            .limit(50)
            .execute()
        )
        r2 = (
            supabase.table("mv_grade_premiums")
            .select("player_name, sport, canonical_name, raw_sale_count_30d")
            .ilike("canonical_name", f"%{current}%")
            .order("raw_sale_count_30d", desc=True)
            .limit(50)
            .execute()
        )

        seen = {}
        for row in (r1.data or []) + (r2.data or []):
            name = row["player_name"]
            if name in seen:
                continue
            from_canonical = (
                cur not in name.lower() and
                cur in (row.get("canonical_name") or "").lower()
            )
            seen[name] = {"sport": row.get("sport", ""), "from_canonical": from_canonical,
                          "count": row.get("raw_sale_count_30d") or 0}

        sorted_names = sorted(seen, key=lambda n: (seen[n]["from_canonical"], -(seen[n]["count"])))
        choices = []
        for name in sorted_names:
            info = seen[name]
            tag  = " ~" if info["from_canonical"] else ""
            label = f"{name} ({info['sport']}){tag}"[:100]
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
        player_val = interaction.namespace.player or ""

        def set_query(player_field):
            q = supabase.table("mv_grade_premiums").select("set_name, raw_price, raw_sale_count_30d")
            if player_val and len(player_val) >= 2:
                q = q.ilike(player_field, f"%{player_val}%")
            if current:
                q = q.ilike("set_name", f"%{current}%")
            return q.order("raw_sale_count_30d", desc=True).limit(100).execute()

        r1 = set_query("player_name")
        r2 = set_query("canonical_name")
        all_rows = list(r1.data or [])
        seen_sets = {r["set_name"] for r in all_rows}
        for r in (r2.data or []):
            if r["set_name"] not in seen_sets:
                all_rows.append(r)
                seen_sets.add(r["set_name"])

        set_counts = Counter(r["set_name"] for r in all_rows)
        set_price  = {}
        for r in all_rows:
            s = r["set_name"]
            if s not in set_price:
                set_price[s] = r.get("raw_price")

        seen = set()
        choices = []
        for r in all_rows:
            val = r["set_name"]
            if val in seen:
                continue
            seen.add(val)
            raw = set_price[val]
            price_str = f" — ${float(raw):.0f} raw" if (raw and set_counts[val] == 1) else ""
            choices.append(app_commands.Choice(name=f"{val}{price_str}"[:100], value=val))
            if len(choices) >= 25:
                break
        return choices
    except Exception as e:
        print(f"[ERROR] set_autocomplete: {e}")
        return []


@grade.autocomplete("variation")
async def variation_autocomplete(interaction: discord.Interaction, current: str):
    try:
        player_val     = interaction.namespace.player or ""
        set_val        = interaction.namespace.set_name or ""
        card_number_val = interaction.namespace.card_number or ""

        query = (
            supabase.table("mv_grade_premiums")
            .select("variation, raw_price, psa10_price, raw_sale_count_30d")
            .not_.is_("variation", "null")
        )
        if player_val:      query = query.ilike("player_name", f"%{player_val}%")
        if set_val:         query = query.ilike("set_name",    f"%{set_val}%")
        if card_number_val: query = query.ilike("card_number", f"%{card_number_val}%")
        if current:         query = query.ilike("variation",   f"%{current}%")
        result = query.order("raw_sale_count_30d", desc=True).limit(100).execute()

        seen = set()
        base_choices  = []
        other_choices = []
        for row in (result.data or []):
            val = (row["variation"] or "").strip()
            if not val or val in seen:
                continue
            seen.add(val)
            raw   = row.get("raw_price")
            psa10 = row.get("psa10_price")
            price_str = f" — ${float(raw):.0f} raw" if raw else ""
            psa10_str = f" · ${float(psa10):.0f} PSA 10" if psa10 else ""
            label  = f"{val}{price_str}{psa10_str}"[:100]
            choice = app_commands.Choice(name=label, value=val)
            if val.lower() == "base":
                base_choices.append(choice)
            else:
                other_choices.append(choice)

        return (base_choices + other_choices)[:25]
    except Exception as e:
        print(f"[ERROR] variation_autocomplete: {e}")
        return []


@grade.autocomplete("insert_set")
async def insert_set_autocomplete(interaction: discord.Interaction, current: str):
    try:
        player_val = interaction.namespace.player or ""
        set_val    = interaction.namespace.set_name or ""
        query = (
            supabase.table("mv_grade_premiums")
            .select("insert_set")
            .not_.is_("insert_set", "null")
        )
        if player_val: query = query.ilike("player_name", f"%{player_val}%")
        if set_val:    query = query.ilike("set_name",    f"%{set_val}%")
        if current:    query = query.ilike("insert_set",  f"%{current}%")
        result = query.order("raw_sale_count_30d", desc=True).limit(100).execute()

        seen = set()
        choices = []
        for row in (result.data or []):
            val = (row["insert_set"] or "").strip()
            if not val or val in seen:
                continue
            seen.add(val)
            choices.append(app_commands.Choice(name=val, value=val))
            if len(choices) >= 25:
                break
        return choices
    except Exception as e:
        print(f"[ERROR] insert_set_autocomplete: {e}")
        return []


# ===========================================================================
# RUN
# ===========================================================================

client.run(TOKEN)
