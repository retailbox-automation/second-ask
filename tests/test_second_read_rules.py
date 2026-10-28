"""The hardened rule reader (second_read/readers/rules.py): hard cases written from the reading conventions
(second_read/data.py CONVENTIONS), in our own words, plus a few dev lines as examples."""
import json
from datetime import date
from pathlib import Path

import pytest

from second_read import cases as C
from second_read.contract import Facts
from second_read.readers.rules import (RuleReader, SmartRulesPatternReader, find_dates, normalize, read_wait,
                                       word_amounts)

TODAY = "2026-11-04"  # a Wednesday
CARDS = {  # one card per job, by what its optional items are
    "capacitor": "B001", "shower": "B002", "ac": "B003", "gfci": "B004", "chimney": "B013", "shingles": "B015",
    "ev": "B018", "repipe": "B024", "roof": "B025", "furnace": "B027", "heater": "B029", "panel": "B031",
}


def case(job, text, today=TODAY):
    card_id = CARDS[job]
    card = C.card_for({"id": "t", "card_id": card_id})
    return {"id": "t", "card_id": card_id, "today": today, "weekday": date.fromisoformat(today).strftime("%A"),
            "quoted_slot": card.estimate().default_slot.start.date().isoformat(), "text": text}


def read(job, text, today=TODAY):
    facts, meta = RuleReader().read(case(job, text, today))
    assert meta["model"] is None and meta["error"] is None and meta["seconds"] >= 0
    return facts


def only(**kw):
    return Facts(**{k: (tuple(v) if k == "drop_items" else v) for k, v in kw.items()})


# ---------------------------------------------------------------------------------------------------------- stop

@pytest.mark.parametrize("text", [
    "STOP", "Stop.", "stop texting me", "Please take me off your list.", "please lose my number, not interested",
    "Don't text me again about this.", "never contact me again", "unsubscribe", "remove my number please",
    "ya no me escriban", "No me contacten nunca más.", "dejen de mandarme mensajes por favor",
    "no quiero que me sigan escribiendo", "Por favor, STOP a los mensajes", "no vuelvan a contactarme",
    "Thanks, but we went another way. Please don't reach out anymore.",
])
def test_stop_any_wording(text):
    assert read("ac", text) == only(stop=True)


def test_stop_wins_over_everything_else():
    f = read("ac", "STOP texting me. I don't need the thermostat and $9,000 is my max anyway.")
    assert f == only(stop=True)


@pytest.mark.parametrize("text", [
    "I don't want to stop the project, just push it a couple of weeks.",
    "The end of next week works for us.",
    "No need for the thermostat.",
])
def test_not_a_stop(text):
    assert read("ac", text).stop is False


# ---------------------------------------------------------------------------------------------------------- amounts

@pytest.mark.parametrize("job,text,want", [
    ("gfci", "I can do four hundred tops.", 400.0),
    ("ev", "nineteen hundred is the most I'll spend", 1900.0),
    ("ev", "my budget for this is 2k", 2000.0),
    ("ev", "another outfit quoted 1,850 all in, I'd go with whoever matches it", 1850.0),
    ("ev", "Otra compañía me cotizó 1800 dólares todo incluido.", 1800.0),
    ("ev", "lo máximo que puedo pagar es mil ochocientos", 1800.0),
    ("ev", "could you do eighteen fifty?", 1850.0),
    ("gfci", "I can't do $450, $420 is my max", 420.0),
    ("gfci", "450 is too much for me, I can't go over 400", 400.0),
    ("ev", "$1.5k is all I have for it", 1500.0),
])
def test_ceiling(job, text, want):
    assert read(job, text).ceiling_usd == want


@pytest.mark.parametrize("job,text", [
    ("ac", "We want the 3-ton system done by Nov 20."),
    ("heater", "The 40-gallon heater has to be in by the 18th."),
    ("panel", "Let's book the 200A panel swap, when can you come?"),
    ("gfci", "I'd like to book the $450 job now, when can the crew come?"),   # quoting the total is not a limit
])
def test_numbers_that_are_not_a_ceiling(job, text):
    assert read(job, text).ceiling_usd is None


def test_word_amounts():
    got = {t: word_amounts(normalize(t))[0][0] for t in
           ("four hundred", "nineteen hundred", "two thousand five hundred", "two grand", "twelve hundred and fifty",
            "cuatrocientos cincuenta", "mil quinientos")}
    assert got == {"four hundred": 400, "nineteen hundred": 1900, "two thousand five hundred": 2500,
                   "two grand": 2000, "twelve hundred and fifty": 1250, "cuatrocientos cincuenta": 450,
                   "mil quinientos": 1500}


