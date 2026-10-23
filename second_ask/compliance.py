"""Messaging rules enforced in code before any text leaves the shop. Not legal advice.

- Quiet hours in the recipient's local time: federal 8:00-21:00 (47 CFR 64.1200(c)(1)); Florida 8:00-20:00
  (Fla. Stat. §501.616(6)(a)). The stricter window applies when either the shop or the recipient is in Florida.
- Florida: at most 3 messages to a person per 24 hours on the same subject (Fla. Stat. §501.616(6)(b)). We count every
  outbound message about the estimate, replies included.
- Opt-out: stop, quit, end, revoke, opt out, cancel, unsubscribe are honoured per se (47 CFR 64.1200(a)(10)); we stop
  immediately, not within the 10 business days the rule allows.
- An estimate older than 90 days is not revived by SMS without written consent: an inquiry-based business relationship
  lasts three months (47 CFR 64.1200(f)(5)).
- Every message says it comes from the shop's assistant, not a person (Maine 10 M.R.S. §1500-DD, Utah 13-75-104 safe
  harbour, California B&P §17941).
"""
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

FEDERAL_WINDOW = (8, 21)
FLORIDA_WINDOW = (8, 20)
FLORIDA_MAX_PER_24H = 3
SMS_ESTIMATE_MAX_AGE_DAYS = 90
# Used when the recipient's time zone is unknown: a send must be inside the window in every one of these zones.
US_ZONES = ("America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles", "America/Anchorage",
            "Pacific/Honolulu")
OPT_OUT_WORDS = ("stop", "quit", "end", "revoke", "opt out", "cancel", "unsubscribe")
_FILLER = {"please", "pls", "now", "it", "texting", "texts", "messages", "messaging", "me", "all", "thanks", "thank", "you"}
_PHRASES = re.compile(r"\b(stop|quit) (texting|messaging|contacting|calling)\b|\bunsubscribe\b|\bopt[ -]?out\b|"
                      r"\bremove me\b|\b(do not|don't|dont) (text|message|contact)\b|\bno more (texts|messages)\b|"
                      r"\bleave me alone\b|\brevoke\b")


def opt_out_status(text):
    """"opt_out" for a bare keyword (plus filler such as "please") or a clear phrase ("stop texting me");
    "ambiguous" when a keyword sits inside an ordinary sentence ("end of the month works"); None otherwise.

    The agent stops on "opt_out" or when the label model says opt_out (either signal is enough). "ambiguous" is shown
    to the owner as a review flag.
    """
    norm = re.sub(r"[^a-z' ]+", " ", (text or "").lower().replace("opt-out", "opt out")).split()
    joined = " ".join(norm)
    if _PHRASES.search(joined):
        return "opt_out"
    rest = [w for w in norm if w not in _FILLER]
    if " ".join(rest) in OPT_OUT_WORDS:
        return "opt_out"
    padded = f" {joined} "
    return "ambiguous" if any(f" {w} " in padded for w in OPT_OUT_WORDS) else None


def window_for(recipient_state, shop_state):
    return FLORIDA_WINDOW if "FL" in (recipient_state, shop_state) else FEDERAL_WINDOW


def _inside(t_utc, zones, window):
    start, end = window
    for z in zones:
        local = t_utc.astimezone(ZoneInfo(z))
        if not (start <= local.hour < end):
            return False
    return True


def next_send_time(now, tz, window, sent_times=(), per_24h=FLORIDA_MAX_PER_24H):
    """Earliest UTC time >= now that is inside the quiet-hours window and under the 24-hour cap."""
    zones = (tz,) if tz else US_ZONES
    t = now.astimezone(timezone.utc).replace(second=0, microsecond=0)
    if t < now:
        t += timedelta(minutes=1)
    for _ in range(14 * 24 * 4):  # scan up to 14 days in 15-minute steps
        recent = sorted(s for s in sent_times if t - timedelta(hours=24) < s <= t)
        if per_24h is not None and len(recent) >= per_24h:
            t = max(t, recent[-per_24h] + timedelta(hours=24, minutes=1))
            continue
        if _inside(t, zones, window):
            return t
        t += timedelta(minutes=15 - t.minute % 15)
    raise RuntimeError("no allowed send time in 14 days")


@dataclass
class Recipient:
    tz: str | None            # IANA zone of the recipient; None = unknown
    state: str | None         # two-letter US state of the recipient; None = unknown
    written_consent: bool = False
    opted_out: bool = False


@dataclass
class Gate:
    ok: bool
    send_at: datetime | None
    text: str
    reasons: list = field(default_factory=list)


def disclose(text, shop_name):
    return f"[{shop_name} assistant, not a person] {text.strip()} Reply STOP to opt out."


def gate(text, channel, recipient, shop_name, shop_state, estimate_sent_at, now, sent_times=()):
    """Decide whether and when a message may be sent. Blocks never fall through to a send."""
    if recipient.opted_out:
        return Gate(False, None, "", ["recipient opted out"])
    age = (now - estimate_sent_at).days
    if channel == "sms" and age > SMS_ESTIMATE_MAX_AGE_DAYS and not recipient.written_consent:
        return Gate(False, None, "", [f"estimate is {age} days old: no SMS without written consent"])
    window = FLORIDA_WINDOW if recipient.state is None else window_for(recipient.state, shop_state)  # unknown: stricter
    send_at = next_send_time(now, recipient.tz, window, sent_times)
    reasons = [f"delayed to {send_at.isoformat()} (quiet hours or 3-per-24h cap)"] if send_at > now + timedelta(minutes=1) else []
    return Gate(True, send_at, disclose(text, shop_name), reasons)
