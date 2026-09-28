"""What can be bought, as WhatsApp messages: the offer list and the checkout card.

Packs and passes come from app_config (prices are GST-inclusive rupees). The offer is an
interactive list (rows "buy:<item id>"); picking a row creates an order and sends an
`order_details` "Review and pay" card, which WhatsApp pays through Razorpay, or (before
a WhatsApp payment configuration exists) a Razorpay payment link behind a URL button.
"""

from dataclasses import dataclass
from typing import Any, Literal

from guruji.agent.language import Language
from guruji.appconfig import Pack, Pass

BUY_PREFIX = "buy:"
_ROW_TITLE = 24  # WhatsApp list limits
_ROW_DESC = 72
_MAX_ROWS = 10
_BUTTON_TEXT = 20  # cta_url display_text


@dataclass(frozen=True)
class Item:
    kind: Literal["pack", "pass"]
    id: str
    price_inr: int
    prashnas: int | None = None
    days: int | None = None


_TEXT: dict[str, dict[Language, str]] = {
    "offer": {
        "en": "Here are the dakshina packs and Guru Plus. Prices include GST; pay right "
        "here in WhatsApp.",
        "hinglish": "Yeh rahe dakshina packs aur Guru Plus. Daam mein GST shaamil hai; "
        "payment yahin WhatsApp mein.",
        "hi": "ये रहे दक्षिणा पैक और गुरु प्लस। दाम में GST शामिल है; भुगतान यहीं WhatsApp में।",
    },
    "offer_link": {
        "en": "Here are the dakshina packs and Guru Plus. Prices include GST; you pay "
        "securely through Razorpay.",
        "hinglish": "Yeh rahe dakshina packs aur Guru Plus. Daam mein GST shaamil hai; "
        "payment Razorpay se surakshit hota hai.",
        "hi": "ये रहे दक्षिणा पैक और गुरु प्लस। दाम में GST शामिल है; भुगतान Razorpay से सुरक्षित होता है।",
    },
    "button": {"en": "See options", "hinglish": "Options dekhein", "hi": "विकल्प देखें"},
    "packs": {"en": "Dakshina packs", "hinglish": "Dakshina packs", "hi": "दक्षिणा पैक"},
    "plus": {"en": "Guru Plus", "hinglish": "Guru Plus", "hi": "गुरु प्लस"},
    "pack_row": {"en": "{n} questions", "hinglish": "{n} sawaal", "hi": "{n} सवाल"},
    "pass_row": {
        "en": "{days} days · up to {per_day} questions a day, voice replies",
        "hinglish": "{days} din · roz {per_day} sawaal tak, voice jawab",
        "hi": "{days} दिन · रोज़ {per_day} सवाल तक, वॉइस जवाब",
    },
    "checkout": {
        "en": "{name}: ₹{price} (GST included). Tap below to pay; your {what} will be added "
        "as soon as the payment is confirmed.",
        "hinglish": "{name}: ₹{price} (GST shaamil). Neeche tap karke payment kijiye; "
        "payment confirm hote hi aapke {what} jud jayenge.",
        "hi": "{name}: ₹{price} (GST शामिल)। नीचे टैप करके भुगतान कीजिए; भुगतान पक्का होते ही "
        "आपके {what} जुड़ जाएँगे।",
    },
    "what_pack": {"en": "credits", "hinglish": "credits", "hi": "क्रेडिट"},
    "what_pass": {"en": "Guru Plus days", "hinglish": "Guru Plus ke din", "hi": "गुरु प्लस के दिन"},
    "paid_pack": {
        "en": "🙏 Payment received. {n} credits added; you now have {balance}. Ask me "
        "anything on your mind.",
        "hinglish": "🙏 Payment mil gaya. {n} credits jud gaye; ab aapke paas {balance} hain. "
        "Jo man mein ho, poochiye.",
        "hi": "🙏 भुगतान मिल गया। {n} क्रेडिट जुड़ गए; अब आपके पास {balance} हैं। जो मन में हो, पूछिए।",
    },
    "paid_pass": {
        "en": "🙏 Payment received. Guru Plus is active till {until}. Ask me anything on "
        "your mind.",
        "hinglish": "🙏 Payment mil gaya. Guru Plus {until} tak chalu hai. Jo man mein ho, "
        "poochiye.",
        "hi": "🙏 भुगतान मिल गया। गुरु प्लस {until} तक चालू है। जो मन में हो, पूछिए।",
    },
    "pay_button": {"en": "Pay ₹{price}", "hinglish": "₹{price} pay karein", "hi": "₹{price} भुगतान"},
    "unavailable": {
        "en": "Payments aren't opening right now, and nothing was charged. Please try again "
        "in a little while.",
        "hinglish": "Abhi payment khul nahi raha, aur koi paisa nahi kata. Thodi der baad "
        "phir try kijiye.",
        "hi": "अभी भुगतान खुल नहीं रहा, और कोई पैसा नहीं कटा। थोड़ी देर बाद फिर कोशिश कीजिए।",
    },
    "refunded": {
        "en": "Your payment of {price} was refunded to you.",
        "hinglish": "Aapka {price} ka payment aapko wapas kar diya gaya hai.",
        "hi": "आपका {price} का भुगतान आपको वापस कर दिया गया है।",
    },
    "refunded_pack": {
        "en": "Your payment of {price} was refunded to you, so its {n} unused credits "
        "were removed.",
        "hinglish": "Aapka {price} ka payment wapas kar diya gaya hai, isliye uske {n} "
        "bache hue credits hata diye gaye.",
        "hi": "आपका {price} का भुगतान वापस कर दिया गया है, इसलिए उसके {n} बचे हुए क्रेडिट हटा दिए गए।",
    },
    "refunded_pass": {
        "en": "Your payment of {price} was refunded to you, so that Guru Plus pass has ended.",
        "hinglish": "Aapka {price} ka payment wapas kar diya gaya hai, isliye woh Guru Plus "
        "pass khatam ho gaya.",
        "hi": "आपका {price} का भुगतान वापस कर दिया गया है, इसलिए वह गुरु प्लस पास समाप्त हो गया।",
    },
    "expired": {  # shown on a WhatsApp checkout card once it can no longer be paid
        "en": "This payment request has expired. Send 'recharge' for a new one.",
        "hinglish": "Yeh payment request expire ho gaya. Naye ke liye 'recharge' likhiye.",
        "hi": "यह भुगतान अनुरोध समाप्त हो गया। नए के लिए 'recharge' लिखिए।",
    },
    "failed": {
        "en": "The payment didn't go through, and nothing was charged. You can try again "
        "whenever you like.",
        "hinglish": "Payment nahi ho paaya, aur koi paisa nahi kata. Jab chahein phir try "
        "kar sakte hain.",
        "hi": "भुगतान नहीं हो पाया, और कोई पैसा नहीं कटा। जब चाहें फिर कोशिश कर सकते हैं।",
    },
}


