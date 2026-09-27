from app.views import media_src

HEX32 = "0123456789abcdef0123456789abcdef"


def test_clip_urls_are_served_through_the_control_room():
    # A phone on HTTPS can't reach the clip finder's port: clips go through /media/clips/...
    assert media_src(f"http://localhost:8173/clips/{HEX32}.mp4") == f"/media/clips/{HEX32}.mp4"
    assert media_src(f"http://localhost:8173/clips/{HEX32}.jpg") == f"/media/clips/{HEX32}.jpg"
    assert media_src("http://localhost:8173/clips/../../etc.mp4") == "http://localhost:8173/clips/../../etc.mp4"  # not rewritten
