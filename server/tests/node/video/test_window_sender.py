"""Tests for sending the crop window to ffmpeg (node/video/window_sender.py).

The sender runs on its own thread and talks to ffmpeg's `zmq` filter. Here
the filter is `FakeZmqFilter`, which behaves like the real one: it takes
commands only when the test lets a "frame" pass (`frame()`). Time for the
answer timeout comes from the fake clock.
"""

import logging
import time
from collections.abc import Callable, Iterator

import pytest
import zmq

from baja_studio.node.video.crop_commands import CropPixels
from baja_studio.node.video.window_sender import ANSWER_TIMEOUT_SECONDS, WindowSender
from tests.conftest import FakeClock, eventually
from tests.node.video.fake_zmq_filter import FakeZmqFilter

WHOLE = CropPixels(3840, 1920, 1080)
LOGGER = logging.getLogger("test.window_sender")


@pytest.fixture
def context() -> Iterator[zmq.Context[zmq.Socket[bytes]]]:
    """A ZeroMQ context for one test, ended afterwards."""
    context: zmq.Context[zmq.Socket[bytes]] = zmq.Context()
    yield context
    context.term()


@pytest.fixture
def ffmpeg_filter(context: zmq.Context[zmq.Socket[bytes]]) -> Iterator[FakeZmqFilter]:
    fake = FakeZmqFilter(context)
    yield fake
    fake.close()


@pytest.fixture
def sender(
    context: zmq.Context[zmq.Socket[bytes]], ffmpeg_filter: FakeZmqFilter, fake_clock: FakeClock
) -> Iterator[WindowSender]:
    """A running sender connected to the fake filter; ffmpeg starts showing the whole frame."""
    window_sender = WindowSender(context, ffmpeg_filter.endpoint, WHOLE, fake_clock, LOGGER)
    window_sender.start()
    yield window_sender
    window_sender.stop()


def sent_count_is(sender: WindowSender, count: int) -> Callable[[], bool]:
    """A condition for `eventually`: the sender has sent `count` commands."""
    return lambda: sender.commands_sent == count


def answer_count_is(sender: WindowSender, count: int) -> Callable[[], bool]:
    """A condition for `eventually`: the sender has read `count` answers."""
    return lambda: sender.answers == count


def test_a_new_window_reaches_ffmpeg_at_the_next_frame(
    sender: WindowSender, ffmpeg_filter: FakeZmqFilter
) -> None:
    # The window moves to the top-left quarter: a new width and a new centre.
    sender.offer(CropPixels(1920, 960, 540))
    eventually(lambda: sender.commands_sent == 3, "window sent")
    # One frame passes: the filter takes all three commands at once.
    assert ffmpeg_filter.frame() == [
        "crop@window w 1920",
        "crop@window x 960-ow/2",
        "crop@window y 540-oh/2",
    ]


def test_only_what_changed_is_sent(sender: WindowSender, ffmpeg_filter: FakeZmqFilter) -> None:
    # A pan from the whole frame's centre: same width, same height position.
    sender.offer(CropPixels(3840, 1930, 1080))
    eventually(lambda: sender.commands_sent == 1, "window sent")
    assert ffmpeg_filter.frame() == ["crop@window x 1930-ow/2"]


def test_while_ffmpeg_has_not_answered_only_the_newest_window_waits(
    sender: WindowSender, ffmpeg_filter: FakeZmqFilter
) -> None:
    # The first window goes out at once...
    sender.offer(CropPixels(3840, 1901, 1080))
    eventually(lambda: sender.commands_sent == 1, "first window sent")
    # ...and while it waits for its frame, the joystick sends ten more updates.
    for step in range(2, 12):
        sender.offer(CropPixels(3840, 1900 + step, 1080))
    # A frame takes the first window and answers it. Only then is the next window
    # sent: the newest one, not the nine in between. (It may be quick enough to
    # be taken by the same frame, as with the real filter; the next frame takes
    # it otherwise.)
    ffmpeg_filter.frame()
    eventually(lambda: sender.commands_sent == 2, "next window sent")
    ffmpeg_filter.frame()
    assert ffmpeg_filter.commands == ["crop@window x 1901-ow/2", "crop@window x 1911-ow/2"]
    assert sender.commands_sent == 2


def test_the_time_from_sending_to_the_answer_is_kept(
    sender: WindowSender, ffmpeg_filter: FakeZmqFilter, fake_clock: FakeClock
) -> None:
    assert sender.median_apply_ms() is None  # nothing measured yet
    sender.offer(CropPixels(1920, 960, 540))
    eventually(lambda: sender.commands_sent == 3, "window sent")
    # 40 ms pass before a frame takes the commands.
    fake_clock.advance(0.040)
    ffmpeg_filter.frame()
    eventually(lambda: sender.median_apply_ms() is not None, "answer read")
    assert sender.median_apply_ms() == pytest.approx(40.0)


def test_a_window_never_answered_is_given_up_and_the_next_one_sent(
    sender: WindowSender, ffmpeg_filter: FakeZmqFilter, fake_clock: FakeClock
) -> None:
    # ffmpeg is stuck (no frames). The window goes out but no answer comes.
    sender.offer(CropPixels(3840, 1901, 1080))
    eventually(lambda: sender.commands_sent == 1, "first window sent")
    sender.offer(CropPixels(3840, 1902, 1080))
    # Not yet: the sender waits for an answer...
    fake_clock.advance(ANSWER_TIMEOUT_SECONDS / 2)
    time.sleep(0.05)
    assert sender.commands_sent == 1
    # ...until the answer is overdue. Then it sends the newest window anyway, so a
    # missed answer can never freeze the window for good.
    fake_clock.advance(ANSWER_TIMEOUT_SECONDS)
    eventually(lambda: sender.commands_sent == 2, "next window sent after the timeout")
    # When ffmpeg wakes up it takes both; the late answer is simply ignored.
    assert ffmpeg_filter.frame() == ["crop@window x 1901-ow/2", "crop@window x 1902-ow/2"]


def test_offering_never_waits_for_ffmpeg(
    sender: WindowSender, ffmpeg_filter: FakeZmqFilter
) -> None:
    # ffmpeg never answers, and the motion loop offers a thousand windows: each
    # offer only stores the window, so all of them return at once.
    started = time.monotonic()
    for step in range(1000):
        sender.offer(CropPixels(3840, 1000 + step, 1080))
    assert time.monotonic() - started < 1.0


def test_a_refused_window_is_logged_once(
    sender: WindowSender, ffmpeg_filter: FakeZmqFilter, caplog: pytest.LogCaptureFixture
) -> None:
    ffmpeg_filter.refuse = True
    for step in range(1, 4):
        sender.offer(CropPixels(3840, 1900 + step, 1080))
        eventually(sent_count_is(sender, step), "window sent")
        ffmpeg_filter.frame()
        eventually(answer_count_is(sender, step), "answer read")
    refusals = [record for record in caplog.records if "refused" in record.getMessage()]
    assert len(refusals) == 1
    assert "22 Invalid argument" in refusals[0].getMessage()


def test_stopping_works_even_with_no_ffmpeg_listening(
    context: zmq.Context[zmq.Socket[bytes]], fake_clock: FakeClock
) -> None:
    # Nothing is bound at this address (ffmpeg never started). The sender still
    # starts, queues a window, and stops cleanly: its socket is closed (or the
    # context's `term()` in the fixture would hang).
    lonely = WindowSender(context, "inproc://nobody-here", WHOLE, fake_clock, LOGGER)
    lonely.start()
    lonely.offer(CropPixels(1920, 960, 540))
    lonely.stop()
