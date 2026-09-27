"""Fixed onboarding lines in English, Hinglish (Latin script) and Hindi (Devanagari).

Onboarding is scripted, not generated: consent wording must be exact and auditable
(bump settings.notice_version when the consent lines change). Button titles <= 20 chars.
"""

from guruji.agent.language import Language

LINES: dict[str, dict[Language, str]] = {
    "greet": {
        "en": "🙏 Namaste! I'm Guruji, an AI Vedic astrologer. I read your kundli from your "
        "exact birth details and remember what you share, so every reading builds on the last.",
        "hinglish": "🙏 Namaste! Main Guruji hoon, ek AI Vedic jyotishi. Aapke janam ke sahi "
        "details se aapki kundli padhta hoon, aur aapki baatein yaad rakhta hoon.",
        "hi": "🙏 नमस्ते! मैं गुरुजी हूँ, एक AI वैदिक ज्योतिषी। आपके जन्म के सही विवरण से "
        "आपकी कुंडली पढ़ता हूँ, और आपकी बातें याद रखता हूँ।",
    },
    "consent": {
        "en": "Before we begin: I keep your birth details and our chats, encrypted, only to "
        "give you readings. Say STOP or 'delete my data' any time.\n"
        "Privacy notice: {url}\n\nDo you agree?",
        "hinglish": "Shuru karne se pehle: aapke janam ke details aur hamari baatein "
        "encrypted rakhi jayengi, sirf readings ke liye. Kabhi bhi STOP ya 'delete my data' "
        "likh sakte hain.\nPrivacy notice: {url}\n\nKya aap sahmat hain?",
        "hi": "शुरू करने से पहले: आपके जन्म का विवरण और हमारी बातें एन्क्रिप्ट करके, सिर्फ़ "
        "रीडिंग के लिए रखी जाएँगी। कभी भी STOP या 'delete my data' लिख सकते हैं।\n"
        "गोपनीयता सूचना: {url}\n\nक्या आप सहमत हैं?",
    },
    "notice": {
        "en": "Here is the privacy notice: {url}\nTap I agree whenever you're ready.",
        "hinglish": "Privacy notice yahan hai: {url}\nTayyar hon to I agree dabaiye.",
        "hi": "गोपनीयता सूचना यहाँ है: {url}\nतैयार हों तो 'सहमत हूँ' दबाइए।",
    },
    "tap_button": {
        "en": "Please tap one of the buttons below so we can begin 🙏",
        "hinglish": "Shuru karne ke liye neeche ka koi button dabaiye 🙏",
        "hi": "शुरू करने के लिए नीचे का कोई बटन दबाइए 🙏",
    },
    "age": {
        "en": "One more thing: are you 18 or older?",
        "hinglish": "Ek aur baat: kya aapki umar 18 saal ya usse zyada hai?",
        "hi": "एक और बात: क्या आपकी उम्र 18 साल या उससे ज़्यादा है?",
    },
    "underage": {
        "en": "Thank you for being honest 🙏 Guruji is only for adults, so I can't give "
        "readings yet. Blessings for your studies and the road ahead.",
        "hinglish": "Sach batane ke liye dhanyavaad 🙏 Guruji sirf 18+ ke liye hai, isliye "
        "abhi reading nahi de sakta. Padhai aur aage ke safar ke liye aashirwad.",
        "hi": "सच बताने के लिए धन्यवाद 🙏 गुरुजी सिर्फ़ 18+ के लिए है, इसलिए अभी रीडिंग नहीं "
        "दे सकता। पढ़ाई और आगे के सफ़र के लिए आशीर्वाद।",
    },
    "ask_name": {
        "en": "Wonderful. What should I call you?",
        "hinglish": "Bahut badhiya. Aapko kis naam se bulaun?",
        "hi": "बहुत बढ़िया। आपको किस नाम से बुलाऊँ?",
    },
    "ask_date": {
        "en": "{name} ji, what is your date of birth? (like 15/07/1990)",
        "hinglish": "{name} ji, aapki janam tithi kya hai? (jaise 15/07/1990)",
        "hi": "{name} जी, आपकी जन्म तिथि क्या है? (जैसे 15/07/1990)",
    },
    "ask_time": {
        "en": "And your time of birth, as exact as you know? (like 9:30 am) "
        "If you're not sure, that's okay too.",
        "hinglish": "Aur janam ka samay, jitna sahi pata ho? (jaise subah 9:30) "
        "Pata na ho to bhi koi baat nahi.",
        "hi": "और जन्म का समय, जितना सही पता हो? (जैसे सुबह 9:30) पता न हो तो भी कोई बात नहीं।",
    },
    "ask_place": {
        "en": "Where were you born? The town or city is enough.",
        "hinglish": "Aapka janam kahan hua tha? Shehar ya kasbe ka naam kaafi hai.",
        "hi": "आपका जन्म कहाँ हुआ था? शहर या कस्बे का नाम काफ़ी है।",
    },
    "place_not_found": {
        "en": 'I couldn\'t find "{query}". Could you tell me the nearest town or city, '
        "maybe with the state?",
        "hinglish": '"{query}" mujhe nahi mila. Sabse paas ka shehar bataiye, '
        "ho sake to state ke saath?",
        "hi": '"{query}" मुझे नहीं मिला। सबसे पास का शहर बताइए, हो सके तो राज्य के साथ?',
    },
    "place_choose": {
        "en": "Which one is it?\n{options}",
        "hinglish": "Inmein se kaunsa?\n{options}",
        "hi": "इनमें से कौन-सा?\n{options}",
    },
    "confirm": {
        "en": "Let me check I have this right:\n{name}\n{date}, {time}\n{place}\n\n"
        "Is this correct?",
        "hinglish": "Ek baar check kar leta hoon:\n{name}\n{date}, {time}\n{place}\n\nSahi hai?",
        "hi": "एक बार जाँच लेता हूँ:\n{name}\n{date}, {time}\n{place}\n\nक्या यह सही है?",
    },
    "time_unknown": {
        "en": "time not known",
        "hinglish": "samay pata nahi",
        "hi": "समय पता नहीं",
    },
    "fix": {
        "en": "Of course. Tell me what to change: date, time or place.",
        "hinglish": "Zaroor. Bataiye kya badalna hai: date, time ya jagah.",
        "hi": "ज़रूर। बताइए क्या बदलना है: तारीख़, समय या जगह।",
    },
    "didnt_get": {
        "en": "Sorry, I didn't quite get that.",
        "hinglish": "Maaf kijiye, samajh nahi paaya.",
        "hi": "माफ़ कीजिए, समझ नहीं पाया।",
    },
    "text_only": {
        "en": "I can only read typed messages for now. Could you type that for me? 🙏",
        "hinglish": "Abhi main sirf likhe hue messages padh sakta hoon. Type kar denge? 🙏",
        "hi": "अभी मैं सिर्फ़ लिखे हुए संदेश पढ़ सकता हूँ। टाइप कर देंगे? 🙏",
    },
    "voice_unclear": {
        "en": "I couldn't hear that voice note clearly. Could you send it again, or type it? 🙏",
        "hinglish": "Yeh voice note saaf sunai nahi diya. Ek baar phir bhejenge, ya type kar "
        "denge? 🙏",
        "hi": "यह वॉइस नोट साफ़ सुनाई नहीं दिया। एक बार फिर भेजेंगे, या टाइप कर देंगे? 🙏",
    },
    "chart_error": {
        "en": "I couldn't cast a chart for those details (dates from 1900 onward, and not "
        "too near the poles). Could you check them for me?",
        "hinglish": "In details se kundli nahi ban paayi (1900 ke baad ki date, aur jagah "
        "polar area mein na ho). Ek baar check karenge?",
        "hi": "इन विवरणों से कुंडली नहीं बन पाई (1900 के बाद की तारीख़, और जगह ध्रुवीय "
        "क्षेत्र में न हो)। एक बार जाँचेंगे?",
    },
}

