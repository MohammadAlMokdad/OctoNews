"""Short-link logic against a fake website (no network)."""

import httpx

from octonews.extract import short_candidates, shorten_url

LONG = "https://www.lebanon24.com/news/lebanon/1593444/%D8%A5%D9%86%D8%AA%D8%B1%D9%86%D8%AA-%D9%84%D8%A8%D9%86%D8%A7%D9%86"
SHORT = "https://www.lebanon24.com/news/lebanon/1593444/"
HEADLINE = "إنترنت لبنان في قاع العالم"


def page(title: str, canonical: str = "") -> str:
    link = f'<link rel="canonical" href="{canonical}">' if canonical else ""
    return f"<html><head><title>{title}</title>{link}</head><body><h1>{title}</h1><p>text</p></body></html>"


def client(routes: dict) -> httpx.Client:
    def handler(request: httpx.Request):
        key = str(request.url)
        for url, (status, body) in routes.items():
            if httpx.URL(url) == request.url:
                return httpx.Response(status, html=body)
        return httpx.Response(404, text="not found " + key)

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_short_candidates():
    assert short_candidates(LONG) == [
        SHORT,
        "https://www.lebanon24.com/news/lebanon/1593444",
        "https://www.lebanon24.com/news/1593444",
        "https://www.lebanon24.com/1593444",
    ]
    assert short_candidates("https://www.lebanon24.com/news/lebanon/1593444/إنترنت-لبنان")[0] == SHORT
    assert short_candidates("https://example.com/news/123/plain-english-slug") == []
    assert short_candidates("https://example.com/%D8%A5%D9%86") == []  # nothing meaningful left
    assert short_candidates("https://x.com/a/my-slug/%D8%A5") == ["https://x.com/a/my-slug/", "https://x.com/a/my-slug"]


def test_picks_first_short_form_that_opens_the_article():
    # Lebanon24's real behaviour: /news/lebanon/<id>/ goes to the homepage, /news/<id> opens the article.
    home = page("Lebanon 24 - Lebanon News, Breaking News")
    c = client({
        LONG: (200, page(HEADLINE)),
        SHORT: (200, home),
        "https://www.lebanon24.com/news/lebanon/1593444": (200, home),
        "https://www.lebanon24.com/news/1593444": (200, page(HEADLINE)),
    })
    assert shorten_url(LONG, headline=HEADLINE, client=c) == "https://www.lebanon24.com/news/1593444"


def test_short_link_used_when_it_opens_the_same_article():
    c = client({LONG: (200, page(HEADLINE)), SHORT: (200, page(HEADLINE))})
    assert shorten_url(LONG, headline=HEADLINE, client=c) == SHORT


def test_short_link_used_when_canonical_matches():
    c = client({LONG: (200, page("A", canonical=LONG)), SHORT: (200, page("B", canonical=LONG))})
    assert shorten_url(LONG, client=c) == SHORT


def test_full_link_kept_when_short_one_is_another_page():
    c = client({LONG: (200, page(HEADLINE)), SHORT: (200, page("Lebanon24 - Latest news"))})
    assert shorten_url(LONG, headline=HEADLINE, client=c) == LONG


def test_generic_site_title_does_not_fool_the_check():
    c = client({LONG: (200, page("Lebanon24")), SHORT: (200, page("Lebanon24"))})
    assert shorten_url(LONG, headline=HEADLINE, client=c) == LONG


def test_full_link_kept_when_short_one_fails():
    c = client({LONG: (200, page(HEADLINE)), SHORT: (404, "")})
    assert shorten_url(LONG, headline=HEADLINE, client=c) == LONG


def test_plain_links_untouched_without_network():
    def boom(request):
        raise AssertionError("no request expected")

    c = httpx.Client(transport=httpx.MockTransport(boom))
    assert shorten_url("https://example.com/news/123/plain-slug", client=c) == "https://example.com/news/123/plain-slug"
