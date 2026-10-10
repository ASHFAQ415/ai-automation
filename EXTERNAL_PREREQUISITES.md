# External Account & Credential Prerequisites

This document establishes the clear boundary between **Code-Implementable** platform functionality and **Manual Owner Prerequisites** per Section 14A.1 of the Master Specification.

The platform includes full mock/stub providers, local sandbox simulators, and deterministic verification for every integration. However, production deployment requires live owner-provided credentials as detailed below.

---

## Service Matrix

| Service | Category | Owner Action Required | Credentials Required | Destination File / Variable | Sandbox Available? | Production Activation Requirements | Fully Testable Locally? | Current Status |
|---|---|---|---|---|---|---|---|---|
| **Local LLM (Ollama)** | Local Runtime | Install Ollama & run ollama pull qwen3:4b | None (Local daemon on port 11434) | OLLAMA_BASE_URL, OLLAMA_MODEL in .env | Yes (Local) | NVIDIA GPU / CPU execution | Yes (MockLLMProvider + Local Ollama) | ACTIVE LOCAL |
| **Groq Cloud API** | Cloud LLM Fallback | Sign up at console.groq.com | GROQ_API_KEY | GROQ_API_KEY in hermes/.env | Yes (Free tier) | Valid API key | Yes (MockLLMProvider or live API) | ACTIVE (Key provided) |
| **Telegram Bot** | Approval & Control Channel | Create bot via @BotFather, get user ID from @userinfobot | TELEGRAM_BOT_TOKEN, TELEGRAM_HOME_CHANNEL | TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID in hermes/.env | Yes (Local mock / test bot) | Public internet access for polling | Yes (MockTelegramProvider) | ACTIVE (Token provided) |
| **Discord Bot** | Notifications Channel | Create Discord Application & Bot in Discord Developer Portal | DISCORD_BOT_TOKEN, DISCORD_GUILD_ID, DISCORD_ALLOWED_USERS | DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID in hermes/.env | Yes (Local mock / test server) | Bot invited to Discord server | Yes (MockDiscordProvider) | ACTIVE (Token provided) |
| **WhatsApp Business / Cloud API** | Customer Ingress & Quotes | Register Meta Business Account, create WhatsApp App, register phone number | WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_ACCESS_TOKEN, WHATSAPP_WEBHOOK_VERIFY_TOKEN | WHATSAPP_ACCESS_TOKEN etc. in hermes/.env | Yes (Meta Test Number + Baileys QR dev bridge) | Meta Business Verification + Payment method for Meta API | Yes (MockWhatsAppProvider + Baileys simulator) | SANDBOX / DEV BRIDGE ACTIVE (Production Cloud API Pending) |
| **Razorpay (Domestic INR)** | Payment Gateway | Create Razorpay Merchant Account, complete KYC | RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, RAZORPAY_WEBHOOK_SECRET | RAZORPAY_KEY_ID etc. in ackend/.env | Yes (Razorpay Test Mode keys) | Merchant KYC & Bank account verification | Yes (MockPaymentProvider + Local Sandbox Simulator) | SANDBOX VERIFIED — PRODUCTION ACTIVATION PENDING |
| **Stripe (International USD)** | Payment Gateway | Create Stripe Account, activate business profile | STRIPE_API_KEY, STRIPE_WEBHOOK_SECRET | STRIPE_API_KEY etc. in ackend/.env | Yes (Stripe Test Mode keys: sk_test_...) | Stripe Identity & Business Verification | Yes (MockPaymentProvider + Local Sandbox Simulator) | SANDBOX VERIFIED — PRODUCTION ACTIVATION PENDING |
| **Cloudflare Quick Tunnel / Named Tunnel** | Live Client Demo URLs | For Quick Tunnels: None (Zero-account 	rycloudflare). For Named Tunnels: Cloudflare account & domain | CLOUDFLARE_TUNNEL_TOKEN, CLOUDFLARE_ACCOUNT_ID | CLOUDFLARE_TUNNEL_TOKEN in ackend/.env | Yes (Quick Tunnels require 0 credentials) | Domain ownership + Cloudflare Zero Trust setup | Yes (pycloudflared Quick Tunnels + MockTunnelProvider) | QUICK TUNNELS IMPLEMENTED (Named Tunnel Production Activation Pending) |
| **Email (SMTP/IMAP)** | Nurturing & Invoicing | Enable 2FA on Google Account & generate 16-character App Password | EMAIL_ADDRESS, EMAIL_PASSWORD, EMAIL_SMTP_HOST | EMAIL_ADDRESS etc. in hermes/.env | Yes (Local MailHog / Python smtpd / Mock) | Active mailbox with SMTP relay enabled | Yes (MockEmailProvider) | ACTIVE (Gmail App Password provided) |

---

## Credential Safety Rules

1. **Never Commit Secrets:** Real API keys, tokens, and passwords must NEVER be committed to Git. All secrets reside in hermes/.env or ackend/.env which are covered by .gitignore.
2. **Deterministic Mock Strategy (TEST_MODE=true):** In testing and CI environments, production external services are disabled, and deterministic mock providers are loaded.
3. **No Fabricated Accounts:** If an external account has not been registered by the owner, it is explicitly classified as IMPLEMENTED — AWAITING OWNER CREDENTIALS or SANDBOX VERIFIED — PRODUCTION ACTIVATION PENDING.