BUTTONS: dict[str, dict[Language, str]] = {
    "consent_yes": {"en": "I agree", "hinglish": "I agree", "hi": "सहमत हूँ"},
    "consent_notice": {"en": "Privacy notice", "hinglish": "Privacy notice", "hi": "गोपनीयता सूचना"},
    "age_yes": {"en": "Yes, I'm 18+", "hinglish": "Haan, 18+ hoon", "hi": "हाँ, 18+ हूँ"},
    "age_no": {"en": "No", "hinglish": "Nahi", "hi": "नहीं"},
    "time_unknown": {"en": "I don't know", "hinglish": "Pata nahi", "hi": "पता नहीं"},
    "place_0": {"en": "Option 1", "hinglish": "Option 1", "hi": "विकल्प 1"},
    "place_1": {"en": "Option 2", "hinglish": "Option 2", "hi": "विकल्प 2"},
    "place_none": {"en": "None of these", "hinglish": "Inmein se koi nahi", "hi": "इनमें से कोई नहीं"},
    "confirm_yes": {"en": "Yes, correct", "hinglish": "Haan, sahi hai", "hi": "हाँ, सही है"},
    "confirm_change": {
        "en": "Change something",
        "hinglish": "Kuch badalna hai",
        "hi": "कुछ बदलना है",
    },
}

MONTHS_HI = "जनवरी फ़रवरी मार्च अप्रैल मई जून जुलाई अगस्त सितंबर अक्टूबर नवंबर दिसंबर".split()