# ---------------------------------------------------------------------------------------------------------- dates

@pytest.mark.parametrize("text,want", [
    ("it has to be done by Friday", "2026-11-06"),
    ("needs to be finished by Monday", "2026-11-09"),
    ("can you get it done before the 14th?", "2026-11-13"),        # "before the 14th" means the 13th
    ("it must be done by the 12th", "2026-11-12"),
    ("we need it by the end of next week", "2026-11-13"),          # Friday of next week
    ("hard deadline Nov 25", "2026-11-25"),
    ("by Tuesday, November 17th please", "2026-11-17"),
    ("done by 11/18", "2026-11-18"),
    ("by the 2nd at the latest", "2026-12-02"),                     # a day already past this month is next month's
    ("tiene que estar listo para el miércoles 25 de noviembre", "2026-11-25"),
    ("antes del 20 de noviembre por favor", "2026-11-19"),
    ("lo necesito para el viernes", "2026-11-06"),
    ("needs to be done by thursday the 12th", "2026-11-12"),
    ("need it done tomorrow if possible", "2026-11-05"),
])
def test_need_by(text, want):
    assert read("shower", text).need_by == want


def test_weekday_with_month_is_one_date():
    assert [d.isoformat() for d, _, _ in find_dates(normalize("by sunday nov 22"), date(2026, 11, 8))] == ["2026-11-22"]


# ---------------------------------------------------------------------------------------------------------- waits

@pytest.mark.parametrize("text,want", [
    ("we want it, just not for a couple of weeks", 2), ("about a month from now works", 4),
    ("can we do it in about two months?", 8), ("let's wait like 6 weeks", 6), ("three weeks out please", 3),
    ("en unas tres semanas", 3), ("dentro de un mes", 4), ("no hasta como en 5 semanas", 5), ("en dos meses", 8),
    ("hold off about a week", 1), ("not until next week", 1),
])
def test_wait_weeks(text, want):
    assert read_wait(normalize(text)) == want


def test_end_of_next_week_is_a_date_not_a_wait():
    f = read("shower", "by the end of next week please")
    assert f.wait_weeks is None and f.need_by == "2026-11-13"


# ---------------------------------------------------------------------------------------------------------- items

@pytest.mark.parametrize("job,text,want", [
    ("ac", "we can do without the wifi thermostat", ["Smart thermostat"]),
    ("ac", "keep the thermostat but skip the UV light", ["UV air purifier"]),
    ("ac", "we want the thermostat, not the purifier", ["UV air purifier"]),
    ("ac", "sin el termostato ni el purificador", ["Smart thermostat", "UV air purifier"]),
    ("furnace", "skip the yearly tune-up plan", ["Annual maintenance plan"]),
    ("heater", "don't need the extra tank", ["Expansion tank"]),
    ("chimney", "leave off the gutter clean-out", ["Gutter cleaning"]),
    ("roof", "no leaf guards please", ["Gutter guards"]),
    ("shingles", "we'll pass on the roof vent", ["Ridge vent"]),
    ("ev", "the power-sharing box isn't needed", ["Load management module"]),
    ("repipe", "no necesito la válvula de cierre", ["Whole-home shutoff valve"]),
    ("panel", "sin la protección contra picos", ["Whole-home surge protection"]),
    ("capacitor", "the surge thing we don't need", ["Surge protector"]),
    ("repipe", "We'd like to go ahead with the repipe and the shutoff valve.", []),
])
def test_drop_items(job, text, want):
    assert list(read(job, text).drop_items) == want


# ---------------------------------------------------------------------------------------------------------- deciders

@pytest.mark.parametrize("text,want", [
    ("gotta run it by the wife first", "wife"), ("mi esposo tiene que aprobarlo", "husband"),
    ("the HOA board has to approve it", "HOA"), ("my landlord needs to OK this", "landlord"),
    ("necesito hablarlo con mi mamá", "mom"), ("the property manager has to sign off", "landlord"),
    ("my partner and I need to talk it over", "partner"), ("tengo que preguntarle a mi papá", "dad"),
])
def test_decider(text, want):
    assert read("ac", text).decider.lower() == want.lower()


# ---------------------------------------------------------------------------------------------------------- ready

@pytest.mark.parametrize("text", [
    "when can your guys start?", "what's your earliest opening?", "¿cuándo pueden venir?", "let's book it",
    "book me in for the soonest slot", "Quiero agendar ya", "go ahead and book it, when can the crew come out?",
])
def test_ready(text):
    assert read("ac", text).ready is True


