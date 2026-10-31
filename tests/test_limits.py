"""F8: length cap and rate limits."""
import format as fmt
import limits
from conftest import MemStore


def test_long_message_gets_a_friendly_note(no_net):
    out = fmt.reply_for_text("x" * 1001, MemStore())
    assert out == limits.TOO_LONG_TEXT % 1001
    assert fmt.reply_for_text("x" * 1000, MemStore()) == fmt.NO_ID_TEXT


def test_mention_does_not_count_towards_length(no_net):
    text = "@agent1qfgjfnaffngvss45953n0x362p0hyfj7xpag073lppuqnnsdruyzg6750aq " + "x" * 990
    assert fmt.reply_for_text(text, MemStore()) == fmt.NO_ID_TEXT


def test_message_rate_limit_per_sender(no_net):
    store = MemStore()
    for i in range(20):
        assert fmt.reply_for_text("hello", store, sender="a", now=1000 + i) == fmt.HELP_TEXT
    assert fmt.reply_for_text("hello", store, sender="a", now=1020) == limits.RATE_LIMITED_TEXT
    # another sender is unaffected, and the window slides
    assert fmt.reply_for_text("hello", store, sender="b", now=1020) == fmt.HELP_TEXT
    assert fmt.reply_for_text("hello", store, sender="a", now=1000 + 600) == fmt.HELP_TEXT


def test_llm_rate_limit():
    store = MemStore()
    assert all(limits.allow_llm(store, "a", now=i) for i in range(5))
    assert not limits.allow_llm(store, "a", now=10)
    assert limits.allow_llm(store, "a", now=3600)


def test_rate_limit_survives_broken_storage():
    class Broken:
        def get(self, *a):
            raise RuntimeError

        def set(self, *a):
            raise RuntimeError

    assert limits.allow_message(Broken(), "a")


def test_storage_holds_only_timestamps(no_net):
    store = MemStore()
    fmt.reply_for_text("secret invoice text 12345", store, sender="a", now=5)
    assert store.d == {"rate:msg:a": [5]}
