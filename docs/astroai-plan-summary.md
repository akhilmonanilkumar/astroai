# AstroAI Guru — plan summary (2026-09-27)

Full living plan: https://claude.ai/code/artifact/97dde8be-e45c-467b-9854-4c0a27aa3755

## Locked decisions
- Product: "Guruji", personal AI Vedic astrologer on WhatsApp for India; one pan-India neutral persona; never claims to be human but must feel natural; human escalation for serious/unknown cases.
- Phase 1: personalised chat + voice notes in/out (Hindi, English, Hinglish); onboarding inside WhatsApp chat via Click-to-WhatsApp ads (72h free window).
- Agent: LangChain create_agent inside a LangGraph parent (consent gate -> onboarding -> guru). Deterministic astro engine (Skyfield + JPL; pyswisseph only as test oracle, AGPL). LLM interprets only.
- Stack: Meta WhatsApp Cloud API direct; FastAPI + Redis queue workers; Supabase (Postgres, pgvector, Auth) Mumbai; RAG = factor-tagged rule cards + hybrid search; Sarvam STT/TTS; DigitalOcean BLR1; Next.js admin console (Supabase Auth + MFA); Telegram escalation alerts (no PII); LiveKit only for later live calls.
- Payments: Razorpay — WhatsApp native checkout (order_details) for top-ups and prepaid passes; UPI Autopay via UPI-app intent for optional renewals (no in-WhatsApp recurring today). Cashfree dropped (not supported by WhatsApp native checkout).
- Business model: free 72h welcome + 1 free short answer/day; dakshina packs ₹11 trial / ₹51 / ₹101 / ₹251 / ₹501 (unit = prashna: question + up to 3 follow-ups; voice = 2 credits); Guru Plus ₹199/mo, ₹501/qtr, ₹1,501/yr; premium readings later. Never charge in distress/escalation.
- Company: Pvt Ltd via MCA SPICe+ (needs 2 directors, else OPC), then K-SWIFT, Udyam, DPIIT; Meta verification + Razorpay KYC need company docs + live website.
- Compliance: DPDP from day one (consent, 18+, delete/export, grievance officer, breach runbook, encryption, audit logs); Meta opt-in rules; no guaranteed outcomes or fear-selling.
- Key cost note: providers report Meta charges service messages from 1 Oct 2026 (~₹0.115 + GST after 1,000 free/month) — keep replies to 1–2 bubbles; confirm on Meta's pricing page.

## Open
Brand name + domain; second director vs OPC; primary LLM; Razorpay vs PayU UPI pricing; GST timing; final free limits and prices after beta.
