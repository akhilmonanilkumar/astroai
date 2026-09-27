"""Fixed tables of Vedic astrology: grahas, rashis, nakshatras, lordships, dignities.

Sign and nakshatra indices are 0-based everywhere in code (0 = Aries, 0 = Ashwini);
names only appear at the edges, in the dossier JSON.
"""

from enum import StrEnum


class Graha(StrEnum):
    SUN = "Sun"
    MOON = "Moon"
    MARS = "Mars"
    MERCURY = "Mercury"
    JUPITER = "Jupiter"
    VENUS = "Venus"
    SATURN = "Saturn"
    RAHU = "Rahu"
    KETU = "Ketu"


GRAHAS: tuple[Graha, ...] = tuple(Graha)
# The seven visible grahas (no nodes): used by yogas that exclude Rahu/Ketu.
SAPTA_GRAHAS: tuple[Graha, ...] = GRAHAS[:7]

SIGNS: tuple[str, ...] = (
    "Aries",
    "Taurus",
    "Gemini",
    "Cancer",
    "Leo",
    "Virgo",
    "Libra",
    "Scorpio",
    "Sagittarius",
    "Capricorn",
    "Aquarius",
    "Pisces",
)
SIGNS_SANSKRIT: tuple[str, ...] = (
    "Mesha",
    "Vrishabha",
    "Mithuna",
    "Karka",
    "Simha",
    "Kanya",
    "Tula",
    "Vrishchika",
    "Dhanu",
    "Makara",
    "Kumbha",
    "Meena",
)

SIGN_LORDS: tuple[Graha, ...] = (
    Graha.MARS,
    Graha.VENUS,
    Graha.MERCURY,
    Graha.MOON,
    Graha.SUN,
    Graha.MERCURY,
    Graha.VENUS,
    Graha.MARS,
    Graha.JUPITER,
    Graha.SATURN,
    Graha.SATURN,
    Graha.JUPITER,
)

NAKSHATRAS: tuple[str, ...] = (
    "Ashwini",
    "Bharani",
    "Krittika",
    "Rohini",
    "Mrigashira",
    "Ardra",
    "Punarvasu",
    "Pushya",
    "Ashlesha",
    "Magha",
    "Purva Phalguni",
    "Uttara Phalguni",
    "Hasta",
    "Chitra",
    "Swati",
    "Vishakha",
    "Anuradha",
    "Jyeshtha",
    "Mula",
    "Purva Ashadha",
    "Uttara Ashadha",
    "Shravana",
    "Dhanishta",
    "Shatabhisha",
    "Purva Bhadrapada",
    "Uttara Bhadrapada",
    "Revati",
)
NAKSHATRA_SPAN = 360.0 / 27  # 13°20'
PADA_SPAN = NAKSHATRA_SPAN / 4  # 3°20'

# Vimshottari order; nakshatra n is ruled by DASHA_ORDER[n % 9].
DASHA_ORDER: tuple[Graha, ...] = (
    Graha.KETU,
    Graha.VENUS,
    Graha.SUN,
    Graha.MOON,
    Graha.MARS,
    Graha.RAHU,
    Graha.JUPITER,
    Graha.SATURN,
    Graha.MERCURY,
)
DASHA_YEARS: dict[Graha, int] = {
    Graha.KETU: 7,
    Graha.VENUS: 20,
    Graha.SUN: 6,
    Graha.MOON: 10,
    Graha.MARS: 7,
    Graha.RAHU: 18,
    Graha.JUPITER: 16,
    Graha.SATURN: 19,
    Graha.MERCURY: 17,
}
DASHA_TOTAL_YEARS = 120
# Vimshottari year length. Julian year, the most common convention in Indian software.
DASHA_YEAR_DAYS = 365.25

# Dignities for the seven visible grahas (BPHS). Nodes get none: traditions disagree.
EXALTATION_SIGN: dict[Graha, int] = {
    Graha.SUN: 0,
    Graha.MOON: 1,
    Graha.MARS: 9,
    Graha.MERCURY: 5,
    Graha.JUPITER: 3,
    Graha.VENUS: 11,
    Graha.SATURN: 6,
}
DEBILITATION_SIGN: dict[Graha, int] = {g: (s + 6) % 12 for g, s in EXALTATION_SIGN.items()}
# (sign, from degree, to degree) within the sign
MOOLATRIKONA: dict[Graha, tuple[int, float, float]] = {
    Graha.SUN: (4, 0.0, 20.0),
    Graha.MOON: (1, 3.0, 30.0),
    Graha.MARS: (0, 0.0, 12.0),
    Graha.MERCURY: (5, 15.0, 20.0),
    Graha.JUPITER: (8, 0.0, 10.0),
    Graha.VENUS: (6, 0.0, 15.0),
    Graha.SATURN: (10, 0.0, 20.0),
}

