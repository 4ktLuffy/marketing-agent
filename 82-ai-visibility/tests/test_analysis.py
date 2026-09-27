from app import analysis as an

BRAND = an.entity("Northwind Roasters", ["Northwind"], ["https://www.northwind-roasters.example.com/"], "brand")
ATLAS = an.entity("Atlas Coffee Club", ["Atlas"], ["https://atlascoffeeclub.com"])
TRADE = an.entity("Trade Coffee", ["Trade"], ["drinktrade.com"])


def test_mentions_whole_words_and_aliases():
    assert an.mentions("Try Northwind Roasters for fresh beans.", BRAND)
    assert an.mentions("northwind roasters ships weekly", BRAND)          # several words: any case
    assert an.mentions("Northwind-Roasters is small", BRAND)              # hyphen between words
    assert an.mentions("I like Northwind's decaf", BRAND)                 # alias + possessive
    assert an.mentions("see northwind-roasters.example.com", BRAND)       # own domain
    assert an.mentions("**Northwind Roasters** – fresh", BRAND)           # markdown bold


def test_mentions_are_not_substrings():
    assert not an.mentions("Northwindy weather today", BRAND)
    assert not an.mentions("the NorthwindRoasters account", BRAND)
    dom = an.entity("drinktrade.com", domains=["drinktrade.com"])
    assert an.mentions("order at drinktrade.com.", dom)
    assert not an.mentions("visit drinktrade.com.evil.net", dom)
    assert not an.mentions("my-drinktrade.com", dom)


def test_single_word_alias_is_case_sensitive():
    assert an.mentions("Trade is a good pick", TRADE)
    assert an.mentions("TRADE ships beans", TRADE)
    assert not an.mentions("there is a trade-off in price", TRADE)
    assert not an.mentions("fair trade beans", TRADE)
    assert an.mentions("atlas coffee club is fun", ATLAS)
    assert not an.mentions("an atlas of coffee regions", ATLAS)


def test_numbered_list_position():
    text = ("Here are the best options:\n\n1. **Atlas Coffee Club** – beans from a new country.\n"
            "   - Great for gifts, better than Northwind Roasters for variety.\n"
            "2. **Northwind Roasters**: roasted to order.\n3. Trade Coffee - quiz based.\n")
    assert an.list_position(text, BRAND) == 2      # the sub-bullet of item 1 does not count
    assert an.list_position(text, ATLAS) == 1
    assert an.list_position(text, TRADE) == 3


def test_heading_list_and_restart():
    text = ("### 1. Trade Coffee\nMatches you to roasters.\n\n### 2. Atlas Coffee Club\nWorld tour.\n\n"
            "Budget picks:\n1. Northwind Roasters\n2. Trade Coffee\n")
    lists = an.ranked_lists(text)
    assert lists == [["Trade Coffee", "Atlas Coffee Club"], ["Northwind Roasters", "Trade Coffee"]]
    assert an.list_position(text, BRAND) == 1      # first list that names it
    assert an.list_position(text, TRADE) == 1


def test_bullets_and_tables():
    bullets = "Options:\n- **Atlas Coffee Club**: fun\n- **Northwind Roasters**: fresh\n"
    assert an.list_position(bullets, BRAND) == 2
    table = ("| Rank | Service | Price |\n|---|---|---|\n| 1 | Trade Coffee | $20 |\n"
             "| 2 | **Northwind Roasters** | $18 |\n")
    assert an.list_position(table, BRAND) == 2
    assert an.list_position("Northwind Roasters is nice.", BRAND) is None


def test_citation_domains_and_gemini_redirects():
    cits = [{"url": "https://blog.atlascoffeeclub.com/best", "title": "Best"},
            {"url": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AbC", "title": "northwind-roasters.example.com"},
            {"url": "https://reddit.com/r/coffee", "title": "r/coffee"}]
    assert an.cites(cits, ATLAS) == ["https://blog.atlascoffeeclub.com/best"]   # subdomain counts
    assert an.cites(cits, BRAND) == ["https://vertexaisearch.cloud.google.com/grounding-api-redirect/AbC"]
    assert an.cites(cits, TRADE) == []
    assert an.citation_domain({"url": "https://www.reddit.com/x"}) == "reddit.com"


def test_analyse_answer():
    text = "1. Trade Coffee\n2. Atlas Coffee Club\n\nNorthwind Roasters is smaller."
    res = an.analyse(text, [{"url": "https://drinktrade.com/quiz"}], BRAND, [ATLAS, TRADE])
    assert res["brand_mentioned"] and res["brand_position"] is None and not res["brand_cited"]
    assert res["competitors"]["Trade Coffee"] == {"mentioned": True, "position": 1, "cited": True}
    assert res["mention_order"] == ["Trade Coffee", "Atlas Coffee Club", "Northwind Roasters"]
    assert res["cited_domains"] == ["drinktrade.com"]


def test_sentences_about_brand_and_products():
    text = ("Northwind Roasters sells a Team Box for $99 a month. Their Desk Blend is a dark roast. "
            "Atlas is different.\n\n| Brand | Price |\n|---|---|\n| Northwind | $18 |")
    got = an.sentences_about(text, BRAND, ["Desk Blend", "Team Box"])
    assert got == ["Northwind Roasters sells a Team Box for $99 a month.", "Their Desk Blend is a dark roast.",
                   "Northwind; $18"]
    assert an.sentences_about("Desk Blend is great.", BRAND, ["Desk Blend"]) == []  # brand never named


def test_wilson_interval():
    assert an.wilson(0, 0) is None
    lo, hi = an.wilson(0, 30)
    assert lo == 0.0 and 0.11 < hi < 0.12          # 0/30 is not "exactly 0%"
    lo, hi = an.wilson(3, 10)
    assert 0.10 < lo < 0.11 and 0.60 < hi < 0.61
    lo, hi = an.wilson(10, 10)
    assert hi == 1.0 and 0.69 < lo < 0.73
    assert an.rate(1, 4) == {"k": 1, "n": 4, "rate": 0.25, "ci95": an.wilson(1, 4)}


def test_urls_in_text():
    assert an.urls_in("See https://atlascoffeeclub.com/plans. Or (https://x.com/a).") == [
        "https://atlascoffeeclub.com/plans", "https://x.com/a"]
