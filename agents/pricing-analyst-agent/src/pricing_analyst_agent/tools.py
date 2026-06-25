"""
Tool implementations and mock data for the Pricing Analyst Agent.

Provides COGS (Coupa) and deal history data, plus market trends and plotting.
All tool functions are registered with @app.tool in main.py.
All data is in-memory dictionaries for demo use.
"""

import base64
import io
import json
from typing import Any, Literal, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

# -----------------------------------------------------------------------------
# COUPA_COSTS: current list price, COGS, breakdown (materials/shipping/labor),
# prior-year breakdown, YoY shipping change. Generic Analgesic includes
# alternate_suppliers for margin levers. Values calibrated to demo table math.
# -----------------------------------------------------------------------------

COUPA_COSTS: dict[str, dict[str, Any]] = {
    "Generic Analgesic": {
        "current_list_price": 14.20,
        "cogs": 11.97,
        "breakdown": {
            "materials": 7.80,
            "shipping": 1.47,
            "labor": 2.70,
        },
        "prior_year": {
            "materials": 7.65,
            "shipping": 1.42,
            "labor": 2.68,
        },
        "yoy_shipping_change_pct": 3.5,
        "alternate_suppliers": [
            {"name": "GenericRx Wholesale", "potential_cogs_savings_per_unit": 0.18},
            {"name": "Value Pharma Direct", "potential_cogs_savings_per_unit": 0.12},
        ],
        "shipping_logistics": {
            "current_frequency": "bi-weekly",
            "consolidation_option": "monthly",
            "estimated_shipping_savings_pct": 15,
            "note": "Consolidating from bi-weekly to monthly shipments reduces per-unit shipping from $1.47 to ~$1.25",
        },
        "gpo_discounts": {
            "HealthTrust": {
                "members": ["Mercy Health"],
                "materials_discount_per_unit": 0.40,
                "adjusted_materials_cost": 7.40,
                "note": "HealthTrust GPO members receive pre-negotiated materials pricing on Generic Analgesic",
            }
        },
    },
    "Specialty Oncology": {
        "current_list_price": 342.00,
        "cogs": 302.55,
        "breakdown": {
            "materials": 245.00,
            "shipping": 28.55,
            "labor": 29.00,
        },
        "prior_year": {
            "materials": 242.00,
            "shipping": 26.93,
            "labor": 28.50,
        },
        "yoy_shipping_change_pct": 6.0,
        "alternate_suppliers": [],
        "shipping_logistics": {
            "current_frequency": "bi-weekly",
            "consolidation_option": "monthly",
            "estimated_shipping_savings_pct": 18,
            "note": "Consolidating from bi-weekly to monthly shipments reduces per-unit shipping from $28.55 to ~$23.41",
        },
    },
    "OTC Respiratory": {
        "current_list_price": 8.50,
        "cogs": 7.50,
        "breakdown": {
            "materials": 4.80,
            "shipping": 0.95,
            "labor": 1.75,
        },
        "prior_year": {
            "materials": 4.75,
            "shipping": 0.92,
            "labor": 1.72,
        },
        "yoy_shipping_change_pct": 3.3,
        "alternate_suppliers": [],
        "shipping_logistics": {
            "current_frequency": "bi-weekly",
            "consolidation_option": "monthly",
            "estimated_shipping_savings_pct": 12,
            "note": "Consolidating from bi-weekly to monthly shipments reduces per-unit shipping from $0.95 to ~$0.84",
        },
    },
}

# -----------------------------------------------------------------------------
# DEAL_HISTORY: past deals by customer. Each deal has per-category line items
# (list_price, discount_pct, negotiated_price, margin_pct) for tool fallback.
# -----------------------------------------------------------------------------