# Naisargika (natural) relationships, BPHS. Anything not listed is neutral.
FRIENDS: dict[Graha, frozenset[Graha]] = {
    Graha.SUN: frozenset({Graha.MOON, Graha.MARS, Graha.JUPITER}),
    Graha.MOON: frozenset({Graha.SUN, Graha.MERCURY}),
    Graha.MARS: frozenset({Graha.SUN, Graha.MOON, Graha.JUPITER}),
    Graha.MERCURY: frozenset({Graha.SUN, Graha.VENUS}),
    Graha.JUPITER: frozenset({Graha.SUN, Graha.MOON, Graha.MARS}),
    Graha.VENUS: frozenset({Graha.MERCURY, Graha.SATURN}),
    Graha.SATURN: frozenset({Graha.MERCURY, Graha.VENUS}),
}
ENEMIES: dict[Graha, frozenset[Graha]] = {
    Graha.SUN: frozenset({Graha.VENUS, Graha.SATURN}),
    Graha.MOON: frozenset(),
    Graha.MARS: frozenset({Graha.MERCURY}),
    Graha.MERCURY: frozenset({Graha.MOON}),
    Graha.JUPITER: frozenset({Graha.MERCURY, Graha.VENUS}),
    Graha.VENUS: frozenset({Graha.SUN, Graha.MOON}),
    Graha.SATURN: frozenset({Graha.SUN, Graha.MOON, Graha.MARS}),
}

# Combustion orbs in degrees from the Sun: (direct, retrograde).
COMBUSTION_ORB: dict[Graha, tuple[float, float]] = {
    Graha.MOON: (12.0, 12.0),
    Graha.MARS: (17.0, 17.0),
    Graha.MERCURY: (14.0, 12.0),
    Graha.JUPITER: (11.0, 11.0),
    Graha.VENUS: (10.0, 8.0),
    Graha.SATURN: (15.0, 15.0),
}

KENDRAS = frozenset({1, 4, 7, 10})
TRIKONAS = frozenset({1, 5, 9})
DUSTHANAS = frozenset({6, 8, 12})

WEEKDAYS: tuple[str, ...] = (
    "Somavara",  # Monday (Python weekday 0)
    "Mangalavara",
    "Budhavara",
    "Guruvara",
    "Shukravara",
    "Shanivara",
    "Ravivara",
)
WEEKDAY_LORDS: tuple[Graha, ...] = (
    Graha.MOON,
    Graha.MARS,
    Graha.MERCURY,
    Graha.JUPITER,
    Graha.VENUS,
    Graha.SATURN,
    Graha.SUN,
)

TITHIS: tuple[str, ...] = (
    "Pratipada",
    "Dwitiya",
    "Tritiya",
    "Chaturthi",
    "Panchami",
    "Shashthi",
    "Saptami",
    "Ashtami",
    "Navami",
    "Dashami",
    "Ekadashi",
    "Dwadashi",
    "Trayodashi",
    "Chaturdashi",
)  # the 15th is Purnima (Shukla) or Amavasya (Krishna)

NITYA_YOGAS: tuple[str, ...] = (
    "Vishkumbha",
    "Priti",
    "Ayushman",
    "Saubhagya",
    "Shobhana",
    "Atiganda",
    "Sukarma",
    "Dhriti",
    "Shula",
    "Ganda",
    "Vriddhi",
    "Dhruva",
    "Vyaghata",
    "Harshana",
    "Vajra",
    "Siddhi",
    "Vyatipata",
    "Variyana",
    "Parigha",
    "Shiva",
    "Siddha",
    "Sadhya",
    "Shubha",
    "Shukla",
    "Brahma",
    "Indra",
    "Vaidhriti",
)

# Karanas: half-tithis 2..57 cycle through the 7 movable karanas; the other 4 are fixed.
MOVABLE_KARANAS: tuple[str, ...] = (
    "Bava",
    "Balava",
    "Kaulava",
    "Taitila",
    "Gara",
    "Vanija",
    "Vishti",
)
