import pytest

from kya.face import liveness as lv


@pytest.mark.parametrize("direction", ["LEFT", "RIGHT"])
def test_following_the_challenge_passes(direction):
    assert lv.evaluate(lv.synthetic_track(direction), direction)["passed"]


def test_static_photo_fails():
    res = lv.evaluate(lv.synthetic_track(None), "LEFT")
    assert not res["passed"] and "static" in res["reason"]


def test_wrong_direction_fails():
    res = lv.evaluate(lv.synthetic_track("RIGHT"), "LEFT")
    assert not res["passed"] and "wrong way" in res["reason"]


def test_second_face_fails():
    assert not lv.evaluate(lv.synthetic_track("LEFT", faces=2), "LEFT")["passed"]


def test_face_missing_in_most_frames_fails():
    track = lv.synthetic_track("LEFT")
    for f in track[:12]:
        f.update(faces=0, yaw=None)
    assert "visible in only" in lv.evaluate(track, "LEFT")["reason"]


def test_must_start_facing_camera():
    track = [{"faces": 1, "yaw": 0.6 - i * 0.02} for i in range(15)]
    assert "start facing" in lv.evaluate(track, "RIGHT")["reason"]


def test_yaw_sign_from_landmarks():
    # nose to the image-right of the eye midpoint => positive yaw (user turned to their left)
    row = [0, 0, 100, 100, 40, 50, 80, 50, 75, 70, 0, 0, 0, 0, 0.99]
    assert lv.yaw_from_landmarks(row) > 0
