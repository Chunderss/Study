"""Reviews are scheduled in local calendar days.

An answer on one local day makes the card due at the start of the local day
1, 3, 7, 16 or 35 days later. A card is due from the start of the local day
that contains its stored due time, which also covers progress saved with exact
24-hour periods and a change of time zone.

Most tests set the zone with TZ and time.tzset(), which Windows lacks; the
first test runs in whatever zone the computer uses.
"""
import math
import os
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from vocab.algorithms import get_scheduler
from vocab.algorithms.base import Review
from vocab.cli.app import App
from vocab.core.learning import LearningStore
from vocab.study.session import StudySession, build_session

INTERVALS = {1: 1, 2: 3, 3: 7, 4: 16, 5: 35}    # box after a review -> days


@pytest.fixture
def zone():
    if not hasattr(time, "tzset"):
        pytest.skip("choosing a time zone needs time.tzset, which Windows lacks")
    saved = os.environ.get("TZ")

    def use(name):
        try:
            import zoneinfo
            info = zoneinfo.ZoneInfo(name)
        except Exception:
            pytest.skip(f"no time zone data for {name}")
        os.environ["TZ"] = name
        time.tzset()
        # TZ goes through the C library's zone files, while zoneinfo may use
        # the tzdata package. Without the files the C library silently uses
        # UTC: skip rather than fail.
        for probe in (1767268800, 1782907200):   # 12:00 UTC on 2026-01-01, 2026-07-01
            expected = datetime.fromtimestamp(probe, info).utcoffset().total_seconds()
            if time.localtime(probe).tm_gmtoff != expected:
                pytest.skip(f"the C library has no time zone data for {name}")

    yield use
    if saved is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = saved
    time.tzset()


def at(text):
    """A local wall-clock time, 'YYYY-MM-DD HH:MM[:SS]', in the current zone."""
    fmt = "%Y-%m-%d %H:%M:%S" if text.count(":") == 2 else "%Y-%m-%d %H:%M"
    return datetime.strptime(text, fmt).timestamp()