@pytest.mark.parametrize("text", [
    "I need to check with my wife before I can book",            # someone else must agree first
    "can you get it done before the 20th?",                       # a deadline alone is not ready
    "we want it but in 3 weeks, can you schedule it for then?",   # a wait is not ready
    "the most I can pay is $9,000",                               # a price limit alone is not ready
    "another company quoted $9,000 all-in, I'd book with whoever does it for that",
])
def test_not_ready(text):
    assert read("ac", text).ready is False


# ---------------------------------------------------------------------------------------------------------- together

def test_several_facts_in_one_reply():
    f = read("ev", "skip the load manager, 1500 max, need it by the 13th, and my wife has to agree")
    assert f == Facts(False, 1500.0, ("Load management module",), "2026-11-13", None, "wife", False)


def test_spanglish_several_facts():
    f = read("ac", "No queremos el purifier. Lo máximo es $9,000 y mi husband tiene que approve, ok?")
    assert f == Facts(False, 9000.0, ("UV air purifier",), None, None, "husband", False)


def test_ready_with_deadline_and_ceiling():
    f = read("gfci", "Book it now — the most I can pay is $420, needs to be done by Tuesday the 10th. When can the "
                     "crew come?")
    assert f == Facts(False, 420.0, (), "2026-11-10", None, None, True)


# ---------------------------------------------------------------------------------------------------------- dev examples

DEV = Path(__file__).resolve().parents[1] / "data" / "second_read" / "dev.jsonl"


@pytest.mark.parametrize("line_id", ["dev-0001", "dev-0005", "dev-0043", "dev-0119", "dev-0164"])
def test_dev_examples(line_id):
    line = next(c for c in C.load(DEV) if c["id"] == line_id)
    facts, _ = RuleReader().read(line)
    gold = line["gold"]
    got = facts.to_dict()
    assert {k: (v.lower() if isinstance(v, str) and k == "decider" else v) for k, v in got.items()} == \
        {k: (v.lower() if isinstance(v, str) and k == "decider" else v) for k, v in gold.items()}


def test_reader_never_sees_gold():
    line = json.loads(DEV.read_text().splitlines()[0])
    a, _ = RuleReader().read(line)
    stripped = {k: line[k] for k in ("id", "card_id", "today", "weekday", "quoted_slot", "text")}
    b, _ = RuleReader().read(stripped)
    assert a == b


def test_start_point_reader_runs():
    facts, meta = SmartRulesPatternReader().read(case("ac", "please stop texting me"))
    assert facts == only(stop=True) and meta["model"] is None


# ---------------------------------------------------------------------------------------------------------- more wordings

@pytest.mark.parametrize("job,text,field,want", [
    ("ac", "nine thousand two fifty is my ceiling", "ceiling_usd", 9250.0),
    ("ac", "could you do it for 9 thousand?", "ceiling_usd", 9000.0),
    ("ac", "otra empresa me lo deja en nueve mil doscientos cincuenta", "ceiling_usd", 9250.0),
    ("shower", "lo necesito para el 6/11", "need_by", "2026-11-06"),     # day/month, the way Spanish speakers write
    ("shower", "we'd rather wait a month and a half", "wait_weeks", 6),
    ("shower", "en mes y medio", "wait_weeks", 6),
    ("ac", "need the missus to sign off first", "decider", "wife"),
    ("ac", "the condo board has to approve", "decider", "HOA"),
    ("ac", "¿qué fecha tienen disponible?", "ready", True),
    ("ac", "how soon can you come out?", "ready", True),
    ("ac", "agéndalo ya porfa", "ready", True),
    ("ac", "everything except the purifier please", "drop_items", ("UV air purifier",)),
    ("ac", "we'd rather not get the thermostat", "drop_items", ("Smart thermostat",)),
    ("capacitor", "we'd like to opt out of the surge protector", "drop_items", ("Surge protector",)),
    ("capacitor", "we'd like to opt out of the surge protector", "stop", False),
    ("ac", "I'd appreciate it if you didn't contact me again", "stop", True),
    ("ac", "borren mi número por favor", "stop", True),
    ("ac", "no rush, we want the thermostat and the AC", "drop_items", ()),
    ("shower", "has to be done by the nineteenth", "need_by", "2026-11-19"),
    ("shower", "by november twenty-first", "need_by", "2026-11-21"),
    ("shower", "para el quince de noviembre", "need_by", "2026-11-15"),
    ("shower", "by next friday (nov 13)", "need_by", "2026-11-13"),     # the written date wins over the weekday
    ("shower", "by friday next week", "need_by", "2026-11-13"),
    ("shower", "has to be done b4 the 14th", "need_by", "2026-11-13"),
    ("ev", "lo máximo que puedo pagar es $1.850", "ceiling_usd", 1850.0),
    ("ev", "2,450 is my max, sorry", "ceiling_usd", 2450.0),           # the cue after the number
    ("ev", "Not interested. Stop.", "stop", True),
    ("shower", "not for another month", "wait_weeks", 4),
    ("shower", "in like 3wks", "wait_weeks", 3),
    ("ac", "please don't add the thermostat", "drop_items", ("Smart thermostat",)),
    ("ac", "no me pongan el termostato", "drop_items", ("Smart thermostat",)),
    ("ac", "skip the smart thermostat upgrade and the UV light thing", "drop_items",
     ("Smart thermostat", "UV air purifier")),
    ("ac", "if you can do $9,000, book it and tell me when the crew can come", "ready", True),
    ("ac", "wen can u guys come", "ready", True),
])
def test_more_wordings(job, text, field, want):
    got = getattr(read(job, text), field)
    assert (got.lower() if field == "decider" else got) == (want.lower() if field == "decider" else want)


