"""Second Read (v4) rule reader: the hardened rulebook baseline. Pure code, no model, no network.

It reads the same input as every other reader (`readers.reader_input`: the estimate's line items and total, today's date,
the quoted slot and the reply text) and returns Facts (`contract.py`). It started from the patterns of `SmartRules`
(`second_ask/tournament/policies.py`), `amounts_in` (`second_ask/menu.py`) and `opt_out_status`
(`second_ask/compliance.py`) and was hardened on the dev split only (log: second_read/RULES-HARDENING-LOG.md).

Every fact is collected independently; nothing stops at the first match. Reading conventions shared with the data
(second_read/data.py CONVENTIONS): "before the 14th" means the 13th; "the end of next week" is Friday of next week;
"a couple of weeks" is 2, "about a month" 4, "about two months" 8; "ready" only when they want to book now or ask when
the crew can come (not with a wait, and not while someone else still has to agree).

`SmartRulesPatternReader` is the plain starting point (SmartRules' own regexes mapped onto Facts, no date reading),
kept for the before/after comparison in rules_dev_score.py.
"""
import re
import time
import unicodedata
from datetime import date, timedelta
from itertools import combinations

from second_ask.compliance import opt_out_status
from second_ask.menu import amounts_in

from ..contract import FactsError, parse
from . import optional_items, reader_input

# ---------------------------------------------------------------------------------------------------------- text

_PUNCT = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "“": '"', "”": '"', "—": " - ",
                        "–": " - ", "¿": " ", "¡": " ", " ": " "})


_ORD_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
              "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
              "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19, "twentieth": 20,
              "thirtieth": 30}
_ORD_TENS = {"twenty": 20, "thirty": 30}
_ES_DAYS = {"primero": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8,
            "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
            "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20, "veintiuno": 21,
            "veintidos": 22, "veintitres": 23, "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26,
            "veintisiete": 27, "veintiocho": 28, "veintinueve": 29, "treinta": 30, "treinta y uno": 31}
_SHORTHAND = [(r"\bw(?:h)?en\b", "when"), (r"\bwhn\b", "when"), (r"\bu\b", "you"), (r"\bur\b", "your"), (r"\bpl[sz]\b", "please"), (r"\bb4\b", "before"),
              (r"\bnxt\b", "next"), (r"\bthru\b", "through"), (r"&", " and "), (r"\bw/o\b", "without"),
              (r"\bmos\b", "months"), (r"\bwk\b", "week"), (r"\bwks\b", "weeks"), (r"\bhrs\b", "hours")]


def _ordinal_words(t):
    def tens(m):
        return f"{_ORD_TENS[m.group(1)] + _ORD_WORDS[m.group(2)]}th"
    t = re.sub(r"\b(twenty|thirty)[\s-](first|second|third|fourth|fifth|sixth|seventh|eighth|ninth)\b", tens, t)
    t = re.sub(r"\b(" + "|".join(_ORD_WORDS) + r")\b(?=\s+(?:of\s+)?(?:" + "|".join(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")) + r"))",
        lambda m: f"{_ORD_WORDS[m.group(1)]}th", t)
    t = re.sub(r"\bthe (" + "|".join(_ORD_WORDS) + r")\b(?! (?:time|one|thing|step|part|week|weeks|month|option|"
               r"item|of all|half))", lambda m: f"the {_ORD_WORDS[m.group(1)]}th", t)
    t = re.sub(r"\b(" + "|".join(sorted(_ES_DAYS, key=len, reverse=True)) + r") de (enero|febrero|marzo|abril|mayo|"
               r"junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre)\b",
               lambda m: f"{_ES_DAYS[m.group(1)]} de {m.group(2)}", t)
    return t


def normalize(text):
    """Lower case, curly quotes and dashes made plain, accents removed (miércoles -> miercoles), texting shorthand
    spelled out ("u" -> "you", "b4" -> "before"), ordinal words as numbers ("the nineteenth" -> "the 19th",
    "el quince de noviembre" -> "el 15 de noviembre"), spaces collapsed."""
    t = (text or "").translate(_PUNCT)
    t = "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c)).lower()
    for pat, sub in _SHORTHAND:
        t = re.sub(pat, sub, t)
    t = _ordinal_words(t)
    return re.sub(r"[ \t]+", " ", t).strip()


# ---------------------------------------------------------------------------------------------------------- numbers

_UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
          "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
          "seventeen": 17, "eighteen": 18, "nineteen": 19,
          # Spanish
          "cero": 0, "uno": 1, "un": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7,
          "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
          "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
         "ninety": 90, "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70, "ochenta": 80,
         "noventa": 90}
_HUNDREDS_ES = {"cien": 100, "ciento": 100, "doscientos": 200, "trescientos": 300, "cuatrocientos": 400,
                "quinientos": 500, "seiscientos": 600, "setecientos": 700, "ochocientos": 800, "novecientos": 900}
_SCALE = {"hundred": 100, "thousand": 1000, "grand": 1000, "k": 1000, "mil": 1000}
_JOIN = {"and", "y", "-"}
_NUM_WORD = set(_UNITS) | set(_TENS) | set(_HUNDREDS_ES) | set(_SCALE)
_SMALL_WORDS = {"one": 1, "a": 1, "an": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "couple": 2, "a couple": 2,
                "a couple of": 2, "couple of": 2, "a few": 3, "few": 3,
                "un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7,
                "ocho": 8, "nueve": 9, "diez": 10, "un par de": 2, "par de": 2, "unas": None}


