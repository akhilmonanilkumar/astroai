"""Hindi / Hinglish astrology words → English, to expand questions before search.

Small multilingual embedding models handle Devanagari Hindi reasonably but romanised
Hinglish poorly ("shaadi kab hogi" lands nowhere near "marriage"). Appending the English
meaning of known words fixes that cheaply and deterministically. Extend freely: each
line is "<words in any script>": "<english expansion>".
"""

import re

_GLOSSARY: dict[str, str] = {
    "shaadi shadi shaadee vivah vivaah byah byaah rishta rishte dulha dulhan patni pati "
    "biwi शादी विवाह ब्याह रिश्ता पति पत्नी": "marriage spouse wedding relationship",
    "married marry wife husband spouse fiance fiancee wedding engagement": (
        "marriage spouse relationship"
    ),
    "canada usa america us uk london england australia germany dubai gulf singapore "
    "newzealand europe": "foreign abroad settle travel",
    "pyaar pyar prem love affair girlfriend boyfriend प्यार प्रेम": "love romance relationship",
    "naukri nokri job kaam promotion transfer boss office sarkari नौकरी काम प्रमोशन "
    "सरकारी": "career job work promotion",
    "vyapar vyapaar business dukaan dhandha व्यापार धंधा दुकान": "business career income",
    "tabiyat tabiyet sehat bimari beemari swasthya dard तबियत सेहत बीमारी स्वास्थ्य": "health",
    "paisa paise dhan kamai income salary karza karz loan paisa धन पैसा कमाई क़र्ज़ कर्ज": (
        "money wealth income finance debts"
    ),
    "bachcha bacche bachche santan santaan baby pregnancy संतान बच्चा बच्चे": "children",
    "exam padhai pariksha shiksha college admission परीक्षा पढ़ाई शिक्षा": "education studies",
    "videsh abroad foreign bahar visa विदेश वीज़ा": "foreign travel abroad",
    "yatra safar travel यात्रा सफ़र": "travel",
    "sade sadesati saadhesati साढ़ेसाती साढ़े": "sade sati saturn transit",
    "shani शनि": "saturn",
    "guru brihaspati गुरु बृहस्पति": "jupiter",
    "mangal मंगल": "mars",
    "shukra शुक्र": "venus",
    "budh बुध": "mercury",
    "surya सूर्य": "sun",
    "chandra चंद्र चन्द्र": "moon",
    "rahu राहु": "rahu",
    "ketu केतु": "ketu",
    "dasha mahadasha antardasha दशा महादशा अंतर्दशा": "dasha period timing",
    "upay upaay totka totke remedy उपाय टोटका": "remedies mantra charity",
    "ghar makaan makan zameen flat property plot घर मकान ज़मीन": "home house property",
    "maa mummy mata माँ माता": "mother",
    "papa pita पिता पापा": "father",
    "bhai behen bhaiya didi भाई बहन": "siblings",
    "dost friends दोस्त": "friends",
    "mann tension chinta dar ghabrahat मन चिंता डर घबराहट": "mind emotions anxiety",
    "kismat bhagya luck किस्मत भाग्य": "luck fortune",
    "kab when कब": "timing when",
    "manglik mangal-dosh मांगलिक": "manglik mars dosha",
    "kundli kundali janampatri कुंडली जन्मपत्री": "birth chart",
}

_LEXICON: dict[str, str] = {
    w: meaning for words, meaning in _GLOSSARY.items() for w in words.split()
}
_WORDS = re.compile(r"[\w\u0900-\u097F]+")


def expand(text: str) -> str:
    """The question plus English meanings of any known Hindi/Hinglish words."""
    found = dict.fromkeys(_LEXICON[w] for w in _WORDS.findall(text.casefold()) if w in _LEXICON)
    return f"{text} | {' '.join(found)}" if found else text
