"""Scripted lines about credits, passes and payments, in en / Hinglish / Hindi.

Money talk is code, not prompt: the cost is stated before a credit is spent, and nothing
here pressures or frightens (no "your stars are bad, buy now"). Button titles <= 20 chars.
"""

from guruji.agent.language import Language

MONEY: dict[str, dict[Language, str]] = {
    "confirm": {
        "en": "Happy to look into this. It will use {cost} credit{s} (you have {balance}). "
        "Shall I go ahead?",
        "hinglish": "Zaroor dekhta hoon. Is sawaal mein {cost} credit lagega (aapke paas "
        "{balance} hain). Aage badhoon?",
        "hi": "ज़रूर देखता हूँ। इस सवाल में {cost} क्रेडिट लगेगा (आपके पास {balance} हैं)। आगे बढ़ूँ?",
    },
    "used": {
        "en": "({cost} credit used · {left} left)",
        "hinglish": "({cost} credit laga · {left} baaki)",
        "hi": "({cost} क्रेडिट लगा · {left} बाकी)",
    },
    "declined": {
        "en": "Of course. Ask me whenever you're ready 🙏",
        "hinglish": "Bilkul. Jab man ho tab poochiye 🙏",
        "hi": "बिल्कुल। जब मन हो तब पूछिए 🙏",
    },
    "empty": {
        "en": "Your free questions are used up for now. You get one free answer again "
        "tomorrow, or you can top up with a dakshina pack whenever you like.",
        "hinglish": "Abhi ke free sawaal ho gaye. Kal phir ek free jawab milega, ya jab "
        "chahein dakshina pack se credits le sakte hain.",
        "hi": "अभी के मुफ़्त सवाल हो गए। कल फिर एक मुफ़्त जवाब मिलेगा, या जब चाहें दक्षिणा "
        "पैक से क्रेडिट ले सकते हैं।",
    },
    "balance": {
        "en": "You have {balance} credit{s}.{plus}{free}",
        "hinglish": "Aapke paas {balance} credit hain.{plus}{free}",
        "hi": "आपके पास {balance} क्रेडिट हैं।{plus}{free}",
    },
    "balance_plus": {
        "en": " Guru Plus is active till {until}.",
        "hinglish": " Guru Plus {until} tak chalu hai.",
        "hi": " गुरु प्लस {until} तक चालू है।",
    },
    "balance_welcome": {
        "en": " {n} free welcome question{s} left.",
        "hinglish": " {n} free welcome sawaal baaki.",
        "hi": " {n} मुफ़्त स्वागत सवाल बाकी।",
    },
    "balance_daily": {
        "en": " Today's free answer is {state}.",
        "hinglish": " Aaj ka free jawab {state}.",
        "hi": " आज का मुफ़्त जवाब {state}।",
    },
    "daily_left": {"en": "still yours", "hinglish": "baaki hai", "hi": "बाकी है"},
    "daily_used": {"en": "used", "hinglish": "ho gaya", "hi": "हो गया"},
    "refunded": {
        "en": "Sorry that didn't help. I've returned your {cost} credit{s}. Tell me what "
        "felt off and I'll look again.",
        "hinglish": "Maaf kijiye, jawab kaam ka nahi laga. Aapka {cost} credit wapas kar "
        "diya hai. Bataiye kya theek nahi laga, main phir dekhta hoon.",
        "hi": "माफ़ कीजिए, जवाब काम का नहीं लगा। आपका {cost} क्रेडिट वापस कर दिया है। "
        "बताइए क्या ठीक नहीं लगा, मैं फिर देखता हूँ।",
    },
    "sorry": {
        "en": "Sorry that didn't help. Tell me what felt off and I'll look again.",
        "hinglish": "Maaf kijiye, jawab kaam ka nahi laga. Bataiye kya theek nahi laga, main "
        "phir dekhta hoon.",
        "hi": "माफ़ कीजिए, जवाब काम का नहीं लगा। बताइए क्या ठीक नहीं लगा, मैं फिर देखता हूँ।",
    },
}

MONEY_BUTTONS: dict[str, dict[Language, str]] = {
    "spend_yes": {"en": "Yes, go ahead", "hinglish": "Haan, dekhiye", "hi": "हाँ, देखिए"},
    "spend_no": {"en": "Not now", "hinglish": "Abhi nahi", "hi": "अभी नहीं"},
}


def plural(n: int, lang: Language) -> str:
    return "s" if lang == "en" and n != 1 else ""