def _word_number(words):
    """Value of a run of number words ("nineteen hundred", "two thousand five hundred", "eighteen fifty",
    "cuatrocientos cincuenta"); None when the run is not a number."""
    words = [w for w in words if w not in _JOIN]
    if not words:
        return None
    # "eighteen fifty" / "twelve hundred fifty": a 10-99 pair said like a year
    vals = []
    for w in words:
        if w in _UNITS:
            vals.append(("u", _UNITS[w]))
        elif w in _TENS:
            vals.append(("t", _TENS[w]))
        elif w in _HUNDREDS_ES:
            vals.append(("h", _HUNDREDS_ES[w]))
        elif w in _SCALE:
            vals.append(("s", _SCALE[w]))
        else:
            return None
    total, cur, pending_pair = 0, 0, None
    i = 0
    # year-like "eighteen fifty": two 2-digit groups with no scale word
    if all(k in "ut" for k, _ in vals) and len(vals) >= 2:
        groups, g = [], 0
        for k, v in vals:
            if k == "t" and g:
                groups.append(g)
                g = v
            elif k == "u" and g >= 20 and g % 10 == 0 and v < 10:
                g += v
            elif k == "u" and g:
                groups.append(g)
                g = v
            else:
                g += v
        groups.append(g)
        if len(groups) == 2 and 10 <= groups[0] <= 99 and 10 <= groups[1] <= 99:
            return groups[0] * 100 + groups[1]
    for n, (k, v) in enumerate(vals):
        if k == "u" and v < 10 and cur == 0 and total and n + 1 < len(vals) and vals[n + 1][0] == "t":
            cur += v * 100          # "nine thousand two fifty": the digit after a thousand counts hundreds
        elif k in "ut":
            cur += v
        elif k == "h":
            cur += v
        elif k == "s":
            if v == 100:
                cur = (cur or 1) * 100
            else:
                total += (cur or 1) * v
                cur = 0
    return total + cur


_TOKEN = re.compile(r"\$?\d[\d,]*(?:\.\d+)?k?|[a-z]+|-")


def word_amounts(text):
    """[(value, start, end)] for runs of number words in normalized text, e.g. "four hundred" -> 400."""
    out = []
    toks = [(m.group(), m.start(), m.end()) for m in re.finditer(r"[a-z]+", text)]
    i = 0
    while i < len(toks):
        if toks[i][0] in _NUM_WORD and toks[i][0] not in ("un", "una", "uno", "k", "grand"):
            j = i
            while j + 1 < len(toks) and (toks[j + 1][0] in _NUM_WORD or (toks[j + 1][0] in ("and", "y")
                                         and j + 2 < len(toks) and toks[j + 2][0] in _NUM_WORD)):
                j += 1
            run = [t[0] for t in toks[i:j + 1]]
            v = _word_number(run)
            if v is not None:
                out.append((float(v), toks[i][1], toks[j][2]))
            i = j + 1
        else:
            i += 1
    return out


_DIGIT_AMOUNT = re.compile(r"(\$\s?)?(\d{1,3}(?:,\d{3})+|\d{1,3}(?:\.\d{3})+(?![\d,])|\d+)(\.\d{1,2})?\s?(k\b|grand\b|mil\b|thousand\b)?")


def digit_amounts(text):
    """[(value, start, end, has_dollar)] for digit amounts ("$1,850", "1850", "2k", "$2.5k")."""
    out = []
    for m in _DIGIT_AMOUNT.finditer(text):
        raw = m.group(2).replace(",", "").replace(".", "") + (m.group(3) or "")
        v = float(raw)
        if m.group(4):
            v *= 1000
        out.append((v, m.start(), m.end(), bool(m.group(1)) or bool(re.match(r"\s*(dollars|bucks|usd|dolares)\b",
                                                                              text[m.end():]))))
    return out


# ---------------------------------------------------------------------------------------------------------- dates

