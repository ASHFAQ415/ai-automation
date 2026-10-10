import pytest
from app.services.mock_providers import (
    mock_llm_provider,
    mock_payment_provider,
    mock_whatsapp_provider,
    mock_telegram_provider,
    mock_discord_provider,
    mock_tunnel_provider,
    mock_email_provider,
)

@pytest.mark.asyncio
async def test_mock_llm_provider():
    score = await mock_llm_provider.score_lead({"requirement": "Build me a CRM"})
    assert score["temperature"] == "HOT"
    assert score["score"] >= 75

    plan = await mock_llm_provider.generate_plan({"company": "Test Co"}, score)
    assert "Enterprise Automation System" in plan["title"]
    assert len(plan["implementation_steps"]) == 4

@pytest.mark.asyncio
async def test_mock_payment_provider():
    link = mock_payment_provider.create_payment_link(lead_id=42, amount=100000)
    assert "simulate/42" in link
    assert "100000" in link
    assert mock_payment_provider.verify_webhook(b"{}", "sig") is True

@pytest.mark.asyncio
async def test_mock_channels():
    # WhatsApp
    wa_ok = await mock_whatsapp_provider.send_message("919999999999", "Hello Client")
    assert wa_ok is True
    assert len(mock_whatsapp_provider.sent_messages) > 0

    # Telegram
    tg_ok = await mock_telegram_provider.send_message("12345", "Alert")
    assert tg_ok is True
    assert len(mock_telegram_provider.dispatched_alerts) > 0

    # Discord
    dc_ok = await mock_discord_provider.send_embed("Title", "Desc")
    assert dc_ok is True

    # Cloudflare Tunnel
    url = mock_tunnel_provider.spawn_tunnel(8080)
    assert url == "https://mock-demo-8080.trycloudflare.com"

    # Email
    em_ok = await mock_email_provider.send_email("client@test.com", "Subject", "Body")
    assert em_ok is True