DEAL_HISTORY: dict[str, list[dict[str, Any]]] = {
    "Mercy Health": [],
    "MedPoint Pharmacies": [
        {
            "deal_id": "DEAL-2024-0012",
            "signed_quarter": "2024 Q1",
            "contract_duration_years": 1.5,
            "categories": [
                {
                    "category": "Generic Analgesic",
                    "list_price": 13.80,
                    "discount_pct": 10,
                    "negotiated_price": 12.42,
                    "margin_pct": 5.1,
                },
                {
                    "category": "Specialty Oncology",
                    "list_price": 338.00,
                    "discount_pct": 2.5,
                    "negotiated_price": 329.55,
                    "margin_pct": 8.2,
                },
                {
                    "category": "OTC Respiratory",
                    "list_price": 8.20,
                    "discount_pct": 7,
                    "negotiated_price": 7.63,
                    "margin_pct": 4.2,
                },
            ],
        },
        {
            "deal_id": "DEAL-2024-0028",
            "signed_quarter": "2024 Q3",
            "contract_duration_years": 1,
            "notes": "MedPoint pushed hard on generic analgesic discounts; final 12% discount. Held firm on specialty oncology at 8.2%.",
            "categories": [
                {
                    "category": "Generic Analgesic",
                    "list_price": 14.00,
                    "discount_pct": 12,
                    "negotiated_price": 12.32,
                    "margin_pct": 5.1,
                },
                {
                    "category": "Specialty Oncology",
                    "list_price": 340.00,
                    "discount_pct": 3,
                    "negotiated_price": 329.80,
                    "margin_pct": 8.2,
                },
                {
                    "category": "OTC Respiratory",
                    "list_price": 8.40,
                    "discount_pct": 8.5,
                    "negotiated_price": 7.69,
                    "margin_pct": 4.2,
                },
            ],
        },
    ],
}


# NOTE: Should realistically be a code executor tool.
def calculate_pricing(categories_json: str) -> str:
    """Compute net price, total COGS, margin dollars, and margin percentage for one or more product categories.

    Use this tool for ALL margin and pricing arithmetic. Pass the cost
    components obtained from lookup_coupa_costs (or adjusted values such as
    consolidated shipping costs) and this tool will return exact figures.

    Args:
        categories_json: JSON array where each object has:
            category (str) — category name,
            list_price (float) — unit list price,
            discount_pct (float) — discount percentage (e.g. 12 for 12%),
            materials_cost (float) — per-unit materials cost,
            shipping_cost (float) — per-unit shipping cost,
            labor_cost (float) — per-unit labor cost.
    """
    try:
        categories = json.loads(categories_json)
    except json.JSONDecodeError as e:
        return json.dumps(
            {"status": "error", "message": f"Invalid categories_json: {e!s}"}, indent=2
        )

    if not isinstance(categories, list):
        categories = [categories]

    results = []
    for cat in categories:
        list_price = float(cat.get("list_price", 0))
        discount_pct = float(cat.get("discount_pct", 0))
        materials = float(cat.get("materials_cost", 0))
        shipping = float(cat.get("shipping_cost", 0))
        labor = float(cat.get("labor_cost", 0))

        net_price = round(list_price * (1 - discount_pct / 100), 2)
        total_cogs = round(materials + shipping + labor, 2)
        margin_dollars = round(net_price - total_cogs, 2)
        margin_pct = round(margin_dollars / net_price * 100, 2) if net_price else 0.0

        results.append(
            {
                "category": cat.get("category", "Unknown"),
                "list_price": list_price,
                "discount_pct": discount_pct,
                "net_price": net_price,
                "total_cogs": total_cogs,
                "margin_dollars": margin_dollars,
                "margin_pct": margin_pct,
            }
        )

    return json.dumps({"status": "ok", "results": results}, indent=2)


def _normalize_category(name: str) -> str | None:
    """Map common variations to COUPA_COSTS keys."""
    name_lower = name.strip().lower()
    if "generic" in name_lower and "analgesic" in name_lower:
        return "Generic Analgesic"
    if "specialty" in name_lower and "onco" in name_lower:
        return "Specialty Oncology"
    if "otc" in name_lower and "respiratory" in name_lower:
        return "OTC Respiratory"
    for key in COUPA_COSTS:
        if key.lower() == name_lower:
            return key
    return None


def lookup_coupa_costs(category: str) -> str:
    """Look up current COGS and cost breakdown for a product category from Coupa.

    Returns list price, COGS, materials/shipping/labor breakdown, prior-year
    comparison, YoY shipping change, and (for Generic Analgesic) alternate
    supplier options for margin levers.

    Args:
        category: Product category (e.g. "Generic Analgesic", "Specialty Oncology", "OTC Respiratory")
    """
    key = _normalize_category(category)
    if key is None:
        return json.dumps(
            {
                "status": "not_found",
                "message": f"No COGS data for category '{category}'. Available: {list(COUPA_COSTS.keys())}",
            },
            indent=2,
        )
    row = COUPA_COSTS[key].copy()
    if not row.get("alternate_suppliers"):
        row.pop("alternate_suppliers", None)
    row["data_source"] = f"Coupa Procurement System — {key} Cost Breakdown"
    return json.dumps(row, indent=2)


