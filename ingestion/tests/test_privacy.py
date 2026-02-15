from datetime import UTC, date, datetime

import pytest

from jobtracker_ingest.core.models import RawPosting
from jobtracker_ingest.privacy import is_phone, scrub_posting, scrub_text


@pytest.mark.parametrize(
    ("text", "scrubbed"),
    [
        ("Send your CV to jobs@example.gr today", "Send your CV to [email] today"),
        ("Στείλτε βιογραφικό στο hr.team+it@acme-corp.com.gr.", "Στείλτε βιογραφικό στο [email]."),
        ("maria (at) example (dot) gr", "[email]"),
        ("maria[at]example[dot]com", "[email]"),
        ("MARIA @ EXAMPLE.GR", "[email]"),
    ],
)
def test_emails(text: str, scrubbed: str) -> None:
    assert scrub_text(text) == scrubbed


@pytest.mark.parametrize(
    "phone",
    [
        "2101234567",
        "210 1234567",
        "210-123-4567",
        "210.12.34.567",
        "2310 123456",
        "6971234567",
        "697 123 4567",
        "+30 210 1234567",
        "+30 6971234567",
        "0030 210 1234567",
        "302101234567",
        "+44 20 7946 0958",
        "+1 (415) 555-0132",
    ],
)
def test_phones(phone: str) -> None:
    assert scrub_text(f"Τηλ. {phone}, ώρες 9-5") == "Τηλ. [phone], ώρες 9-5"


@pytest.mark.parametrize(
    "text",
    [
        # Salaries, as the dbt salary rules need them
        "Μικτές αποδοχές 1.800 - 2.200 € μηνιαίως",
        "Gross annual salary 25.000-30.000 EUR",
        "Salary 25.000 30.000 per year",
        "Salary 25000-30000 EUR",
        "Salary 2,500/3,000 per month",
        # Dates, years and times
        "Έναρξη 02.11.2026 10:00",
        "Start 2026-11-02",
        "Experience 2019-2023",
        "Office hours 09.00-17.00",
        # Other numbers that are not phones
        "Ref. code 4012345",
        "Postal code 151 25",
        "VAT 094012345",
        "1234567890 is not a Greek number",
        "+12 34",
    ],
)
def test_numbers_that_are_not_phones_are_kept(text: str) -> None:
    assert scrub_text(text) == text


@pytest.mark.parametrize(
    ("text", "scrubbed"),
    [
        ("Contact: Maria Papadopoulou", "Contact: [contact]"),
        ("- Contact person: John Smith, HR", "- Contact person: [contact]"),
        ("Υπεύθυνος: Γιώργος Κ.", "Υπεύθυνος: [contact]"),
        ("Υπεύθυνη επικοινωνίας: Ελένη Π.", "Υπεύθυνη επικοινωνίας: [contact]"),
        ("ΥΠΕΥΘΥΝΟΣ - ΝΙΚΟΣ Α.", "ΥΠΕΥΘΥΝΟΣ - [contact]"),
        ("About us\nContact: Maria\nApply now", "About us\nContact: [contact]\nApply now"),
    ],
)
def test_contact_lines(text: str, scrubbed: str) -> None:
    assert scrub_text(text) == scrubbed


@pytest.mark.parametrize(
    "text",
    [
        "Contact-center agents wanted",
        "Contact Center Agent",
        "You will be our contact: point for clients",
        "Υπεύθυνος Αποθήκης",
    ],
)
def test_lines_that_only_mention_contact_are_kept(text: str) -> None:
    assert scrub_text(text) == text


@pytest.mark.parametrize(
    ("text", "scrubbed"),
    [
        (
            "please contact directly George Komninos at hr@example.gr",
            "please contact directly [contact] at [email]",
        ),
        ("Contact Ms. Maria Papadopoulou for details", "Contact Ms. [contact] for details"),
        ("Reach out to Anna K. Smith today", "Reach out to [contact] today"),
        ("Επικοινωνήστε με την κα Ελένη Παπαδοπούλου", "Επικοινωνήστε με την κα [contact]"),
        ("Επικοινωνήστε με τον Γιώργο Κ.", "Επικοινωνήστε με τον [contact]"),
    ],
)
def test_names_after_contact_in_descriptions(text: str, scrubbed: str) -> None:
    assert scrub_text(text, names=True) == scrubbed


@pytest.mark.parametrize(
    "text",
    [
        "contact us at the office",
        "Contact Center agents wanted",
        "We contact Clients daily",
    ],
)
def test_contact_without_a_full_name_is_kept(text: str) -> None:
    assert scrub_text(text, names=True) == text


def test_names_are_only_scrubbed_when_asked() -> None:
    assert scrub_text("Contact Center Agent") == "Contact Center Agent"


def test_contact_line_with_email_and_phone() -> None:
    text = "Για πληροφορίες: hr@example.gr ή 210 1234567"
    assert scrub_text(text) == "Για πληροφορίες: [email] ή [phone]"


def test_none_stays_none() -> None:
    assert scrub_text(None) is None


def test_is_phone_rejects_short_international_numbers() -> None:
    assert not is_phone("+30 1234")
    assert is_phone("+30 210 1234567")


def posting(**fields: str | None) -> RawPosting:
    values: dict[str, str | None] = {
        "title_raw": "Backend Developer",
        "company_raw": "Acropolis Analytics",
        "location_raw": "Athens",
        "workplace_raw": "hybrid",
        "employment_type_raw": "FULL_TIME",
        "salary_raw": "2000 - 2500 EUR per month",
        "department_raw": "Engineering",
        "description_raw": "Python and Django",
        "posted_at_raw": "2026-10-20",
        **fields,
    }
    return RawPosting(
        source="greenhouse",
        source_job_id="1",
        run_date=date(2026, 10, 25),
        scraped_at=datetime(2026, 10, 25, 3, tzinfo=UTC),
        url="https://example.org/jobs/1?contact=jobs@example.gr",
        object_key=None,
        parser_version="1",
        **values,
    )


def test_scrub_posting_cleans_free_text_and_rehashes() -> None:
    dirty = posting(
        title_raw="Contact Center Agent",
        description_raw="Apply at jobs@example.gr\nContact: Maria P.\nOr contact Anna Smith",
    )

    clean = scrub_posting(dirty)

    assert clean.description_raw == "Apply at [email]\nContact: [contact]\nOr contact [contact]"
    assert clean.title_raw == "Contact Center Agent"
    assert clean.content_hash != dirty.content_hash
    # The URL is the posting's address, not free text
    assert clean.url == dirty.url


def test_scrub_posting_returns_the_same_posting_when_clean() -> None:
    clean = posting()
    assert scrub_posting(clean) is clean