def wall(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def local_date(ts):
    return datetime.fromtimestamp(ts).date()


def reviewed(box_before, outcome, now):
    sched = get_scheduler("leitner")
    card = dict(sched.new_card(), box=box_before)
    return sched.review(card, outcome, now)


def assert_starts_its_day(due):
    assert local_date(due - 1) < local_date(due), f"{wall(due)} is not the start of a day"


def test_answers_make_cards_due_at_the_start_of_a_local_day():
    """In this computer's own zone: every interval ends at a day's first instant."""
    sched = get_scheduler("leitner")
    start = datetime(2026, 1, 1, 0, 7).timestamp()
    for step in range(0, 366 * 24, 53):              # every 53 hours through 2026
        now = start + step * 3600 + 0.25
        for box_before in range(1, 6):
            for outcome in (Review.GOOD, Review.AGAIN):
                card = sched.review(dict(sched.new_card(), box=box_before), outcome, now)
                due = card["due"]
                assert_starts_its_day(due)
                assert local_date(due) == local_date(now) + timedelta(days=INTERVALS[card["box"]])
                assert not sched.is_due(card, due - 1) and sched.is_due(card, due)
                assert card["last"] == now


def test_times_the_platform_cannot_convert_count_utc_days():
    """Python cannot convert these to local dates on any platform (nor
    times before 1970 on Windows); scheduling must not fail on them."""
    from vocab.algorithms.leitner import local_day_start
    sched = get_scheduler("leitner")
    for now in (-1e12, 1e18):
        assert local_day_start(now, 1) == (math.floor(now / 86400) + 1) * 86400
        card = sched.review(sched.new_card(), Review.GOOD, now)
        assert card["due"] == (math.floor(now / 86400) + 3) * 86400
        assert sched.is_due(card, card["due"]) and not sched.is_due(card, card["due"] - 86400)


def test_a_card_missed_in_the_evening_is_due_the_next_evening(zone, tmp_path, monkeypatch):
    """The audit's case: missed at 20:14, studied again at 20:00 the next day."""
    zone("America/Chicago")
    app = App(tmp_path)
    app.cmd_create("Book")
    app.cmd_use("Book")
    app.cmd_add("alpha", manual_def="first letter")
    clock = SimpleNamespace(time=lambda: at("2026-09-29 20:14:30"))
    monkeypatch.setattr("vocab.study.session.time", clock)
    monkeypatch.setattr("vocab.cli.app.time", clock)
    session = StudySession(app.storage, app.scheduler, ["Book"])
    assert session.answer(False).box_after == 1
    assert wall(app.storage.load_stats("Book")["alpha"]["due"]) == "2026-09-30 00:00:00"

    clock.time = lambda: at("2026-09-29 23:59:59")
    assert build_session(app.storage, app.scheduler, ["Book"]) == []
    assert app.due_count("Book") == 0
    clock.time = lambda: at("2026-09-30 00:00")
    assert app.due_count("Book") == 1
    clock.time = lambda: at("2026-09-30 20:00")
    assert [c.word.word for c in build_session(app.storage, app.scheduler, ["Book"])] == ["alpha"]
    assert "1 due now" in app.cmd_stats("Book")
    assert "DUE alpha" in app.cmd_stats("Book")


@pytest.mark.parametrize("answered, box_before, outcome, due, hours", [
    # Clocks go forward at 02:00 on 2026-03-08 in Chicago: that day has 23 hours.
    ("2026-03-07 21:00", 1, Review.AGAIN, "2026-03-08 00:00:00", 3),
    ("2026-03-07 21:00", 1, Review.GOOD, "2026-03-10 00:00:00", 3 + 2 * 24 - 1),
    ("2026-03-08 03:30", 1, Review.AGAIN, "2026-03-09 00:00:00", 20.5),
    # Clocks go back at 02:00 on 2026-11-01: that day has 25 hours.
    ("2026-10-31 22:00", 1, Review.GOOD, "2026-11-03 00:00:00", 2 + 2 * 24 + 1),
    # The one case later than 24-hour periods, by less than the shift: 72 hours
    # after 00:30 on Oct 31 is 23:30 on Nov 2.
    ("2026-10-31 00:30", 1, Review.GOOD, "2026-11-03 00:00:00", 23.5 + 25 + 24),
    ("2026-11-01 01:30", 1, Review.AGAIN, "2026-11-02 00:00:00", 22.5 + 1),  # first 01:30
    ("2026-10-31 12:00", 4, Review.GOOD, "2026-12-05 00:00:00", 12 + 34 * 24 + 1),
])
def test_intervals_count_calendar_days_across_daylight_saving_changes(
        zone, answered, box_before, outcome, due, hours):
    zone("America/Chicago")
    now = at(answered)
    card = reviewed(box_before, outcome, now)
    assert wall(card["due"]) == due
    assert card["due"] - now == pytest.approx(hours * 3600)


def test_the_repeated_hour_is_the_same_day(zone):
    zone("America/Chicago")
    first = at("2026-11-01 01:30")             # CDT
    second = first + 3600                      # the same wall time again, in CST
    assert wall(second) == "2026-11-01 01:30:00"
    for now in (first, second):
        assert wall(reviewed(1, Review.AGAIN, now)["due"]) == "2026-11-02 00:00:00"


@pytest.mark.parametrize("name, answered, due", [
    ("America/Havana", "2026-03-07 10:00", "2026-03-08 01:00:00"),     # 00:00 -> 01:00
    ("America/Santiago", "2026-09-05 10:00", "2026-09-06 01:00:00"),   # 00:00 -> 01:00
    ("Pacific/Apia", "2011-12-29 10:00", "2011-12-31 00:00:00"),       # 2011-12-30 skipped
])
def test_a_day_without_a_midnight_starts_at_its_first_time(zone, name, answered, due):
    zone(name)
    card = reviewed(1, Review.AGAIN, at(answered))
    assert wall(card["due"]) == due
    assert_starts_its_day(card["due"])
    sched = get_scheduler("leitner")
    assert not sched.is_due(card, card["due"] - 1) and sched.is_due(card, card["due"])


# (zone, its daylight-saving shift in seconds, 2026 transition days)
ZONES = [
    ("America/Chicago", 3600, ["2026-03-08", "2026-11-01"]),
    ("Europe/London", 3600, ["2026-03-29", "2026-10-25"]),
    ("Australia/Lord_Howe", 1800, ["2026-04-05", "2026-10-04"]),
    ("America/Havana", 3600, ["2026-03-08", "2026-11-01"]),
    ("America/Santiago", 3600, ["2026-04-05", "2026-09-06"]),
    ("Asia/Kolkata", 0, []),
]


@pytest.mark.parametrize("name, shift, changes", ZONES, ids=[z[0] for z in ZONES])
def test_calendar_days_are_never_later_than_24_hour_periods_beyond_the_clock_change(
        zone, name, shift, changes):
    """Rounding to the day only moves a due time earlier, except by less than a
    daylight-saving shift after an answer in the first hour after midnight
    before the clocks go back (the old rule then lands late the evening before)."""
    zone(name)
    times = [at("2026-01-01 00:00") + n * (7 * 3600 + 13 * 60) for n in range(1215)]
    for change in changes:                     # every 10 minutes for 4 days around it
        first = at(change + " 00:00") - 2 * 86400
        times += [first + n * 600 for n in range(4 * 144)]
    sched = get_scheduler("leitner")
    for now in times:
        for box_before in (1, 2, 3, 4, 5):
            for outcome in (Review.GOOD, Review.AGAIN):
                card = reviewed(box_before, outcome, now)
                days = INTERVALS[card["box"]]
                old = now + days * 86400
                assert_starts_its_day(card["due"])
                assert local_date(card["due"]) == local_date(now) + timedelta(days=days)
                assert card["due"] <= old + shift, (wall(now), days)
                assert card["due"] > old - 86400 - shift - 1, (wall(now), days)
                assert not sched.is_due(card, now)


def test_progress_saved_with_exact_times_is_due_from_the_start_of_its_day(zone, tmp_path, monkeypatch):
    """Older versions stored the answer time plus whole 24-hour periods."""
    zone("America/Chicago")
    app = App(tmp_path)
    app.cmd_create("Book")
    app.cmd_use("Book")
    app.cmd_add("alpha", manual_def="first letter")
    app.cmd_add("beta", manual_def="second letter")
    old_due = at("2026-09-29 20:14:30") + 0.731 + 86400   # missed at 20:14 on Sep 29
    stats = app.storage.load_stats("Book")
    for word in ("alpha", "beta"):
        stats[word] = {"algo": "leitner", "box": 1, "due": old_due, "reps": 1,
                       "lapses": 1, "last": old_due - 86400}
    stats["beta"]["due"] = at("2026-10-01 07:00")      # due the day after
    app.storage.save_stats("Book", stats)
    saved = app.storage.load_stats("Book")

    clock = SimpleNamespace(time=lambda: at("2026-09-29 23:59:59"))
    monkeypatch.setattr("vocab.cli.app.time", clock)
    assert app.due_count("Book") == 0
    clock.time = lambda: at("2026-09-30 00:00")
    assert app.due_count("Book") == 1
    queue = build_session(app.storage, app.scheduler, ["Book"], now_ts=at("2026-09-30 08:00"))
    assert [c.word.word for c in queue] == ["alpha"]
    assert app.storage.load_stats("Book") == saved     # read, never rewritten


def test_learning_reviews_use_calendar_days(zone, tmp_path):
    zone("America/Chicago")
    app = App(tmp_path)
    app.cmd_create("Book")
    store = LearningStore(app.storage)
    item = store.save_capture("Book", kind="concept", prompt="Why?", quote="Because.")
    item = store.review("Book", item["id"], item["revision"], "my answer", "again",
                        now=at("2026-09-29 20:14"))
    assert wall(item["card"]["due"]) == "2026-09-30 00:00:00"
    assert store.status(item, now=at("2026-09-29 23:59")) == "Review Sep 30"
    assert store.status(item, now=at("2026-09-30 00:00")) == "Due now"
    items = store.load("Book")
    assert store.due(items, at("2026-09-29 23:59")) == []
    assert store.due(items, at("2026-09-30 20:00")) == [item["id"]]

    # A capture saved by an older version, due at an exact time on Oct 2.
    legacy = dict(item, card=dict(item["card"], due=at("2026-10-02 18:45:10") + 0.5))
    assert store.status(legacy, now=at("2026-10-01 23:59")) == "Review Oct 02"
    assert store.status(legacy, now=at("2026-10-02 08:00")) == "Due now"
    # A due time too far away to show as a date (valid in the file) does not fail.
    far = dict(item, card=dict(item["card"], due=1e20))
    assert store.status(far, now=at("2026-10-02 08:00")) == "Review later"


def test_after_a_time_zone_change_a_card_is_due_from_the_start_of_the_day_holding_its_due_time(zone):
    """The stored due time is the start of the due day where the answer was
    given. Elsewhere the card is due from the start of the local day containing
    that instant: the same date to the east, a day early to the west, never later."""
    zone("America/Chicago")
    card = reviewed(1, Review.AGAIN, at("2026-10-10 20:00"))
    assert wall(card["due"]) == "2026-10-11 00:00:00"
    sched = get_scheduler("leitner")

    zone("Asia/Tokyo")                     # the instant is 14:00 on Oct 11 here
    assert not sched.is_due(card, at("2026-10-10 23:59:59"))
    assert sched.is_due(card, at("2026-10-11 00:00"))
    zone("Europe/Berlin")                  # 07:00 on Oct 11
    assert not sched.is_due(card, at("2026-10-10 23:59:59"))
    assert sched.is_due(card, at("2026-10-11 00:00"))
    zone("America/Los_Angeles")            # 22:00 on Oct 10
    assert not sched.is_due(card, at("2026-10-09 23:59:59"))
    assert sched.is_due(card, at("2026-10-10 00:00"))
    for name in ("Pacific/Kiritimati", "Pacific/Pago_Pago", "UTC"):
        zone(name)
        assert sched.is_due(card, card["due"])    # never later than the stored instant
