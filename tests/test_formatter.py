from conftest import make_candidate

from octonews import config
from octonews.formatter import PostContent, build_message, validate

CONTENT = PostContent(
    ar_headline="اختراق *كبير* يطال شركة اتصالات",
    ar_summary="أعلنت الشركة عن حادث أمني أثر على أنظمتها. وقالت إن التحقيقات جارية.",
    en_headline="Major breach hits telecom company",
    en_summary="The company disclosed a security incident affecting its systems. It said investigations are ongoing.",
    hashtags=["DataBreach", "#Telecom", "Lebanon", "data breach!"],
)


def test_message_structure_lebanon():
    c = make_candidate(1, lebanon=3, category="cybersecurity")
    msg = build_message(c, CONTENT, "https://example.com/a")
    lines = msg.split("\n")
    assert lines[0].startswith("🇱🇧 🛡️ *")
    assert "*" not in lines[0][lines[0].index("*") + 1 : -1]  # inner markup stripped
    assert "*Major breach hits telecom company*" in msg
    assert "🔗 https://example.com/a" in msg
    tags = lines[-1].split()
    assert tags[0] == "#Cybersecurity"
    assert "#Lebanon" in tags and "#لبنان" in tags
    assert len(tags) == len(set(t.lower() for t in tags))  # no duplicate Lebanon tag
    assert len(tags) <= 5


def test_message_global_ai_and_breaking():
    c = make_candidate(1, lebanon=0, category="ai")
    msg = build_message(c, CONTENT, "https://example.com/a", breaking=True)
    assert msg.startswith("🚨 *عاجل | Breaking*\n🤖 *")
    assert "🇱🇧" not in msg
    assert "#Lebanon" not in msg


def test_long_text_is_truncated_on_word_boundary():
    long = PostContent(CONTENT.ar_headline, CONTENT.ar_summary, "word " * 60, "sentence " * 100, [])
    msg = build_message(make_candidate(1), long, "https://x.y")
    en_head = [l for l in msg.split("\n") if l.startswith("*word")][0]
    assert len(en_head) <= config.MAX_HEADLINE_CHARS + 2
    assert en_head.endswith("…*")


def test_arabic_url_is_percent_encoded_and_ascii_url_untouched():
    msg = build_message(make_candidate(1), CONTENT, "https://www.annahar.com/tech/1/مراكز-البيانات")
    link = [l for l in msg.split("\n") if l.startswith("🔗")][0]
    assert link.isascii() is False  # the emoji
    assert link[2:].isascii() and "%D9%85" in link
    plain = "https://example.com/a?b=1&c=%20x"
    assert plain in build_message(make_candidate(1), CONTENT, plain)


def test_validate():
    assert validate(CONTENT) == []
    bad = PostContent("English only", "Not Arabic either", "", "x" * 2000, [])
    problems = " ".join(validate(bad))
    assert "en_headline is empty" in problems
    assert "ar_headline is not in Arabic" in problems
    assert "en_summary is far too long" in problems
