

def test_health_word_about_margins_is_not_a_health_claim():
    from app import kit_rules
    assert kit_rules.business_sense("It moves fast and keeps margins healthy.", "healthy")
    assert not kit_rules.business_sense("Maltex keeps you healthy.", "healthy")
