"""Client-link tokens are in the URL path (/c/<token>): the access log must never keep them."""
import logging


def test_access_log_hides_client_link_tokens():
    from app.logredact import RedactClientTokens
    f = RedactClientTokens()
    tok = "Zk3v9QpL2mN8xR4tY7wB1cD5eF6gH0jK"
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1,
                            '%s - "%s %s HTTP/%s" %d', ("1.2.3.4:5", "GET", f"/c/{tok}?x=1", "1.1", 200), None)
    assert f.filter(rec) is True
    assert tok not in rec.getMessage() and "/c/***" in rec.getMessage()
    media = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                              ("1.2.3.4:5", "GET", "/c/m/cards/abc.png", "1.1", 200), None)
    f.filter(media)
    assert "/c/m/cards/abc.png" in media.getMessage()   # no token in media paths
    other = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                              ("1.2.3.4:5", "GET", "/tasks/T-ABC", "1.1", 200), None)
    f.filter(other)
    assert "/tasks/T-ABC" in other.getMessage()


def test_filter_is_installed_on_the_access_logger():
    from app.main import create_app
    from app.logredact import RedactClientTokens
    create_app()
    assert any(isinstance(x, RedactClientTokens) for x in logging.getLogger("uvicorn.access").filters)
