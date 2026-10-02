from zoneinfo import ZoneInfo

import pytest

from playout import playlist as P
from playout.playlist import PlaylistError, fmt_tc, parse_tc

TZ = ZoneInfo("Europe/London")


def test_timecodes():
    assert parse_tc("00:01:00:00") == 60
    assert parse_tc("00:00:01:12") == pytest.approx(1.48)
    assert parse_tc("01:00:00") == 3600
    assert parse_tc("00:00:01.5") == 1.5
    assert parse_tc(10) == 10
    assert parse_tc("") is None
    assert fmt_tc(61.04) == "00:01:01:01"
    with pytest.raises(PlaylistError):
        parse_tc("1:2")


def test_json_breaks_flatten():
    pl = P.from_json("""{"channel":"C","date":"2026-10-02","items":[
      {"id":"1","title":"Prog","source":"a.mp4","category":"Film"},
      {"id":"B","type":"break","spots":[{"id":"s1","source":"x.mp4","duration":10},
                                         {"id":"s2","source":"y.mp4","duration":20}]},
      {"id":"2","type":"live","source":"srt://:9002?mode=listener","start":"20:00:00","duration":"00:30:00"}
    ]}""", TZ)
    assert [e.uid for e in pl.events] == ["1", "B/s1", "B/s2", "2"]
    s1, s2 = pl.events[1], pl.events[2]
    assert s1.break_first and not s1.break_last and s2.break_last
    assert s1.break_duration == 30 and s1.scte
    assert pl.events[0].category == "film"
    live = pl.events[3]
    assert live.start_mode == "hard" and live.duration == 1800
    assert live.hard_start is not None


def test_csv_spots_attach_to_break():
    pl = P.from_csv("id,title,type,source,duration\n1,A,programme,a.mp4,\nB,Brk,break,,\n"
                    "s1,S,spot,s.mp4,10\n2,B,programme,b.mp4,\n", TZ)
    assert [e.uid for e in pl.events] == ["1", "B/s1", "2"]


@pytest.mark.parametrize("bad, msg", [
    ('{"items":[]}', "no items"),
    ('{"items":[{"type":"nope","source":"x"}]}', "type must be"),
    ('{"items":[{"source":"x"},{"id":"001","source":"y"}]}', "duplicate"),
    ('{"items":[{"type":"break","spots":[]}]}', "no spots"),
    ('{"items":[{"id":"1"}]}', "missing"),
    ('not json', "invalid JSON"),
])
def test_validation(bad, msg):
    with pytest.raises(PlaylistError, match=msg):
        P.from_json(bad, TZ)
