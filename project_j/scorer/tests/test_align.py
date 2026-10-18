from helpers import VOCAB, posteriors

from project_j_scorer.align import ctc_align, edit_ops, greedy_decode
from project_j_scorer.rules import same

M, IY = VOCAB["m"], VOCAB["iː"]


def test_alignment_gives_each_target_its_frames():
    lp = posteriors(["<pad>", "m", "m", "<pad>", "iː", "<pad>"])
    assert ctc_align(lp, [M, IY], blank=0) == [(1, 3), (4, 5)]


def test_repeated_targets_need_a_blank_between_them():
    assert ctc_align(posteriors(["m", "<pad>", "m"]), [M, M], blank=0) == [(0, 1), (2, 3)]
    assert ctc_align(posteriors(["m", "m"]), [M, M], blank=0) is None


def test_long_alignment_keeps_backpointers_valid():
    phones = ["m", "iː"] * 150
    frames = [p for p in phones for _ in range(10)]
    spans = ctc_align(posteriors(frames), [VOCAB[p] for p in phones], blank=0)
    assert len(spans) == 300 and spans[-1] == (2990, 3000)
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:], strict=False))


def test_greedy_decode_collapses_runs_and_drops_blanks():
    lp = posteriors(["<pad>", "m", "m", "<pad>", "m", "iː", "iː"])
    assert greedy_decode(lp, frozenset({0, 1, 2, 3})) == [(M, 1, 3), (M, 4, 5), (IY, 5, 7)]


def test_edit_ops_treat_group_variants_as_matches():
    assert edit_ops(["m", "ɐ"], ["m", "ə"], same) == [("match", 0, 0), ("match", 1, 1)]
    assert edit_ops(["m", "iː", "ə"], ["m", "ə"], same) == [
        ("match", 0, 0),
        ("del", 1, None),
        ("match", 2, 1),
    ]
    assert edit_ops(["m", "ə"], ["m", "k", "ə"], same) == [
        ("match", 0, 0),
        ("ins", None, 1),
        ("match", 1, 2),
    ]
