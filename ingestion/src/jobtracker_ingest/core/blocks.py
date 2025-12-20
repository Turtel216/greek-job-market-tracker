"""Spot responses that mean a site does not want us there.

A block stops the source for the day. We never try to get around one, so a
false alarm only costs a day of data, while a missed block risks the
university IP. The checks still try to stay narrow, because a real job page
can carry a reCAPTCHA on its apply form.
"""

import re
from urllib.parse import urlsplit

BLOCK_STATUSES = {401: "401 Unauthorized", 403: "403 Forbidden"}

# Only ever seen on challenge or block pages, so one match is enough
CHALLENGE_MARKERS = (
    "<title>just a moment...</title>",
    "<title>attention required! | cloudflare</title>",
    "window._cf_chl_opt",
    'id="challenge-form"',
    "captcha-delivery.com",
    "px-captcha",
)

# Also used by normal pages, for example on an apply form. They only count
# on a small page, since a CAPTCHA wall is short and a job page is not.
CAPTCHA_WIDGETS = ("g-recaptcha", "h-captcha", "cf-turnstile", "captcha")
SMALL_PAGE_CHARS = 15_000

LOGIN_PATH = re.compile(r"(^|/)(log-?in|sign-?in|sign_in|signin|auth|sso)(/|$|\.|\?)", re.I)


def is_html(content_type: str, body: bytes) -> bool:
    if content_type:
        return "html" in content_type.lower()
    return body.lstrip()[:1] == b"<"


def captcha_reason(content_type: str, body: bytes) -> str | None:
    if not is_html(content_type, body):
        return None
    text = body.decode("utf-8", errors="replace").lower()
    for marker in CHALLENGE_MARKERS:
        if marker in text:
            return f"challenge page ({marker})"
    if len(text) <= SMALL_PAGE_CHARS:
        for marker in CAPTCHA_WIDGETS:
            if marker in text:
                return f"captcha page ({marker})"
    return None


def is_login_url(url: str) -> bool:
    return bool(LOGIN_PATH.search(urlsplit(url).path))


def block_reason(status: int, content_type: str, body: bytes) -> str | None:
    """Why this response counts as a block, or None if it does not."""
    if status in BLOCK_STATUSES:
        return BLOCK_STATUSES[status]
    return captcha_reason(content_type, body)
