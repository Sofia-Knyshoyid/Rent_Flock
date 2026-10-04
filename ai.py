"""
Listing analysis for RentFlock.
Uses OpenAI if OPENAI_API_KEY is set, otherwise falls back to rule-based analysis.
"""

import os
import re
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


def analyze_listing(listing_text: str) -> dict:
    """
    Analyze a rental listing for potential red flags and questions to ask.
    Returns a structured dict suitable for display.
    """
    if OPENAI_API_KEY:
        return _analyze_with_llm(listing_text)
    else:
        return _analyze_with_rules(listing_text)


def _analyze_with_llm(listing_text: str) -> dict:
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)

        prompt = f"""Analyze this rental listing for potential red flags and things a renter should investigate.
Focus on: suspicious pricing, payment urgency, missing ownership info, pressure tactics, inconsistencies.
Do NOT say "this is a scam". Instead flag "things worth investigating".

Listing:
\"\"\"
{listing_text[:3000]}
\"\"\"

Respond in this exact JSON format:
{{
  "risk_level": "low" | "medium" | "high",
  "summary": "one short sentence",
  "flags": [
    {{"title": "short title", "detail": "why it matters", "severity": "info|warning|critical"}}
  ],
  "questions": [
    "question the renter should ask"
  ]
}}
"""
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        import json
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"LLM error, falling back to rules: {e}")
        return _analyze_with_rules(listing_text)


def _analyze_with_rules(listing_text: str) -> dict:
    """Simple keyword-based analysis for demo without API key."""
    text = listing_text.lower()
    flags = []
    questions = []
    risk_score = 0

    # Urgency / pressure
    urgency_words = ["urgent", "today only", "asap", "immediately", "hurry", "first come", "don't miss", "last chance", "dziś", "pilne", "natychmiast"]
    if any(w in text for w in urgency_words):
        flags.append({
            "title": "Strong urgency language",
            "detail": "The listing pushes for a very quick decision. Legitimate landlords usually allow some time.",
            "severity": "warning"
        })
        risk_score += 2
        questions.append("Why is there such time pressure? Can I view the apartment tomorrow?")

    # Payment red flags
    payment_words = ["cash only", "western union", "paypal", "crypto", "bitcoin", "prepaid", "deposit today", "pay now", "przelew z góry", "gotówka"]
    if any(w in text for w in payment_words):
        flags.append({
            "title": "Suspicious payment request",
            "detail": "Requests for unusual payment methods or immediate payment before viewing are common in scams.",
            "severity": "critical"
        })
        risk_score += 3
        questions.append("Can I pay the deposit by bank transfer after seeing the contract?")

    # Price too good
    price_match = re.search(r'(\d{3,5})\s*(pln|zł|euro|€|eur)', text)
    if price_match:
        price = int(price_match.group(1))
        if price < 1200 and ("kraków" in text or "krakow" in text or "warsaw" in text or "warszawa" in text):
            flags.append({
                "title": "Unusually low price",
                "detail": f"Rent of {price} appears significantly below market average for a major city. Verify carefully.",
                "severity": "warning"
            })
            risk_score += 2
            questions.append("Is the price correct? What exactly is included?")

    # Missing ownership / documents
    if "owner" not in text and "właściciel" not in text and "owner's" not in text:
        flags.append({
            "title": "No mention of ownership",
            "detail": "The listing does not clearly state who the legal owner is.",
            "severity": "info"
        })
        questions.append("Who is the legal owner of the apartment? Can I see proof of ownership or księga wieczysta?")

    # Deposit conditions
    if "deposit" in text or "kaucja" in text:
        questions.append("What does the deposit cover and under what conditions is it returned?")
    else:
        flags.append({
            "title": "Deposit conditions unclear",
            "detail": "No clear information about the deposit amount or return conditions.",
            "severity": "info"
        })
        questions.append("What is the deposit amount and return policy?")

    # Always useful questions
    questions.extend([
        "Can I see a valid ID of the person showing the apartment?",
        "Is payment by bank transfer possible?",
        "What is the notice period and early termination policy?",
        "Are utilities (heating, water, electricity) individual or shared? Can I see the meters (liczniki)?"
    ])

    # Deduplicate questions
    questions = list(dict.fromkeys(questions))[:8]

    if risk_score >= 4:
        risk_level = "high"
        summary = "Several potential concerns detected. Investigate carefully before committing."
    elif risk_score >= 2:
        risk_level = "medium"
        summary = "A few things worth checking before you decide."
    else:
        risk_level = "low"
        summary = "No major red flags found, but always verify the important details in person."

    if not flags:
        flags.append({
            "title": "Looks reasonably standard",
            "detail": "No obvious pressure tactics or payment red flags detected in the text.",
            "severity": "info"
        })

    return {
        "risk_level": risk_level,
        "summary": summary,
        "flags": flags,
        "questions": questions
    }