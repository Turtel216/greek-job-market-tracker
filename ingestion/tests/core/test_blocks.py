import pytest

from jobtracker_ingest.core.blocks import block_reason, is_login_url

HTML = "text/html; charset=utf-8"
JOB_PAGE = "<html><body><h1>Data Engineer</h1>" + "<p>We are hiring.</p>" * 2000


@pytest.mark.parametrize(("status", "reason"), [(401, "401 Unauthorized"), (403, "403 Forbidden")])
def test_auth_statuses_are_blocks(status: int, reason: str) -> None:
    assert block_reason(status, "application/json", b"{}") == reason


@pytest.mark.parametrize("status", [200, 404, 429, 500])
def test_other_statuses_with_a_normal_body_are_fine(status: int) -> None:
    assert block_reason(status, HTML, JOB_PAGE.encode()) is None


def test_cloudflare_challenge_is_a_block() -> None:
    body = b"<html><head><title>Just a moment...</title></head><body></body></html>"
    assert block_reason(200, HTML, body) == "challenge page (<title>just a moment...</title>)"


def test_challenge_marker_counts_on_a_large_page() -> None:
    body = (JOB_PAGE + '<div class="px-captcha"></div>').encode()
    assert block_reason(503, HTML, body) == "challenge page (px-captcha)"


def test_small_captcha_page_is_a_block() -> None:
    body = b'<html><body><div class="g-recaptcha" data-sitekey="x"></div></body></html>'
    assert block_reason(200, HTML, body) == "captcha page (g-recaptcha)"


def test_recaptcha_on_a_full_job_page_is_not_a_block() -> None:
    body = (JOB_PAGE + '<form><div class="g-recaptcha"></div></form>').encode()
    assert block_reason(200, HTML, body) is None


def test_json_mentioning_captcha_is_not_a_block() -> None:
    body = b'{"title": "Engineer, anti-bot and captcha systems"}'
    assert block_reason(200, "application/json", body) is None


def test_html_without_content_type_is_still_checked() -> None:
    body = b"  <html><title>Just a moment...</title></html>"
    assert block_reason(200, "", body) is not None


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/login",
        "https://example.org/login?next=/jobs/1",
        "https://example.org/account/sign-in",
        "https://example.org/users/sign_in",
        "https://example.org/auth/",
        "https://example.org/Login.aspx",
    ],
)
def test_login_urls(url: str) -> None:
    assert is_login_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/jobs/123",
        "https://example.org/jobs/authentication-engineer",
        "https://login.example.org/jobs/1",
        "https://example.org/jobs?q=login",
    ],
)
def test_normal_urls(url: str) -> None:
    assert not is_login_url(url)
