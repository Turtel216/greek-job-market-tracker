"""Scrub personal data from parsed postings before they reach Postgres.

Removes email addresses, phone numbers in Greek and international formats
and named contact lines such as "Contact: Maria Papadopoulou". Each is
replaced with a placeholder, so the text still reads naturally and dbt can
tell that a posting had contact details.

Raw payloads in the object store still hold this data until the 30 day
retention removes them. See "Privacy" in the design doc.
"""

import re

from jobtracker_ingest.core.models import RawPosting

EMAIL_TOKEN = "[email]"
PHONE_TOKEN = "[phone]"
CONTACT_TOKEN = "[contact]"

# Also catches the "name (at) example (dot) gr" spelling some ads use
AT = r"\s*(?:@|[(\[]at[)\]])\s*"
DOT = r"(?:\.|\s*[(\[]dot[)\]]\s*)"
EMAIL = re.compile(rf"[\w.+-]+{AT}[\w-]+(?:{DOT}[\w-]+)*{DOT}[a-z]{{2,}}", re.IGNORECASE)

# Digits with up to two spaces, dots, dashes or brackets between them, as
# in "+1 (415) 555-0132". The candidates are checked in is_phone, since
# amounts and dates look alike.
PHONE_CANDIDATE = re.compile(r"(?<![\w+])(?:\+|\b00)?\(?\d(?:[ .()-]{0,2}\d){6,}\)?(?!\w)")
# Salary amounts and ranges such as 1.800, 25.000-30.000 or 25000 - 30000
AMOUNT = re.compile(
    r"\d{1,3}(?:[.,]\d{3})+(?:(?:\s?[-/]\s?|\s)\d{1,3}(?:[.,]\d{3})+)?"
    r"|\d{4,6}\s?[-/]\s?\d{4,6}"
)
# 02.11.2026 or 2026-11-02 at the start of a candidate is a date
DATE = re.compile(r"\d{1,2}[.-]\d{1,2}[.-]\d{4}|\d{4}-\d{1,2}-\d{1,2}")

# A line that names a contact person, such as "Contact: ..." or
# "Ypefthynos: ..." in Greek, with or without accents. Only the part after
# the label is removed. A dash only counts with spaces around it, so
# "Contact-center agents" is left alone.
CONTACT_LINE = re.compile(
    r"^(?P<label>[\W_]*(?:contact(?:\s+person)?|υπ[εέ][υύ]θυν(?:ος|η)(?:\s+επικοινων[ιί]ας)?)"
    r"(?:\s*:|\s+-)\s*)(?P<rest>\S.*)$",
    re.IGNORECASE | re.MULTILINE,
)

# A name right after "contact" or "reach out to", or the Greek "contact
# Mr or Ms", as in "please contact directly George K. at ...". The verb
# matches in any case, the name must be two or three capitalised words.
NAME_WORD = r"(?:[A-Z\u0386-\u038f\u0391-\u03a9][a-z\u03ac-\u03ce'-]+|[A-Z\u0391-\u03a9]\.)"
INLINE_CONTACT = re.compile(
    r"(?P<verb>(?i:\bcontact(?:\s+directly)?|\breach\s+out\s+to"
    r"|επικοινων[ήη]στε\s+(?:απευθε[ίι]ας\s+)?με\s+(?:τον|την|τη)(?:\s+κ[αο]?\.?)?)"
    r"(?:\s+(?:Mr|Mrs|Ms)\.?)?\s+)"
    rf"{NAME_WORD}(?:\s{NAME_WORD}){{1,2}}"
)

# Free text fields of a posting. url and the structured values are left as they are.
TEXT_FIELDS = ("title_raw", "company_raw", "location_raw", "salary_raw", "description_raw")


def is_phone(candidate: str) -> bool:
    if AMOUNT.fullmatch(candidate) or DATE.match(candidate):
        return False
    digits = re.sub(r"\D", "", candidate)
    if candidate.startswith("+") or candidate.startswith("00"):
        # Country code plus number, as in E.164
        return 8 <= len(digits.removeprefix("00")) <= 15
    if len(digits) == 12 and digits.startswith("30"):
        digits = digits[2:]
    # Greek landlines start with 2 and mobiles with 69, both 10 digits
    return len(digits) == 10 and (digits.startswith("2") or digits.startswith("69"))


def scrub_phones(text: str) -> str:
    return PHONE_CANDIDATE.sub(
        lambda m: PHONE_TOKEN if is_phone(m.group(0)) else m.group(0),
        text,
    )


def scrub_text(text: str | None, names: bool = False) -> str | None:
    """Text with emails, phones and contact lines removed.

    names also removes a name right after "contact", which is only safe in
    a description. A title like "Contact Center Agent" would match.
    """
    if text is None:
        return None
    text = CONTACT_LINE.sub(lambda m: f"{m.group('label')}{CONTACT_TOKEN}", text)
    if names:
        text = INLINE_CONTACT.sub(lambda m: f"{m.group('verb')}{CONTACT_TOKEN}", text)
    text = EMAIL.sub(EMAIL_TOKEN, text)
    return scrub_phones(text)


def scrub_posting(posting: RawPosting) -> RawPosting:
    """The posting with personal data removed from its free text fields."""
    changes = {}
    for name in TEXT_FIELDS:
        value = getattr(posting, name)
        scrubbed = scrub_text(value, names=name == "description_raw")
        if scrubbed != value:
            changes[name] = scrubbed
    return posting.model_copy(update=changes) if changes else posting
