from project_j_scorer.model import chunk_spans

RATE = 16000


def test_short_audio_is_one_window():
    assert chunk_spans(10 * RATE) == [(0, 10 * RATE, 0, 10 * RATE)]


def test_long_audio_windows_cover_every_sample_once():
    n = 100 * RATE
    spans = chunk_spans(n)
    keeps = [(k0, k1) for _, _, k0, k1 in spans]
    assert keeps[0][0] == 0 and keeps[-1][1] == n
    assert all(a[1] == b[0] for a, b in zip(keeps, keeps[1:], strict=False))
    assert all(w0 <= k0 < k1 <= w1 and w1 - w0 <= 44 * RATE for w0, w1, k0, k1 in spans)