def search_deal_history(customer_name: str) -> str:
    """Search past deals for a customer.

    Returns deal IDs, contract terms, and per-category pricing (list, discount,
    negotiated price, margin) for use in renewal drafting and vs. Last comparisons.

    Args:
        customer_name: Customer or account name (e.g. "MedPoint Pharmacies")
    """
    customer_stripped = customer_name.strip()
    for key, deals in DEAL_HISTORY.items():
        if key.lower() == customer_stripped.lower():
            return json.dumps(
                {
                    "customer": key,
                    "deals": deals,
                    "data_source": f"Deal Management System — {key} History",
                },
                indent=2,
            )
    return json.dumps(
        {
            "customer": customer_stripped,
            "deals": [],
            "message": f"No deal history found for '{customer_stripped}'. Available: {list(DEAL_HISTORY.keys())}",
        },
        indent=2,
    )


# NOTE: Realistically this should be a code executor tool, but for now we'll use this as
# a placeholder for demo.
def plot_data(
    data_json: str,
    chart_type: Literal["line", "bar"] = "line",
    title: Optional[str] = None,
    x_label: Optional[str] = None,
    y_label: Optional[str] = None,
) -> str:
    """Plot or visualize data as a line or bar chart. The chart is rendered as an image in the UI.

    IMPORTANT: Always call this tool to generate charts. Never write chart parameters,
    data arrays, or plot specifications as inline text in your response.

    Use this when the user asks to plot, graph, visualize, or chart data
    (e.g. "can you plot this", "show a graph", "visualize the data").
    Pass the data as a JSON string with "x" and "y" arrays, e.g. from
    lookup_coupa_costs or search_deal_history results, or any structured data.

    Args:
        data_json: JSON string with data to plot, e.g. {"x": [1,2,3], "y": [10,20,15]}
                  or {"labels": ["A","B","C"], "values": [10,20,15]} for bar charts.
        chart_type: "line" or "bar". Default "line".
        title: Optional chart title.
        x_label: Optional x-axis label.
        y_label: Optional y-axis label.
    """
    try:
        data = json.loads(data_json)
    except json.JSONDecodeError as e:
        return json.dumps(
            {"status": "error", "message": f"Invalid data_json: {e!s}"},
            indent=2,
        )

    # Support both {"x": [...], "y": [...]} and {"labels": [...], "values": [...]}
    x = data.get("x") or data.get("labels")
    y = data.get("y") or data.get("values")
    if not x or not y:
        return json.dumps(
            {
                "status": "error",
                "message": "data_json must contain 'x' and 'y' arrays, or 'labels' and 'values'.",
            },
            indent=2,
        )
    if len(x) != len(y):
        return json.dumps(
            {
                "status": "error",
                "message": "x and y (or labels and values) must have the same length.",
            },
            indent=2,
        )

    # Use numeric y for plotting (in case of string categories on x)
    y_num = [float(v) for v in y]
    y_max = max(y_num)

    # Format y-axis in millions when values are large to avoid "1e6" scientific notation
    use_millions = y_max >= 100_000
    if use_millions:
        y_plot = [v / 1e6 for v in y_num]
        y_max = max(y_plot)
    else:
        y_plot = y_num

    fig, ax = plt.subplots(figsize=(8, 5))
    if chart_type == "line":
        x_plot = range(len(x)) if not all(isinstance(v, (int, float)) for v in x) else x
        ax.plot(x_plot, y_plot, marker="o")
    else:
        ax.bar(range(len(x)), y_plot, tick_label=x)
        # Rotate x-axis labels so category names don't overlap
        ax.tick_params(axis="x", labelsize=9)
        plt.setp(ax.get_xticklabels(), rotation=35, ha="right", rotation_mode="anchor")
    if title:
        ax.set_title(title, fontsize=12)
    if x_label:
        ax.set_xlabel(x_label)
    if y_label:
        effective_y_label = y_label
        if use_millions and "USD" in y_label.upper():
            effective_y_label = "Revenue (Millions USD)"
        elif use_millions:
            effective_y_label = f"{y_label} (Millions)"
        ax.set_ylabel(effective_y_label)
    if use_millions:
        # Show clean tick values (e.g. 0.5, 1.0, 1.5) instead of scientific notation
        ax.yaxis.set_major_formatter(FuncFormatter(lambda val, _: f"{val:.1f}"))
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100)
    plt.close(fig)
    buf.seek(0)
    image_base64 = base64.b64encode(buf.read()).decode("utf-8")

    return json.dumps(
        {
            "status": "ok",
            "image_base64": image_base64,
            "message": "Plot created successfully. The chart will be displayed automatically in the UI. Do NOT include the image in your markdown response using ![...](...) syntax.",
        },
        indent=2,
    )
