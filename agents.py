from agents import Agent, WebSearchTool

SPECIALISTS = {}

def specialist(name: str, role: str):
    return Agent(
        name=name,
        instructions=role + """
Return concise, evidence-oriented findings. Separate facts from interpretation.
If evidence is insufficient, say so explicitly. Do not fabricate prices, dates, sources, or events.
""",
        tools=[WebSearchTool()],
    )

SPECIALISTS["Macro"] = specialist("ATHENA Macro", "Analyze rates, inflation, employment, GDP, liquidity, FX, commodities and central-bank regime.")
SPECIALISTS["News"] = specialist("ATHENA News", "Find recent material news, catalysts, information shocks and event risk.")
SPECIALISTS["Company"] = specialist("ATHENA Company", "Analyze company fundamentals, earnings, guidance, valuation context, catalysts and risks.")
SPECIALISTS["Market"] = specialist("ATHENA Market", "Analyze price structure, trend, momentum, volatility, breadth and market regime.")
SPECIALISTS["Sentiment"] = specialist("ATHENA Sentiment", "Analyze investor narrative, positioning, expectations and sentiment shifts.")
SPECIALISTS["Risk"] = specialist("ATHENA Risk", "Act as adversarial reviewer. Try to falsify the emerging thesis and identify invalidation conditions.")
SPECIALISTS["Learning"] = specialist("ATHENA Learning", "Analyze historical ATHENA predictions, outcomes and recurring methodological patterns. Never claim learning without evaluated evidence.")

# Agents-as-tools: the manager dynamically chooses specialists.
SPECIALIST_TOOLS = [
    a.as_tool(
        tool_name=f"call_{name.lower()}",
        tool_description=f"Ask the {name} specialist to investigate the question using web research."
    )
    for name, a in SPECIALISTS.items()
]