def text(key: str, lang: Language, **kw: Any) -> str:
    return _TEXT[key][lang].format(**kw)


def pass_name(item_id: str) -> str:
    return {
        "plus_monthly": "Guru Plus · 1 month",
        "plus_quarterly": "Guru Plus · 3 months",
        "plus_yearly": "Guru Plus · 1 year",
    }.get(item_id, "Guru Plus")


def item_name(item: Item, lang: Language) -> str:
    if item.kind == "pass":
        return pass_name(item.id)
    return f"₹{item.price_inr} · {text('pack_row', lang, n=item.prashnas)}"


def items(packs: list[Pack], passes: list[Pass]) -> dict[str, Item]:
    out = {p.id: Item("pack", p.id, p.price_inr, prashnas=p.prashnas) for p in packs}
    out.update({p.id: Item("pass", p.id, p.price_inr, days=p.days) for p in passes})
    return out


def offer(
    packs: list[Pack],
    passes: list[Pass],
    plus_per_day: int,
    lang: Language,
    *,
    first_buy: bool,
    by_link: bool = False,
) -> dict[str, Any]:
    """An interactive list message (without "to"); the trial pack only before a first buy.
    `by_link`: checkout is a Razorpay payment link, not payment inside WhatsApp."""
    pack_rows = [
        {
            "id": f"{BUY_PREFIX}{p.id}",
            "title": f"₹{p.price_inr} · {text('pack_row', lang, n=p.prashnas)}"[:_ROW_TITLE],
            **({"description": p.badge[:_ROW_DESC]} if p.badge else {}),
        }
        for p in packs
        if first_buy or not p.show_once
    ]
    pass_rows = [
        {
            "id": f"{BUY_PREFIX}{p.id}",
            "title": f"₹{p.price_inr} · {pass_name(p.id).split(' · ')[-1]}"[:_ROW_TITLE],
            "description": text("pass_row", lang, days=p.days, per_day=plus_per_day)[:_ROW_DESC],
        }
        for p in passes
    ]
    room = _MAX_ROWS - len(pass_rows)
    sections = [
        {"title": text("packs", lang), "rows": pack_rows[:room]},
        {"title": text("plus", lang), "rows": pass_rows},
    ]
    return {
        "type": "list",
        "body": {"text": text("offer_link" if by_link else "offer", lang)},
        "action": {"button": text("button", lang), "sections": [s for s in sections if s["rows"]]},
    }


def link_checkout(item: Item, url: str, lang: Language) -> dict[str, Any]:
    """A Razorpay payment link behind a "Pay ₹N" URL button (PAYMENT_CHECKOUT=link)."""
    what = text("what_pack" if item.kind == "pack" else "what_pass", lang)
    body = text("checkout", lang, name=item_name(item, lang), price=item.price_inr, what=what)
    return {
        "type": "cta_url",
        "body": {"text": body},
        "action": {
            "name": "cta_url",
            "parameters": {
                "display_text": text("pay_button", lang, price=item.price_inr)[:_BUTTON_TEXT],
                "url": url,
            },
        },
    }


def checkout(
    item: Item, reference_id: str, payment_config: str, lang: Language, expire_by: int
) -> dict[str, Any]:
    """The order_details "Review and pay" card (India payments, Razorpay gateway).

    `expire_by` (unix seconds, at least 5 minutes ahead) matches the order's pending
    window, so the card can't be paid after the retention sweep has expired the order."""
    amount = {"value": item.price_inr * 100, "offset": 100}
    name = item_name(item, lang)
    what = text("what_pack" if item.kind == "pack" else "what_pass", lang)
    return {
        "type": "order_details",
        "body": {"text": text("checkout", lang, name=name, price=item.price_inr, what=what)},
        "action": {
            "name": "review_and_pay",
            "parameters": {
                "reference_id": reference_id,
                "type": "digital-goods",
                "payment_settings": [
                    {
                        "type": "payment_gateway",
                        "payment_gateway": {
                            "type": "razorpay",
                            "configuration_name": payment_config,
                            # becomes the Razorpay order's receipt: how we look it up
                            "razorpay": {
                                "receipt": reference_id,
                                "notes": {"reference_id": reference_id},
                            },
                        },
                    }
                ],
                "currency": "INR",
                "total_amount": amount,
                "order": {
                    "status": "pending",
                    "expiration": {
                        "timestamp": str(expire_by),
                        "description": text("expired", lang),
                    },
                    "items": [
                        {"retailer_id": item.id, "name": name[:60], "amount": amount, "quantity": 1}
                    ],
                    "subtotal": amount,
                },
            },
        },
    }
