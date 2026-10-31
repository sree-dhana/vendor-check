"""F1: loose input parsing."""
import pytest

from checks import extract_ids

GST = "27AAPFU0939F1ZV"


@pytest.mark.parametrize("text", [
    "GSTIN 27 AAPFU 0939 F1ZV",
    "gstin 27-aapfu-0939-f1zv",
    "27.AAPFU.0939.F1ZV",
    "check 27 aapfu 0939 f1zv please",
    "GSTIN No.27AAPFU0939F1ZV",
])
def test_spaced_dashed_dotted_and_lowercase_gstin(text):
    assert extract_ids(text)[0]["gstin"] == [GST]


def test_lowercase_and_spaced_ifsc_and_dashed_pan():
    found, _ = extract_ids("ifsc sbin0000001")
    assert found["ifsc"] == ["SBIN0000001"]
    assert extract_ids("IFSC SBIN 0000001")[0]["ifsc"] == ["SBIN0000001"]
    assert extract_ids("sbin 0000001")[0]["ifsc"] == ["SBIN0000001"]  # the whole message
    assert extract_ids("PAN AAPFU-0939-F")[0]["pan"] == ["AAPFU0939F"]
    assert extract_ids("pan aapfu 0939 f")[0]["pan"] == ["AAPFU0939F"]


def test_two_ids_in_one_line():
    found, _ = extract_ids("check 27 aapfu 0939 f1zv pan aapfu0939f ifsc yesb0dnb002")
    assert found["gstin"] == [GST]
    assert found["pan"] == ["AAPFU0939F"]
    assert found["ifsc"] == ["YESB0DNB002"]


def test_no_false_positives():
    assert extract_ids("invoice 123456")[0]["pin"] == []
    assert extract_ids("invoice 560 001 due")[0]["pin"] == []
    # PAN- and IFSC-shaped runs of several words need their keyword
    assert extract_ids("hello 1234 a and more")[0]["pan"] == []
    assert extract_ids("call from 0123456 now")[0]["ifsc"] == []
    # a keyword never becomes part of an ID
    assert extract_ids("pan ab 1234 c")[0]["pan"] == []


def test_spaced_pin_after_keyword():
    assert extract_ids("pincode 560 001")[0]["pin"] == ["560001"]
