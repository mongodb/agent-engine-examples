from __future__ import annotations

import json

from pricing_analyst_agent.tools import (
    calculate_pricing,
    lookup_coupa_costs,
    plot_data,
    search_deal_history,
)


def test_lookup_coupa_costs_returns_cost_breakdown() -> None:
    payload = json.loads(lookup_coupa_costs("Generic Analgesic"))

    assert payload["current_list_price"] == 14.20
    assert payload["breakdown"]["shipping"] == 1.47
    assert payload["gpo_discounts"]["HealthTrust"]["members"] == ["Mercy Health"]


def test_search_deal_history_returns_medpoint_deals() -> None:
    payload = json.loads(search_deal_history("MedPoint Pharmacies"))

    assert payload["customer"] == "MedPoint Pharmacies"
    assert len(payload["deals"]) == 2
    assert payload["deals"][0]["categories"][0]["category"] == "Generic Analgesic"


def test_calculate_pricing_returns_margin_math() -> None:
    payload = json.loads(
        calculate_pricing(
            json.dumps(
                [
                    {
                        "category": "Generic Analgesic",
                        "list_price": 14.20,
                        "discount_pct": 10,
                        "materials_cost": 7.80,
                        "shipping_cost": 1.47,
                        "labor_cost": 2.70,
                    }
                ]
            )
        )
    )

    result = payload["results"][0]
    assert payload["status"] == "ok"
    assert result["net_price"] == 12.78
    assert result["total_cogs"] == 11.97
    assert result["margin_dollars"] == 0.81
    assert result["margin_pct"] == 6.34


def test_plot_data_returns_base64_png() -> None:
    payload = json.loads(
        plot_data(
            json.dumps({"labels": ["A", "B", "C"], "values": [1, 2, 3]}),
            chart_type="bar",
            title="Revenue",
            x_label="Category",
            y_label="USD",
        )
    )

    assert payload["status"] == "ok"
    assert payload["image_base64"]
