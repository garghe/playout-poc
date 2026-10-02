from playout.playlist import Event
from playout.schedule import countdowns, preair_checks, project


def ev(uid, dur, hard=None, kind="programme", cat="", brk=None, first=False):
    return Event(uid=uid, id=uid, title=uid, kind=kind, source="srt://x" if kind == "live" else f"{uid}.mp4",
                 category=cat, duration=dur, hard_start=hard, start_mode="hard" if hard else "follow",
                 break_id=brk, break_title=brk, break_first=first, media_duration=dur, media_info="h264 1x1, aac 2ch 48000Hz")


def test_follow_on_projection():
    evs = [ev("a", 10), ev("b", 20), ev("c", 5)]
    s = project(evs, 0, 1000, 1000)
    assert [(x.start, x.end) for x in s] == [(1000, 1010), (1010, 1030), (1030, 1035)]


def test_hard_start_gap_and_truncation():
    evs = [ev("a", 10), ev("b", 5, hard=1020), ev("c", 30), ev("d", 10, hard=1040)]
    s = project(evs, 0, 1000, 1000)
    assert s[1].gap_before == 10 and s[1].start == 1020
    assert s[2].truncated_by == 15 and s[2].end == 1040   # c ran into d's hard start
    assert s[3].start == 1040


def test_countdowns_break_and_category():
    evs = [ev("a", 10, cat="news"), ev("s1", 10, kind="spot", brk="B1", first=True), ev("f", 60, cat="film")]
    s = project(evs, 0, 1000, 1000)
    cds = {c["key"]: c["at"] for c in countdowns(evs, s, 1000)}
    assert cds["break"] == 1010 and cds["cat:film"] == 1020


def test_preair_checks_flag_problems():
    a = ev("a", 10)
    short = ev("short", 30)
    short.media_duration = 12
    missing = ev("missing", 10)
    missing.media_info = "MISSING"
    issues = preair_checks([a, short, missing, ev("late", 5, hard=10**10)], now=1000)
    msgs = {(i["uid"], i["level"]) for i in issues}
    assert ("short", "warning") in msgs
    assert ("missing", "error") in msgs
    assert ("late", "warning") in msgs   # big gap before hard start