_MONTHS = {"jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4, "may": 5,
           "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
           "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
           "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
           "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12}
_WEEKDAYS = {"monday": 0, "mon": 0, "tuesday": 1, "tues": 1, "tue": 1, "wednesday": 2, "wed": 2, "weds": 2,
             "thursday": 3, "thurs": 3, "thur": 3, "thu": 3, "friday": 4, "fri": 4, "saturday": 5, "sat": 5,
             "sunday": 6, "sun": 6,
             "lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4, "sabado": 5, "domingo": 6}
_MONTH_RE = "|".join(sorted(_MONTHS, key=len, reverse=True))
_WD_RE = "|".join(sorted(_WEEKDAYS, key=len, reverse=True))
_ORD = r"(?:st|nd|rd|th)?"
_YEAR = r"(?:,?\s*(20\d\d))?"
_DATE_PATTERNS = [
    # "nov 10", "november 9th, 2026", "nov. 10"
    ("md", re.compile(rf"\b({_MONTH_RE})\.?\s+(\d{{1,2}}){_ORD}\b{_YEAR}")),
    # "10 de noviembre", "25 de noviembre de 2026", "10 nov"
    ("dm", re.compile(rf"\b(\d{{1,2}})(?:\s+de)?\s+({_MONTH_RE})\b(?:\s+(?:de\s+)?(20\d\d))?")),
    # "the 10th of november"
    ("dofm", re.compile(rf"\bthe\s+(\d{{1,2}}){_ORD}\s+of\s+({_MONTH_RE})\b{_YEAR}")),
    # "11/10", "11/10/2026"
    ("slash", re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")),
    # "thursday the 12th", "el miercoles 25", "saturday 7th"
    ("wd_day", re.compile(rf"\b(?:{_WD_RE})\.?,?\s+(?:the\s+)?(\d{{1,2}}){_ORD}\b(?!\s*(?:weeks?|semanas?|days?|dias?))")),
    # "the 12th", "el 12"
    ("day", re.compile(r"\b(?:the|el|del|al)\s+(\d{1,2})(?:st|nd|rd|th)\b|\b(?:el|del|al|para el)\s+(\d{1,2})\b"
                       rf"(?!\s*(?:weeks?|semanas?|semanitas?|days?|dias?|%|(?:de\s+)?(?:{_MONTH_RE})\b))")),
    # bare ordinal "by 12th"
    ("day", re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\b(?!\s*(?:of|de)\b)")),
]
_REL = [
    (re.compile(r"\b(?:end of next week|end of the next week|fin de la (?:proxima|otra) semana|"
                r"finales de la (?:proxima|otra) semana)\b"), "eonw"),
    (re.compile(r"\b(?:end of (?:this|the) week|fin de (?:esta|la) semana|this weekend|este fin de semana)\b"), "eotw"),
    (re.compile(r"\b(?:day after tomorrow|pasado manana)\b"), "after_tomorrow"),
    (re.compile(r"\b(?:tomorrow|tmrw|tmr)\b|\bpara manana\b|\bmanana mismo\b"), "tomorrow"),
]
_BEFORE = re.compile(r"\b(?:before|prior to|antes(?: de| del)?)\s+(?:the\s+|el\s+)?$")
_WD_ALONE = re.compile(rf"\b(?:by|before|done on|finished on|ready on|completed on|para el|para|antes del?|el|this|"
                       rf"next|este|el proximo|no later than|deadline)\s+(?:this\s+|next\s+|coming\s+)?({_WD_RE})\b"
                       rf"(?!\.?\s*,?\s*(?:the\s+|el\s+)?(?:\d|(?:{_MONTH_RE})\b))")


def _resolve_day(day, today):
    """Day of month without a month: this month when it has not passed, else next month."""
    y, m = today.year, today.month
    if day < today.day:
        m += 1
        if m == 13:
            y, m = y + 1, 1
    try:
        return date(y, m, day)
    except ValueError:
        return None


def _resolve_md(month, day, today, year=None):
    y = int(year) if year else today.year
    try:
        d = date(y, month, day)
    except ValueError:
        return None
    if not year and d < today - timedelta(days=60):
        d = date(y + 1, month, day)
    return d


def _next_weekday(wd, today):
    """The next such weekday strictly after today ("by Monday" said on a Sunday is tomorrow)."""
    return today + timedelta(days=(wd - today.weekday() - 1) % 7 + 1)


def friday_next_week(today):
    return today - timedelta(days=today.weekday()) + timedelta(days=11)


def find_dates(text, today):
    """[(date, start, end)] of every date in normalized text, resolved from today; "before <date>" is the day before."""
    found = []
    taken = []

    def free(s, e):
        return all(e <= a or s >= b for a, b in taken)

    for kind, pat in _DATE_PATTERNS:
        for m in pat.finditer(text):
            if not free(m.start(), m.end()):
                continue
            d = None
            if kind == "md":
                d = _resolve_md(_MONTHS[m.group(1)], int(m.group(2)), today, m.group(3))
            elif kind in ("dm", "dofm"):
                d = _resolve_md(_MONTHS[m.group(2)], int(m.group(1)), today, m.group(3))
            elif kind == "slash":
                a, b = int(m.group(1)), int(m.group(2))
                year = ("20" + m.group(3)[-2:]) if m.group(3) else None
                options = [x for x in (_resolve_md(a, b, today, year) if 1 <= a <= 12 else None,
                                       _resolve_md(b, a, today, year) if 1 <= b <= 12 else None) if x]
                near = [x for x in options if 0 < (x - today).days <= 120]
                d = (near or options or [None])[0]
            else:
                day = int(next(g for g in m.groups() if g))
                if 1 <= day <= 31:
                    d = _resolve_day(day, today)
            if d is None:
                continue
            taken.append((m.start(), m.end()))
            found.append([d, m.start(), m.end()])
    for pat, kind in _REL:
        for m in pat.finditer(text):
            if not free(m.start(), m.end()):
                continue
            d = {"eonw": friday_next_week(today),
                 "eotw": today - timedelta(days=today.weekday()) + timedelta(days=4 if kind == "eotw" else 0),
                 "tomorrow": today + timedelta(days=1), "after_tomorrow": today + timedelta(days=2)}[kind]
            if kind == "eotw" and d <= today:
                d = today + timedelta(days=(4 - today.weekday()) % 7 or 7)
            taken.append((m.start(), m.end()))
            found.append([d, m.start(), m.end()])
    explicit = bool(found)
    for m in _WD_ALONE.finditer(text):
        s, e = m.start(1), m.end(1)
        if explicit or not free(s, e):   # "next friday (nov 13)": the written date wins over the weekday
            continue
        wd = _WEEKDAYS[m.group(1)]
        d = _next_weekday(wd, today)
        if re.match(r"\s*(?:of\s+)?next week\b|\s*(?:de\s+)?la (?:proxima|otra) semana\b|\s*de la semana que viene\b",
                    text[e:e + 30]):
            d = today - timedelta(days=today.weekday()) + timedelta(days=7 + wd)   # "friday next week"
        taken.append((s, e))
        found.append([d, s, e])
    for f in found:
        if _BEFORE.search(text[max(0, f[1] - 30):f[1]]):
            f[0] = f[0] - timedelta(days=1)
    return [tuple(f) for f in sorted(found, key=lambda f: f[1])]


# ---------------------------------------------------------------------------------------------------------- facts

_STOP_PHRASES = re.compile(
    r"\b(?:stop|quit|cease) (?:texting|messaging|contacting|calling|emailing|sending|reaching|writing|bugging|bothering)\b"
    r"|\b(?:do not|don't|dont|never|no longer|please don't|pls don't|didn't|did not|wouldn't|would not) (?:text|message|"
    r"contact|call|reach out|reach|email|"
    r"write|send|bother)(?: (?:me|us|to me))?(?: (?:again|anymore|any more|ever|further|about this))\b"
    r"|\bnever (?:text|message|contact|call|reach out to|write to|email) (?:me|us)\b"
    r"|\b(?:don't|do not|dont) (?:want|need) (?:any )?(?:more )?(?:texts|messages|calls|emails)\b"
    r"|\b(?:take|remove|delete|drop|scratch) (?:me|us|my (?:number|name|info|contact(?: info)?)) (?:off|from)\b"
    r"|\boff (?:your|the) (?:list|contact list|mailing list|texting list)\b"
    r"|\b(?:lose|delete) my number\b|\bunsubscribe\b|\bopt(?:-| )?out\b|\bleave (?:me|us) alone\b"
    r"|\bno more (?:texts|messages|calls|contact)\b|\bstop all (?:texts|messages|contact)\b"
    # Spanish
    r"|\bno (?:(?:me|nos) )?(?:vuelvan a |vuelva a |vuelvas a )?(?:contacten|contacte|contactes|escriban|escriba|escribas|"
    r"llamen|llame|llames|manden|mande|mandes|envien|envie|envies|textees|texteen|molesten|moleste|"
    r"contactar|escribir|llamar|mandar|enviar|molestar)\b"
    r"|\bya no (?:me |nos )?(?:contacten|escriban|llamen|manden|envien|molesten|quiero (?:mas )?(?:mensajes|que me))\b"
    r"|\bdej(?:en|e|a) de (?:escribirme|escribirnos|mandarme|mandarnos|contactarme|contactarnos|llamarme|enviarme|"
    r"textearme|molestarme|escribir|mandar|contactar|llamar|enviar)\b"
    r"|\b(?:no quiero|no queremos) (?:recibir )?(?:mas )?(?:mensajes|textos|llamadas)\b"
    r"|\b(?:borren|borrenme|quiten|quitenme|saquen|saquenme|eliminen|eliminenme|sacar|quitar|borrar)(?:me)? "
    r"(?:de )?(?:su |la |tu )?lista\b|\bde su lista\b|\bdar(?:me)? de baja\b|\bdenme de baja\b"
    r"|\bno (?:quiero|queremos) que (?:me|nos) (?:sigan |vuelvan a )?(?:contacten|escriban|llamen|manden|envien|"
    r"molesten|contactando|escribiendo|llamando|mandando|enviando|molestando|contactar|escribir|llamar)\b"
    r"|\bno (?:me |nos )?(?:vuelvan?|vuelvas) a (?:contactar|escribir|llamar|mandar|enviar|molestar|textear)(?:me|nos)?\b"
    r"|\bno (?:contactarme|contactarnos|escribirme|escribirnos|llamarme|mandarme|enviarme|textearme|molestarme)\b"
    r"|\bno mas (?:mensajes|textos|llamadas)\b|\bbasta de (?:mensajes|textos|llamadas)\b"
    r"|\b(?:borr|elimin|quit)(?:en|a|e) mi (?:numero|contacto|telefono)\b"
    r"|\bstop a los mensajes\b|\bpara(?:r)? (?:los|con los) mensajes\b")
_STOP_UPPER = re.compile(r"(?<![A-Za-z])(?:STOP|UNSUBSCRIBE|QUIT|CANCEL)(?![A-Za-z])")


def read_stop(raw, text):
    if _STOP_UPPER.search(raw or ""):
        return True
    cleaned = _OPT_OUT_OF_ITEM.sub(" ", raw or "")
    if any(opt_out_status(part) == "opt_out" for part in [cleaned] + re.split(r"[.!?\n]+", cleaned) if part.strip()):
        return True
    return bool(_STOP_PHRASES.search(_OPT_OUT_OF_ITEM.sub(" ", text)))


# "opt out of the surge protector" leaves out a line item; it is not an opt-out of messages
_OPT_OUT_OF_ITEM = re.compile(r"(?i)\bopt(?:ing|ed)?[ -]?out of (?:the|that|this|your|an?) (?!(?:texts?|messages?|"
                              r"messaging|texting|list|calls?|emails?|contact|marketing|sms)\b)[a-z\- ]+")


_DECIDERS = [
    ("HOA", r"hoa|h\.o\.a\.?|homeowners'? association|homeowner'?s association|home owners association|"
            r"(?:the |our |my )(?:hoa |condo |association |co-op |coop )?board|condo board|asociacion(?: de propietarios| de vecinos)?|la junta|condo association"),
    ("landlord", r"landlord|landlady|property manager|property management|management company|property owner|owner of the (?:house|property|building)|"
                 r"casero|casera|dueno de la casa|duena de la casa|el dueno|la duena|arrendador|arrendadora"),
    ("husband", r"husband|hubby|esposo|marido"),
    ("wife", r"wife|wifey|esposa|mi mujer|missus|mrs|senora"),
    ("partner", r"partner|boyfriend|girlfriend|fiance|fiancee|pareja|novio|novia|better half|significant other"),
    ("in-laws", r"in-laws|in laws|mother-in-law|father-in-law|suegra|suegro|suegros"),
    ("spouse", r"spouse|conyuge"),
    ("dad", r"dad|father|papa|padre|pops|my old man"),
    ("mom", r"mom|mother|mama|madre|mum|ma"),
    ("parents", r"parents|padres|folks|papas"),
    ("brother", r"brother|hermano"),
    ("sister", r"sister|hermana"),
    ("son", r"son|hijo"),
    ("daughter", r"daughter|hija"),
    ("roommate", r"roommate|roomie|companero de casa|companera de casa"),
]
_DECIDER_RE = [(name, re.compile(rf"\b(?:my|mi|mis|our|nuestro|nuestra|nuestros|the|el|la|los)\s+(?:{pat})\b"))
               for name, pat in _DECIDERS]
_DECIDER_BARE = [(name, re.compile(rf"\b(?:{pat})\b")) for name, pat in _DECIDERS[:2]]  # "hoa", "landlord" alone


def read_decider(text):
    hits = []
    for name, rx in _DECIDER_RE + _DECIDER_BARE:
        m = rx.search(text)
        if m:
            hits.append((m.start(), name))
    if not hits:
        return None
    # "my mom" inside "my mom's house" (a place, not a person who must agree) is still a person; keep the first named
    return min(hits)[1]


_WEEKS = re.compile(
    r"\b(\d{1,2}|a couple of|a couple|couple of|couple|a few|few|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|un par de|par de|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)"
    r"(?:\s*(?:-|to|or|a|o)\s*(\d{1,2}|two|three|four|five|six|seven|eight|nine|ten|dos|tres|cuatro|cinco|seis|siete|"
    r"ocho|nueve|diez))?(?:-?ish)?\s*(?:more\s+|extra\s+|mas\s+)?(weeks?|wks?|semanas?|semanitas?)\b")
_ONE_WEEK = re.compile(r"\b(?:about|around|like|in|for|wait|maybe|roughly|approximately|approx|another|just|only|"
                       r"give it|give me|como|en|dentro de|unos?|unas?)\s+(?:a|one|una)\s+(?:week|semana)\b"
                       r"|\b(?:a|one|una) (?:week|semana) (?:from now|out|or so|later|de ahora|desde hoy|mas)\b"
                       r"|\bweek from now\b")
_MONTHS_WAIT = re.compile(
    r"\b(?:(a|one|an|un|una|1|another|otro)\s+(?:month|mes)(?:\s+(?:and a half|y medio))?|(\d{1,2}|two|three|four|five|six|"
    r"dos|tres|cuatro|cinco|seis|a couple of|a couple|couple of|un par de)\s+(?:months|meses))\b")
_NEXT_MONTH = re.compile(r"\b(?:next month|el (?:mes que viene|proximo mes)|el otro mes)\b")
_NEXT_WEEK_WAIT = re.compile(r"\b(?:until|till|til|wait(?: for| till| until)?|hold off(?: until| till)?|"
                             r"push it(?: to| out to)?|after|sometime|some time|hasta|despues de)\s+(?:sometime\s+)?"
                             r"(?:next week|la (?:proxima|otra) semana|la semana que viene)\b")
_NOT_WEEKS = re.compile(r"\b(?:next|this|last|past|that|end of the|end of next|esta|la proxima|la otra|la que viene)\s+$")


def _n(word):
    if word is None:
        return None
    if word.isdigit():
        return int(word)
    return _SMALL_WORDS.get(word)


def read_wait(text):
    """Whole weeks they want to wait, from "N weeks", "a couple of weeks", "a month", "two months" (Spanish too)."""
    for m in _WEEKS.finditer(text):
        if _NOT_WEEKS.search(text[max(0, m.start() - 14):m.start()]):
            continue
        a, b = _n(m.group(1)), _n(m.group(2))
        if a is None:
            continue
        n = max(a, b) if b else a
        # "within 2 weeks"/"in the next 2 weeks" is a deadline, not a wait
        if re.search(r"\b(?:within|in the next|no later than|before|dentro de las proximas)\s+$",
                     text[max(0, m.start() - 22):m.start()]):
            continue
        return n if 1 <= n <= 52 else None
    m = _ONE_WEEK.search(text)
    if m and not re.search(r"\b(?:within|before)\s+$", text[max(0, m.start() - 10):m.start()]):
        return 1
    if re.search(r"\b(?:un )?mes y medio\b|\ba month and a half\b", text):
        return 6
    m = _MONTHS_WAIT.search(text)
    if m:
        if m.group(1):
            return 6 if re.search(r"and a half|y medio", m.group(0)) else 4
        n = _n(m.group(2))
        return 4 * n if n else None
    if _NEXT_MONTH.search(text):
        return 4
    if _NEXT_WEEK_WAIT.search(text):
        return 1
    return None


_READY = re.compile(
    r"\bwhen (?:can|could|will|would|do|are)\b[^.?!]{0,40}?\b(?:come|start|get (?:here|out|started|this done|"
    r"it done|going)|be (?:here|out|over)|schedule|show up|fit (?:me|us) in|begin|book)\b"
    r"|\bwhen (?:the |your |you |ur |y'all |you all |your guys |the guys )?(?:crew|team|guys|you|they|someone|workers?|"
    r"installers?|techs?|technicians?|people|folks)\s+(?:can|could|would|will|are able to|are free to|might)\s+"
    r"(?:\w+\s+)?(?:come|start|get (?:here|out|started)|be (?:here|out|over)|show up|fit (?:me|us) in|begin|do it)\b"
    r"|\bwhen (?:are|is) (?:you|your crew|the crew|your team|y'all|you guys) (?:free|available|open)\b"
    r"|\bwhen(?:'s| is) (?:the )?(?:earliest|soonest|first|next)\b"
    r"|\b(?:earliest|soonest|first|next) (?:available|opening|slot|date|day|time|availability|opening)\b"
    r"|\bwhat (?:days?|dates?|times?) (?:work|works|do you have|are open|are available|can you come)\b"
    r"|\b(?:book|schedule|lock|sign) (?:it|this|me|us|the job|that|the work|the install|the repair)?\s*(?:in|up|on)?\s*"
    r"(?:now|today|asap|right away|right now|ya|ahora)\b"
    r"|\b(?:want|wanna|ready|like|love|need|would like|i'd like|we'd like|let's|lets|please|can we|could we|can you|"
    r"could you|go ahead and|gonna|going to)\s+(?:to\s+)?(?:go ahead and\s+)?(?:book|lock in|reserve)\b"
    r"|^(?:please |ok,? |okay,? |great,? |sounds good,? )?book\b|\bplease book\b|\bbook (?:it|this|me|us) (?:in|for)\b"
    r"|\bbook (?:us|me) in\b|\bbook it\b(?! (?:with|if))|\bsign (?:me|us) up\b"
    r"|\bput (?:me|us) (?:on|in) (?:the|your) (?:schedule|calendar|books)\b"
    r"|\blet'?s (?:do it|do this|go|book|get (?:it|this) (?:done|booked|scheduled|on the calendar))\b"
    r"|\b(?:go ahead and|ready to) (?:book|schedule|start|do it)\b"
    # Spanish / Spanglish
    r"|\bcuando (?:puede|pueden|podrian|podria|vienen|viene|podemos|pudieran|llegan|empiezan|comienzan|tienen "
    r"(?:espacio|disponibilidad|cupo)|hay (?:espacio|disponibilidad|cupo))\b"
    r"|\bque (?:dia|dias|fecha|fechas) (?:pueden|puede|vienen|tienen|hay)\b"
    r"|\bcuando (?:\w+ ){1,2}(?:puede|pueden|podrian|podria|vienen|viene)\b|\bcuando tienen disponible\b"
    r"|\b(?:agendalo|agendenlo|agendelo|reservalo|reservenlo|programalo|programenlo|apartalo)\b"
    r"|\bhow soon (?:can|could)\b|\b(?:your|the crew's|the team's) availability\b"
    r"|\bget (?:me|us|it|this) (?:on|in) (?:the|your) (?:schedule|calendar|books)\b"
    r"|\bsend (?:the |your |a )?(?:crew|team|guys|someone|tech)\b"
    r"|\b(?:quiero|queremos|quisiera|quisieramos|me gustaria|nos gustaria|vamos a)\s+(?:agendar|reservar|programar|"
    r"apartar|book|schedule|hacer la cita|que vengan)\b"
    r"|\b(?:agendemos|reservemos|hagamoslo|dale|vamos con eso|vamos a hacerlo|adelante con la cita)\b"
    r"|\bbook it ya\b|\bbook it now\b")
_NOT_READY = re.compile(r"\b(?:before|until|till|til|antes de(?: que)?|hasta que)\s+(?:i|we|yo|nosotros)?\s*"
                        r"(?:can |could |)?(?:book|schedule|lock|commit|agendar|reservar)")
_STRONG_READY = re.compile(r"\b(?:book|schedule)\b[^.?!]{0,12}\b(?:now|today|asap)\b|\bwhen (?:can|could)\b[^.?!]{0,40}"
                           r"\bcome\b|\bcuando (?:puede|pueden)\b")


def read_ready(text):
    if not _READY.search(text):
        return False
    if _NOT_READY.search(text) and not _STRONG_READY.search(text):
        return False
    return True


# ---- line items -------------------------------------------------------------------------------------------------

_SYNONYMS = {  # a word of an optional item's name -> other ways people say it (English and Spanish)
    "surge": ["surge", "sobretension", "sobrevoltaje", "picos de voltaje", "picos de corriente", "picos", "spike",
              "power spike", "spike protector", "surge suppressor"],
    "protector": ["protector"],
    "protection": ["protection", "proteccion"],
    "maintenance": ["maintenance", "upkeep", "tune-up", "tune up", "tuneup", "tune-ups", "service plan", "yearly plan",
                    "annual plan", "yearly service", "annual service", "check-up plan", "checkup plan", "check-ups",
                    "checkups", "yearly visits", "yearly visit", "annual visit", "the plan", "that plan", "this plan",
                    "mantenimiento", "maintenance plan", "membership", "service agreement", "service contract",
                    "plan anual", "el plan", "ese plan"],
    "thermostat": ["thermostat", "termostato", "nest", "ecobee", "smart thing", "wifi thing", "smart one"],
    "purifier": ["purifier", "purificador", "purification", "purificacion", "uv light", "uv lamp", "uv thing", "uv",
                 "air cleaner", "air scrubber", "air cleaning", "air quality", "germ", "germ killer", "germ-killing",
                 "luz uv", "luz ultravioleta", "ultraviolet", "purificador de aire"],
    "expansion": ["expansion", "expansion tank", "tanque de expansion", "tanque", "tank", "tanquecito"],
    "shutoff": ["shutoff", "shut-off", "shut off", "main valve", "valvula de cierre", "llave de paso",
                "valvula de paso", "valvula principal", "cutoff", "cut-off", "master valve"],
    "valve": ["valve", "valvula", "llave"],
    "gutter": ["gutter", "gutters", "canaleta", "canaletas", "canalon", "canalones", "desague"],
    "cleaning": ["cleaning", "clean out", "cleanout", "clean-out", "limpieza"],
    "guards": ["guards", "guard", "protectores de canaleta", "cubiertas", "mallas", "screens", "covers", "leaf filter",
               "leaf", "leaves", "hojas"],
    "ridge": ["ridge", "cumbrera"],
    "vent": ["vent", "vents", "venting", "ventilation", "ventilacion", "ventila", "respiradero", "rejilla"],
    "load": ["load management", "load manager", "load module", "load", "power-sharing", "power sharing",
             "power management", "power thing", "power gadget", "power module", "power splitter", "power manager",
             "energy management", "load balancing", "load-sharing", "load sharing", "power", "manejo de carga",
             "gestion de carga", "control de carga", "modulo de carga", "carga"],
    "management": ["management"],
    "module": ["module", "modulo"],
}
_GENERIC = {"whole", "home", "air", "the", "and", "plan", "smart", "annual", "uv", "of", "a", "optional", "extra"}
# "no" said before the item ("don't need the thermostat", "sin el purificador", "skip the vent")
_PRE_NEG = re.compile(
    r"(?:don't|do not|dont|doesn't|does not|won't|will not|didn't|wouldn't|would not) (?:really |even |actually |"
    r"think (?:i|we) (?:really )?|see (?:the )?)?(?:need|want|care for|require|use|bother with|go for|do|add|include|"
    r"install|put in|get)\b|\bstrike\b|\bdejen? fuera\b|\bdeja fuera\b"
    r"|\bno need (?:for|of)?\b|\bnot (?:need|want|interested in|including|include|doing|do|bothering with)\b"
    r"|\bwithout\b|\bskip\b|\bskipping\b|\bdrop\b|\bdropping\b|\bremove\b|\bremoving\b|\btake (?:out|off)\b"
    r"|\btake (?:it|that|them|this|the)\b|\bleave (?:out|off)\b|\bleave (?:the|that|it)\b|\bpass on\b|\bnix\b"
    r"|\bcut\b|\bget rid of\b|\bexclude\b|\bminus\b|\bforget (?:about )?\b|\bno thanks (?:to|on)\b|\bhold off on\b"
    r"|\bnot (?:the|that|those|this|any)\b|\bno\b(?! (?:worries|worry|problem|rush|hurry|pressure|prisa|apuro|pasa nada|te preocupes|later than|more than|sooner))|\bgood without\b|\bfine without\b|\bok without\b|\bnor\b"
    r"|\bcan do without\b|\bnot interested in\b|\bnot sure (?:we|i) need\b|\bdon't bother with\b|\bno (?:extra|optional)\b"
    r"|\bknock (?:off|out)\b|\bknock (?:the|that|it)\b|\bexcept\b|\bexcluding\b|\bforgo\b|\bforego\b|\brather not\b"
    r"|\bdecline\b|\bdeclining\b|\bopt out of\b|\bopting out of\b|\bscratch\b|\bditch\b|\blose the\b|\bexcepto\b"
    # Spanish
    r"|\bno (?:lo |la |los |las |le |me |nos )?(?:quiero|queremos|necesito|necesitamos|hace falta|hacen falta|me interesa|"
    r"nos interesa|ocupo|ocupamos|incluyan|pongan|instalen)\b|\bsin\b|\bni\b|\bquit(?:a|en|ar|ame|enme|enle|ale)\b"
    r"|\bsaca(?:r|n|me|nme|le)?\b|\belimin(?:a|en|ar)\b|\bborr(?:a|en|ar)\b|\bmenos\b")
# "no" said after the item ("the purifier we don't need", "the module isn't needed", "el tanque no lo necesito")
_POST_NEG = re.compile(
    r"^[^.!?]{0,30}?(?:\b(?:we|i) (?:don't|do not|dont|won't) (?:really )?(?:need|want)\b|\bisn't (?:needed|necessary)\b"
    r"|\bis not (?:needed|necessary)\b|\baren't (?:needed|necessary)\b|\bnot (?:needed|necessary|wanted|required)\b"
    r"|\bcan go\b|\bcan be (?:removed|dropped|skipped|taken off|left off|left out)\b|\bno thanks\b|\bno thank you\b"
    r"|\b(?:we|i) can skip\b|\bwe'll skip\b|\bi'll skip\b|\bwe'll pass\b|\bi'll pass\b|\b(?:off|out) (?:of )?(?:the|my|our) "
    r"(?:estimate|quote|bill|total|list)\b|\bno (?:lo|la|los|las) (?:quiero|queremos|necesito|necesitamos)\b"
    r"|\bno hace(?:n)? falta\b|\bno, gracias\b|\bno gracias\b|\bunnecessary\b|\bnot interested\b)")
_POSITIVE_RE = re.compile(r"(?<!no )(?<!not )(?<!n't )(?<!nor )(?<!ni )\b(?:keep|want|wants|include|add|with|go ahead with|quiero|queremos|con|y el|y la)\b")
_CLAUSE_SPLIT = re.compile(r"[.!?;\n]+|\s-\s|,?\s+(?:but|pero|however|though|aunque|also|ademas|and also|y tambien)\s+")


def item_keys(item, required_names):
    """Words and phrases that name this optional item, minus words shared with the job's required items."""
    req_words = set()
    for r in required_names:
        req_words |= set(re.findall(r"[a-z]+", normalize(r)))
    words = [w for w in re.findall(r"[a-z]+", normalize(item)) if w not in _GENERIC and len(w) > 2]
    keys = {normalize(item)}
    for w in words:
        keys.update(normalize(s) for s in _SYNONYMS.get(w, []))
        if w not in req_words:
            keys.add(w)
    return {k for k in keys if k and k not in req_words}


def _positions(clause, keys):
    """(start, end) of every mention of the item in the clause (longest key first, no overlaps)."""
    spans = []
    for k in sorted(keys, key=len, reverse=True):
        for m in re.finditer(rf"(?<![a-z]){re.escape(k)}s?(?![a-z])", clause):
            if all(m.end() <= a or m.start() >= b for a, b in spans):
                spans.append((m.start(), m.end()))
    return sorted(spans)


_LIST_GAP = re.compile(r"[\s,]*(?:[a-z'\-]+\s+){0,2}?(?:or|and|nor|ni|o|y|&|/|plus|as well as|or even)?[\s,]*(?:(?:the|el|la|los|las|that|"
                       r"those|any|ni|also|tambien)\s+)?(?:[a-z'\-]+\s+){0,2}?")


def read_drops(text, items):
    """Optional items they say they do not want: the item named (or described) with a "no" right before it in the same
    clause ("don't need the thermostat", "sin el purificador") or right after it ("the module isn't needed")."""
    optional = [i["name"] for i in items if i["optional"]]
    if not optional:
        return []
    required = [i["name"] for i in items if not i["optional"]]
    keys = {name: item_keys(name, required) for name in optional}
    out = set()
    for c in (c for c in _CLAUSE_SPLIT.split(text) if c and c.strip()):
        mentions = sorted((s, e, n) for n in optional for s, e in _positions(c, keys[n]))
        if not mentions:
            continue
        for idx, (s, e, n) in enumerate(mentions):
            prev_end = mentions[idx - 1][1] if idx else 0
            if idx and mentions[idx - 1][2] != n:
                # another item sits in between: only the stretch after it counts, unless a list joins them
                # ("no thermostat or purifier", "ni el termostato ni el purificador")
                gap = c[prev_end:s]
                if _LIST_GAP.fullmatch(gap) and not _POSITIVE_RE.search(gap) and not (
                        _PRE_NEG.search(gap) and not re.fullmatch(r"[\s,]*(?:nor|ni)\s+(?:(?:the|el|la|los|las)\s+)?", gap)):
                    if n not in out and mentions[idx - 1][2] in out:
                        out.add(n)
                    continue
                window = c[prev_end:s]
            else:
                window = c[max(0, s - 60):s]
            pre = list(_PRE_NEG.finditer(window))
            if pre and not _POSITIVE_RE.search(window, pre[-1].end()):
                out.add(n)
                continue
            if _POST_NEG.match(c[e:]):
                out.add(n)
    return sorted(out)


# ---- amounts ----------------------------------------------------------------------------------------------------

_UNIT_AFTER = re.compile(r"\s*(?:-\s*)?(?:ton|tons|gallon|gallons|gal|amp|amps|a\b|v\b|volt|volts|weeks?|wks?|days?|"
                         r"semanas?|dias?|meses|months?|years?|anos?|hours?|horas?|%|percent|por ciento|sq|square|ft|"
                         r"feet|pies|outlets?|st\b|nd\b|rd\b|th\b|am\b|pm\b|o'clock|minutes?)")
_CUE = re.compile(r"(?:max|maximum|most|up to|upto|go over|go above|more than|over|above|cap|capped|quoted|quote|"
                  r"budget|afford|pay|paying|spend|keep it at|keep it under|keep it to|do it for|can do|could do|"
                  r"willing|offer|offering|match|all-in|all in|ceiling|limit|top|under|tops|no more than|at most|"
                  r"maximo|pagar|pago|cotizo|cotizaron|presupuesto|hasta|ofrezco|tope|limite|gastar|mas de)\W*"
                  r"(?:\w+\W+){0,3}$")
_CUE_AFTER = re.compile(r"\s*(?:dollars|bucks|usd|dolares)?\s*(?:,\s*)?(?:(?:is|would be|will be|es|seria|as)\s+)?"
                        r"(?:(?:my|the|our|mi|el|mis|absolute|tops|at most)\s+)*(?:max|maximum|limit|budget|ceiling|top|"
                        r"tops|cap|tope|limite|maximo|most|best i can|all-in|all in|todo incluido|or less|and not a "
                        r"penny more|is all|is what i can)\b")
_NEG_CUE = re.compile(r"(?:can't|cannot|can not|won't|not)\s+(?:do|pay|afford|spend)\s*$")


def reference_prices(items):
    """Prices the customer may quote back as a reference: every line item and every package total."""
    opt = [i for i in items if i["optional"]]
    base = sum(i["price"] for i in items if not i["optional"])
    refs = {round(i["price"], 2) for i in items}
    for k in range(len(opt) + 1):
        for keep in combinations(opt, k):
            refs.add(round(base + sum(i["price"] for i in keep), 2))
    return refs


def read_ceiling(text, items, spans):
    """The most they say they will pay (incl. another company's all-in price), or None.

    Candidates: digit amounts with or without "$" ("$1,850", "1850", "2k") and amounts in words ("four hundred",
    "nineteen hundred"), outside dates, week counts and unit numbers ("3-ton", "40-gallon", "200A"). A number equal to
    a line item or a package total is a reference to the estimate, unless a ceiling word stands right before it
    ("max", "can't go over", "quoted" ...)."""
    def blocked(s, e):
        return any(not (e <= a or s >= b) for a, b in spans)

    refs = reference_prices(items)
    cands = []
    for v, s, e, dollar in digit_amounts(text):
        if blocked(s, e):
            continue
        if _UNIT_AFTER.match(text[e:]) and not dollar:
            continue
        if re.search(r"[\d/]$|\d[-/]$", text[max(0, s - 2):s]):   # part of 11/10 or 3-4
            continue
        if not dollar and v < 50:
            continue
        cands.append((v, s, e, dollar))
    digit_spans = [(s, e) for _, s, e, _ in cands]
    for v, s, e in word_amounts(text):
        if blocked(s, e) or v < 50 or any(not (e <= a or s >= b) for a, b in digit_spans):
            continue
        if _UNIT_AFTER.match(text[e:]):
            continue
        cands.append((v, s, e, False))
    good = []
    for v, s, e, dollar in cands:
        before = text[max(0, s - 40):s]
        after = text[e:e + 30]
        cue = (bool(_CUE.search(before)) and not _NEG_CUE.search(before)) or bool(_CUE_AFTER.match(after))
        too_much = re.search(r"^\s*(?:is|es|seems|feels|sounds|esta)\s+(?:too|a bit|a little|way|kind of|kinda|"
                             r"more than|over|above|out of|beyond|demasiado|mucho|muy|caro|steep|high|a lot)", after)
        if too_much:
            continue
        if round(v, 2) in refs and not cue:
            continue
        good.append((cue, v, s))
    if not good:
        return None
    cued = [g for g in good if g[0]]
    pool = cued or good
    return min(g[1] for g in pool)


# ---------------------------------------------------------------------------------------------------------- reader

def extract(inp):
    """Facts dict from one reader input (`readers.reader_input`)."""
    raw = inp["text"] or ""
    text = normalize(raw)
    today = date.fromisoformat(inp["today"])
    if read_stop(raw, text):
        return {"stop": True, "ceiling_usd": None, "drop_items": [], "need_by": None, "wait_weeks": None,
                "decider": None, "ready": False}
    dates = find_dates(text, today)
    spans = [(s, e) for _, s, e in dates]
    spans += [(m.start(), m.end()) for m in _WEEKS.finditer(text)]
    spans += [(m.start(), m.end()) for m in _MONTHS_WAIT.finditer(text)]
    future = [d for d, _, _ in dates if d > today]
    need_by = min(future).isoformat() if future else None
    wait = read_wait(text)
    decider = read_decider(text)
    ready = read_ready(text) and wait is None and decider is None
    return {"stop": False, "ceiling_usd": read_ceiling(text, inp["items"], spans),
            "drop_items": read_drops(text, inp["items"]), "need_by": need_by, "wait_weeks": wait,
            "decider": decider, "ready": ready}


def _meta(name, t0, error=None, kind=None):
    return {"reader": name, "model": None, "seconds": round(time.perf_counter() - t0, 6), "usd": 0.0, "attempts": 1,
            "error": error, "error_kind": kind}


class RuleReader:
    """The hardened rulebook reader (baseline arm "rules")."""
    name = "rules"

    def read(self, case):
        t0 = time.perf_counter()
        inp = reader_input(case)
        try:
            facts = parse(extract(inp), optional_items(inp))
        except FactsError as e:
            return None, _meta(self.name, t0, str(e), "invalid")
        return facts, _meta(self.name, t0)


# ---------------------------------------------------------------------------------------------------------- start point

class SmartRulesPatternReader:
    """The plain starting point: SmartRules' own patterns mapped onto Facts, before any hardening. Stop is the shared
    opt-out layer; a ceiling is the largest "$" amount below the estimate total that is not a line-item price; an
    item is dropped when a "not needed / don't need / skip ..." phrase and its exact name are both in the reply; weeks
    and "later" phrases give wait_weeks; the first family word is the decider; ready words give ready. SmartRules has
    no date reading ("sooner" words pick the earliest slot), so need_by is never set."""
    name = "smart_rules_patterns"

    def read(self, case):
        from second_ask.tournament import policies as P
        t0 = time.perf_counter()
        inp = reader_input(case)
        raw = inp["text"] or ""
        text = raw.translate(str.maketrans({"’": "'", "‘": "'"})).lower()
        if opt_out_status(raw) == "opt_out":
            f = {"stop": True}
        else:
            visible = {i["price"] for i in inp["items"]}
            asks = [a for a in amounts_in(text) if a < inp["total"] and a not in visible]
            drops = []
            if P._NOT_NEEDED.search(text):
                drops = [n for n in optional_items(inp) if n.lower() in text]
            wait = None
            w = P._WEEKS.search(text)
            if w:
                n = w.group(1)
                wait = int(n) if n.isdigit() else P._NUMBER_WORDS[n]
            else:
                days = next((d for pat, d in P._LATER.items() if re.search(pat, text)), None)
                wait = round(days / 7) if days else None
            fam = P._FAMILY.search(text)
            decider = None
            if fam:
                decider = {"hoa": "HOA", "mother": "mom", "father": "dad"}.get(fam.group(1), fam.group(1))
            f = {"stop": False, "ceiling_usd": max(asks) if asks else None, "drop_items": drops, "need_by": None,
                 "wait_weeks": wait or None, "decider": decider, "ready": bool(P._READY.search(text))}
        try:
            facts = parse(f, optional_items(inp))
        except FactsError as e:
            return None, _meta(self.name, t0, str(e), "invalid")
        return facts, _meta(self.name, t0)
