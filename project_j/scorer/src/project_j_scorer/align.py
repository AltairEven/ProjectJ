"""CTC forced alignment, greedy decoding and phone edit alignment; numpy only."""

from collections.abc import Callable, Sequence
from typing import Literal

import numpy as np

Op = tuple[Literal["match", "sub", "del", "ins"], int | None, int | None]


def ctc_align(
    log_probs: np.ndarray, targets: Sequence[int], blank: int
) -> list[tuple[int, int]] | None:
    """Viterbi path through blank/target states; None when there are too few frames."""
    frames, states = len(log_probs), 2 * len(targets) + 1
    if not targets or frames == 0:
        return None
    tokens = np.full(states, blank)
    tokens[1::2] = targets
    skip = np.zeros(states, dtype=bool)
    skip[3::2] = tokens[3::2] != tokens[1:-2:2]  # May jump the blank between different targets.
    emit = log_probs[:, tokens].astype(np.float64)
    dp = np.full(states, -np.inf)
    dp[0], dp[1] = emit[0, 0], emit[0, 1]
    back = np.zeros((frames, states), dtype=np.int32)  # int8 overflowed in the spike.
    index = np.arange(states)
    for t in range(1, frames):
        before1 = np.concatenate(([-np.inf], dp[:-1]))
        before2 = np.where(skip, np.concatenate(([-np.inf, -np.inf], dp[:-2])), -np.inf)
        candidates = np.stack([dp, before1, before2])
        choice = candidates.argmax(axis=0)  # Ties prefer staying, then one step, then the jump.
        dp = candidates[choice, index] + emit[t]
        back[t] = index - choice
    end = states - 1 if dp[states - 1] >= dp[states - 2] else states - 2
    if dp[end] == -np.inf:
        return None
    path = np.empty(frames, dtype=np.int64)
    path[-1] = end
    for t in range(frames - 1, 0, -1):
        path[t - 1] = back[t, path[t]]
    spans: list[list[int]] = [[-1, -1] for _ in targets]
    for t, state in enumerate(path):
        if state % 2:
            span = spans[state // 2]
            span[0] = t if span[0] < 0 else span[0]
            span[1] = t + 1
    return [(start, end) for start, end in spans]


def greedy_decode(log_probs: np.ndarray, ignore: frozenset[int]) -> list[tuple[int, int, int]]:
    """Best token per frame, runs collapsed, blanks and special tokens dropped."""
    best = log_probs.argmax(axis=1)
    decoded, start = [], 0
    for t in range(1, len(best) + 1):
        if t == len(best) or best[t] != best[start]:
            if int(best[start]) not in ignore:
                decoded.append((int(best[start]), start, t))
            start = t
    return decoded


def edit_ops(ref: Sequence[str], hyp: Sequence[str], same: Callable[[str, str], bool]) -> list[Op]:
    """Levenshtein alignment where equivalent phones cost nothing."""
    n, m = len(ref), len(hyp)
    cost = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        cost[i][0] = i
    for j in range(1, m + 1):
        cost[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost[i][j] = min(
                cost[i - 1][j - 1] + (0 if same(ref[i - 1], hyp[j - 1]) else 1),
                cost[i - 1][j] + 1,
                cost[i][j - 1] + 1,
            )
    ops: list[Op] = []
    i, j = n, m
    while i or j:
        if i and j:
            equal = same(ref[i - 1], hyp[j - 1])
            if cost[i][j] == cost[i - 1][j - 1] + (0 if equal else 1):
                ops.append(("match" if equal else "sub", i - 1, j - 1))
                i, j = i - 1, j - 1
                continue
        if i and cost[i][j] == cost[i - 1][j] + 1:
            ops.append(("del", i - 1, None))
            i -= 1
        else:
            ops.append(("ins", None, j - 1))
            j -= 1
    return ops[::-1]
