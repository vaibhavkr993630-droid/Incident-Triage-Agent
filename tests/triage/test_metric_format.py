from triage.tools.metrics import format_result, num, summarize_series, trend


def test_numbers_are_rounded_and_nan_becomes_none():
    assert num("0.123456789") == 0.1235
    assert num("NaN") is None and num("+Inf") is None


def test_trend_words():
    assert trend([1, 1, 1, 5, 5, 5]) == "rising"
    assert trend([5, 5, 5, 1, 1, 1]) == "falling"
    assert trend([3, 3.1, 2.9, 3, 3.05, 3]) == "flat"


def test_range_series_are_summarized_not_dumped():
    points = [[t, str(v)] for t, v in enumerate([0.002] * 40 + [0.08] * 20)]
    s = summarize_series(points)
    assert (s["first"], s["last"], s["min"], s["max"]) == (0.002, 0.08, 0.002, 0.08)
    assert s["trend"] == "rising"
    assert len(s["samples"]) == 8


def test_instant_vectors_sorted_largest_first_and_capped():
    data = {
        "resultType": "vector",
        "result": [{"metric": {"route": f"/r{i}"}, "value": [0, str(i)]} for i in range(15)],
    }
    out = format_result(data, ranged=False)
    assert out["series_total"] == 15
    assert len(out["series"]) == 10
    assert out["series"][0] == {"labels": {"route": "/r14"}, "value": 14.0}


def test_empty_results_explain_themselves():
    out = format_result({"resultType": "vector", "result": []}, ranged=False)
    assert out["series"] == [] and "list_metrics" in out["note"]


def test_scalars():
    assert format_result({"resultType": "scalar", "result": [0, "2"]}, ranged=False) == {
        "result_type": "scalar",
        "value": 2.0,
    }
