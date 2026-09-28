"""DPDP commands, answered by code in every state (even before consent):

  STOP            no more messages; data kept (erased after retention.opted_out_days)
  START           resume after STOP
  delete my data  confirm, then erase everything personal (payment records stay, anonymised)
  export my data  send the user their data as a file, here in the chat

Only short, clear messages count, so "stop worrying me, what does my 7th house say" still
reaches Guruji. Safety detection still runs first.
"""

import re
from typing import Literal

from guruji.agent.language import Language

PrivacyCommand = Literal["stop", "start", "delete", "export"]

_MAX_WORDS = 8
_STOP = re.compile(
    r"^\W*(?:stop|stop (?:all )?messages|unsubscribe|opt ?out|message(?:s)? band karo|"
    r"band karo|रोको|बंद करो|मैसेज बंद करो)\W*$",
    re.IGNORECASE,
)
_START = re.compile(r"^\W*(?:start|resume|unstop|shuru karo|चालू करो|शुरू करो)\W*$", re.IGNORECASE)
_DATA = r"(?:data|details|information|info|account|jankari|jaankari|डेटा|डाटा|जानकारी|अकाउंट)"
_DELETE = re.compile(
    r"(?:delete|erase|remove|wipe|hatao|hata do|mita do|mitao|delete karo|डिलीट|मिटा|हटा)"
    r".{0,30}?" + _DATA + r"|" + _DATA + r".{0,30}?(?:delete|erase|remove|hatao|hata do|"
    r"mita do|mitao|डिलीट|मिटा|हटा)",
    re.IGNORECASE,
)
_EXPORT = re.compile(
    r"(?:export|download|send|bhejo|bhej do|copy|भेजो|भेज दो|डाउनलोड)"
    r".{0,30}?" + _DATA + r"|" + _DATA + r".{0,30}?(?:export|download|bhejo|bhej do|"
    r"भेजो|भेज दो|डाउनलोड)",
    re.IGNORECASE,
)


def detect_privacy(text: str) -> PrivacyCommand | None:
    t = text.strip()
    if not t or len(t.split()) > _MAX_WORDS:
        return None
    if _STOP.match(t):
        return "stop"
    if _START.match(t):
        return "start"
    if _DELETE.search(t):
        return "delete"
    if _EXPORT.search(t):
        return "export"
    return None


PRIVACY: dict[str, dict[Language, str]] = {
    "stopped": {
        "en": "Done. You won't get any more messages from me. Your details stay safe with "
        "us; send START to come back, or 'delete my data' to erase everything.",
        "hinglish": "Theek hai. Ab aapko mere koi message nahi aayenge. Aapki jaankari "
        "surakshit hai; wapas aane ke liye START likhiye, ya sab mitane ke liye "
        "'delete my data'.",
        "hi": "ठीक है। अब आपको मेरे कोई संदेश नहीं आएँगे। आपकी जानकारी सुरक्षित है; वापस आने के "
        "लिए START लिखिए, या सब मिटाने के लिए 'delete my data'।",
    },
    "started": {
        "en": "Welcome back 🙏 I'm here. What's on your mind?",
        "hinglish": "Wapas swagat hai 🙏 Main yahin hoon. Kya poochna chahenge?",
        "hi": "वापस स्वागत है 🙏 मैं यहीं हूँ। क्या पूछना चाहेंगे?",
    },
    "confirm_delete": {
        "en": "This erases your birth details, chart, our conversations and everything I "
        "remember about you, for good. Payment records are kept for tax law, without any of "
        "that. Delete everything?",
        "hinglish": "Isse aapke janam ke details, kundli, hamari saari baatein aur jo kuch main "
        "aapke baare mein yaad rakhta hoon, hamesha ke liye mit jayega. Payment records tax "
        "kanoon ke liye rakhe jaate hain, bina in sab ke. Sab delete kar doon?",
        "hi": "इससे आपके जन्म का विवरण, कुंडली, हमारी सारी बातें और जो कुछ मैं आपके बारे में याद "
        "रखता हूँ, हमेशा के लिए मिट जाएगा। भुगतान रिकॉर्ड कर कानून के लिए रखे जाते हैं, इन सब "
        "के बिना। सब मिटा दूँ?",
    },
    "deleted": {
        "en": "Everything is erased. If you ever message again, we'll start fresh. Take care 🙏",
        "hinglish": "Sab mita diya gaya hai. Agar phir kabhi message karenge, to naye sire se "
        "shuru karenge. Apna khayal rakhiye 🙏",
        "hi": "सब मिटा दिया गया है। अगर फिर कभी संदेश करेंगे, तो नए सिरे से शुरू करेंगे। अपना ख़याल रखिए 🙏",
    },
    "kept": {
        "en": "Okay, nothing was deleted.",
        "hinglish": "Theek hai, kuch delete nahi hua.",
        "hi": "ठीक है, कुछ नहीं मिटाया गया।",
    },
    "export_soon": {
        "en": "I'll send you a file with all your data here in a minute.",
        "hinglish": "Aapki saari jaankari ki file ek minute mein yahin bhejta hoon.",
        "hi": "आपकी सारी जानकारी की फ़ाइल एक मिनट में यहीं भेजता हूँ।",
    },
    "export_caption": {
        "en": "Your data with Guruji, as of today.",
        "hinglish": "Guruji ke paas aapki jaankari, aaj tak ki.",
        "hi": "गुरुजी के पास आपकी जानकारी, आज तक की।",
    },
}

PRIVACY_BUTTONS: dict[str, dict[Language, str]] = {
    "erase_yes": {"en": "Yes, delete all", "hinglish": "Haan, sab mitao", "hi": "हाँ, सब मिटाओ"},
    "erase_no": {"en": "Keep my data", "hinglish": "Nahi, rehne do", "hi": "नहीं, रहने दो"},
}