@pytest.mark.parametrize("job,text,want", [
    ("ev", "no puedo gastar más de 1500", {"ceiling_usd": 1500.0}),
    ("ev", "me cotizaron 1,600 en otro lado, si me lo igualan lo hago con ustedes", {"ceiling_usd": 1600.0}),
    ("furnace", "no necesitamos el plan de mantenimiento anual", {"drop_items": ["Annual maintenance plan"]}),
    ("ac", "quítenme el termostato inteligente", {"drop_items": ["Smart thermostat"]}),
    ("ac", "el purificador no lo queremos", {"drop_items": ["UV air purifier"]}),
    ("chimney", "sin la limpieza de canaletas", {"drop_items": ["Gutter cleaning"]}),
    ("ev", "eso del módulo de carga no lo necesito", {"drop_items": ["Load management module"]}),
    ("shower", "lo necesito listo antes del viernes", {"need_by": "2026-11-05"}),   # before Friday = by Thursday
    ("shower", "a más tardar el 20 de este mes", {"need_by": "2026-11-20"}),
    ("shower", "dentro de unas 3 semanas", {"wait_weeks": 3}),
    ("shower", "en dos semanitas", {"wait_weeks": 2}),
    ("shower", "de aquí a dos meses", {"wait_weeks": 8}),
    ("shower", "el casero tiene que aprobar", {"decider": "landlord"}),
    ("shower", "mi marido tiene que dar el visto bueno", {"decider": "husband"}),
    ("shower", "¿cuándo me lo pueden hacer?", {"ready": True}),
    ("shower", "vamos a hacerlo, ¿cuándo empiezan?", {"ready": True}),
    ("shower", "por favor no contacten más", {"stop": True}),
    ("shower", "favor de no contactarme", {"stop": True}),
    ("shower", "ya no quiero recibir mensajes", {"stop": True}),
])
def test_spanish(job, text, want):
    got = read(job, text).to_dict()
    expected = Facts().to_dict() | want
    assert {k: (v.lower() if k == "decider" and v else v) for k, v in got.items()} == \
        {k: (v.lower() if k == "decider" and v else v) for k, v in expected.items()}


@pytest.mark.parametrize("job,text,want", [
    ("shower", "it's been a crazy couple of weeks, but the most I can pay is $400", {"ceiling_usd": 400.0}),
    ("shower", "the leak started 2 weeks ago. need it fixed by the 12th", {"need_by": "2026-11-12"}),
    ("shower", "hace 2 semanas que gotea, lo necesito para el 12 de noviembre", {"need_by": "2026-11-12"}),
    ("shower", "I've been thinking maybe 3 weeks from now", {"wait_weeks": 3}),
    ("shower", "we've had this problem since the 3rd, the most I'll pay is $410", {"ceiling_usd": 410.0}),
    ("shower", "we've lived here since 2019 and the most I'll pay is $410", {"ceiling_usd": 410.0}),
    ("ev", "let's go with just the circuit, we don't need the load management module",
     {"drop_items": ["Load management module"]}),
])
def test_no_extra_facts_from_small_talk(job, text, want):
    got = read(job, text).to_dict()
    assert got == Facts().to_dict() | want


def test_day_of_month_said_alone():
    assert read("shower", "it's needed by the 9th").need_by == "2026-11-09"
