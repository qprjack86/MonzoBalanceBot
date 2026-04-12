from __future__ import annotations

import json
from dataclasses import dataclass

import requests

from core.finance_settings import FinanceSettings


@dataclass(frozen=True)
class AdviceInput:
    weekly_spend_pence: int
    weekly_target_pence: int
    overspend_categories: list[str]
    natwest_balance_pence: int
    months_remaining: int
    target_months: int
    monthly_payment_on_track: bool
    emergency_fund_pence: int
    emergency_fund_target_pence: int
    pot_balance_pence: int
    previous_advice_summary: str


def _pence_to_gbp(amount_pence: int) -> str:
    return f"{amount_pence / 100:.2f}"


def build_prompt(payload: AdviceInput) -> str:
    overspend = ", ".join(payload.overspend_categories) if payload.overspend_categories else "none"
    on_track = "yes" if payload.monthly_payment_on_track else "no"
    return (
        "Weekly financial summary:\n"
        f"- Weekly discretionary spend: GBP {_pence_to_gbp(payload.weekly_spend_pence)} vs GBP {_pence_to_gbp(payload.weekly_target_pence)} target\n"
        f"- Overspend categories: {overspend}\n"
        f"- NatWest balance: GBP {_pence_to_gbp(payload.natwest_balance_pence)}, months remaining: {payload.months_remaining} of {payload.target_months}, monthly payment on track: {on_track}\n"
        f"- Emergency fund: GBP {_pence_to_gbp(payload.emergency_fund_pence)} of GBP {_pence_to_gbp(payload.emergency_fund_target_pence)} target\n"
        f"- Monzo pot balance: GBP {_pence_to_gbp(payload.pot_balance_pence)}\n"
        f"- Last weeks advice was followed: {payload.previous_advice_summary}\n"
        "Provide brief, specific, actionable advice for the coming week in plain English. Keep it under 150 words."
    )


def generate_weekly_advice(settings: FinanceSettings, payload: AdviceInput, timeout: tuple[float, float]) -> str:
    endpoint = settings.azure_openai_endpoint
    api_key = settings.azure_openai_api_key
    deployment = settings.azure_openai_deployment

    if not endpoint or not api_key or not deployment:
        raise ValueError("Azure OpenAI settings are missing. Set AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, and AZURE_OPENAI_DEPLOYMENT.")

    url = (
        endpoint.rstrip("/")
        + f"/openai/deployments/{deployment}/chat/completions?api-version={settings.azure_openai_api_version}"
    )

    body = {
        "messages": [
            {
                "role": "system",
                "content": "You are a practical personal finance assistant. Keep advice concise and specific.",
            },
            {"role": "user", "content": build_prompt(payload)},
        ],
        "temperature": 0.4,
        "max_tokens": 300,
    }

    response = requests.post(
        url,
        headers={"Content-Type": "application/json", "api-key": api_key},
        data=json.dumps(body),
        timeout=timeout,
    )
    response.raise_for_status()

    data = response.json()
    choices = data.get("choices", [])
    if not choices:
        return "No advice generated this week."
    content = choices[0].get("message", {}).get("content", "")
    return str(content).strip() or "No advice generated this week."
