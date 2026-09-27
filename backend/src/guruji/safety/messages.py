"""Scripted safety replies. Never generated: wording here is reviewed and must stay exact.

India helplines: Tele-MANAS 14416 (free, 24x7 mental health), 112 (all emergencies),
108 (ambulance), 181 (women's helpline).
"""

from guruji.agent.language import Language
from guruji.safety.detect import Category

REPLIES: dict[Category, dict[Language, list[str]]] = {
    "crisis": {
        "en": [
            "I'm really glad you told me, and I'm here with you. What you're feeling matters "
            "far more than any chart right now.",
            "Please call Tele-MANAS on 14416 (free, any time) to talk to someone now, or 112 "
            "if you are in danger. I've also let our team know, and a person from our team "
            "will message you here. Is there someone you can be with right now?",
        ],
        "hinglish": [
            "Aapne mujhse yeh kaha, iske liye shukriya. Main aapke saath hoon. Abhi aap jo "
            "mehsoos kar rahe hain, woh kisi bhi kundli se kahin zyada zaroori hai.",
            "Abhi kisi se baat karne ke liye Tele-MANAS 14416 par call kijiye (free, kabhi "
            "bhi), ya khatre mein hon to 112. Maine hamari team ko bhi bata diya hai, team "
            "ka koi insaan yahan aapko message karega. Kya abhi koi aapke paas reh sakta hai?",
        ],
        "hi": [
            "आपने मुझसे यह कहा, इसके लिए शुक्रिया। मैं आपके साथ हूँ। अभी आप जो महसूस कर रहे "
            "हैं, वह किसी भी कुंडली से कहीं ज़्यादा ज़रूरी है।",
            "अभी किसी से बात करने के लिए टेली-मानस 14416 पर कॉल कीजिए (मुफ़्त, कभी भी), या "
            "ख़तरे में हों तो 112। मैंने हमारी टीम को भी बता दिया है, टीम का कोई व्यक्ति यहाँ "
            "आपको संदेश करेगा। क्या अभी कोई आपके पास रह सकता है?",
        ],
    },
    "medical": {
        "en": [
            "This sounds like it needs medical help right away. Please call 108 for an "
            "ambulance or 112 now, or get to the nearest hospital.",
            "I've alerted our team as well. Astrology can wait; your safety comes first.",
        ],
        "hinglish": [
            "Yeh turant medical madad wali baat lag rahi hai. Abhi 108 (ambulance) ya 112 "
            "par call kijiye, ya sabse paas ke hospital jaiye.",
            "Maine hamari team ko bhi bata diya hai. Kundli baad mein, pehle aapki suraksha.",
        ],
        "hi": [
            "यह तुरंत चिकित्सा सहायता वाली बात लग रही है। अभी 108 (एम्बुलेंस) या 112 पर कॉल "
            "कीजिए, या सबसे पास के अस्पताल जाइए।",
            "मैंने हमारी टीम को भी बता दिया है। कुंडली बाद में, पहले आपकी सुरक्षा।",
        ],
    },
    "abuse": {
        "en": [
            "I'm so sorry you're going through this. No one deserves to be hurt, and it is "
            "not your fault.",
            "If you are in danger now, please call 112. The women's helpline 181 and "
            "Tele-MANAS 14416 can also help and listen. I've let our team know, and someone "
            "from our team will message you here.",
        ],
        "hinglish": [
            "Mujhe bahut afsos hai ki aap yeh sab jhel rahe hain. Kisi ko bhi chot pahunchana "
            "galat hai, aur ismein aapki koi galti nahi hai.",
            "Agar abhi khatra hai to 112 par call kijiye. Mahila helpline 181 aur Tele-MANAS "
            "14416 bhi madad aur baat kar sakte hain. Maine hamari team ko bata diya hai, "
            "team ka koi insaan yahan aapko message karega.",
        ],
        "hi": [
            "मुझे बहुत अफ़सोस है कि आप यह सब झेल रहे हैं। किसी को चोट पहुँचाना ग़लत है, और "
            "इसमें आपकी कोई ग़लती नहीं है।",
            "अगर अभी ख़तरा है तो 112 पर कॉल कीजिए। महिला हेल्पलाइन 181 और टेली-मानस 14416 भी "
            "मदद कर सकते हैं। मैंने हमारी टीम को बता दिया है, टीम का कोई व्यक्ति यहाँ आपको "
            "संदेश करेगा।",
        ],
    },
    "legal": {
        "en": [
            "That sounds serious, and it needs a lawyer's help more than a chart. I've let "
            "our team know; someone from our team will message you here."
        ],
        "hinglish": [
            "Yeh gambhir baat hai, aur ismein kundli se zyada vakeel ki madad chahiye. Maine "
            "hamari team ko bata diya hai, team ka koi insaan yahan message karega."
        ],
        "hi": [
            "यह गंभीर बात है, और इसमें कुंडली से ज़्यादा वकील की मदद चाहिए। मैंने हमारी टीम "
            "को बता दिया है, टीम का कोई व्यक्ति यहाँ संदेश करेगा।"
        ],
    },
    "human_requested": {
        "en": [
            "Of course. I've asked our team to join; a person will reply to you right here, "
            "usually within a few hours. Their messages will say they are from the team."
        ],
        "hinglish": [
            "Zaroor. Maine hamari team ko bula liya hai; ek insaan yahin aapko jawab dega, "
            "aam taur par kuch ghanton mein. Unke message par likha hoga ki woh team se hain."
        ],
        "hi": [
            "ज़रूर। मैंने हमारी टीम को बुला लिया है; एक व्यक्ति यहीं आपको जवाब देगा, आम तौर पर "
            "कुछ घंटों में। उनके संदेश पर लिखा होगा कि वे टीम से हैं।"
        ],
    },
}

# Sent (at most every few hours) while a human from the team is handling the chat.
HOLDING: dict[Language, str] = {
    "en": "Our team has your message and will reply here soon. If you are in danger, please "
    "call 112, or Tele-MANAS 14416 to talk to someone now.",
    "hinglish": "Hamari team ko aapka message mil gaya hai, jaldi yahin jawab milega. Khatra "
    "ho to 112, ya abhi kisi se baat karne ke liye Tele-MANAS 14416 par call kijiye.",
    "hi": "हमारी टीम को आपका संदेश मिल गया है, जल्द यहीं जवाब मिलेगा। ख़तरा हो तो 112, या "
    "अभी किसी से बात करने के लिए टेली-मानस 14416 पर कॉल कीजिए।",
}

# Used when a guru reply still breaks a guardrail after one rewrite.
SAFE_FALLBACK: dict[Language, str] = {
    "en": "I'd rather not answer that one in the way it came out. Could you tell me a little "
    "more about what you'd like guidance on?",
    "hinglish": "Is sawaal ka jawab main is tarah nahi dena chahunga. Aap thoda aur bata sakte "
    "hain ki aapko kis baat mein margdarshan chahiye?",
    "hi": "इस सवाल का जवाब मैं इस तरह नहीं देना चाहूँगा। आप थोड़ा और बता सकते हैं कि आपको किस "
    "बात में मार्गदर्शन चाहिए?",
}

# First line of every message a person from the team sends from the admin console, so the
# user can tell it apart from Guruji (the human_requested reply promises this).
TEAM_LABEL: dict[Language, str] = {
    "en": "Guruji team (a person):",
    "hinglish": "Guruji team (ek insaan):",
    "hi": "गुरुजी टीम (एक व्यक्ति):",
}
