"""Phone posteriors + reference words → per-word scores and statuses (thresholds provisional-1)."""

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from statistics import mean

import numpy as np

from project_j_scorer.align import ctc_align, edit_ops, greedy_decode
from project_j_scorer.rules import (
    ABSENT_BELOW,
    BLANK,
    FRAME_SECONDS,
    FUNCTION_WORDS,
    INSERT_MIN_PHONES,
    MIN_DECODED_PHONES,
    MISREAD_BELOW,
    OMIT_DELETED_AT_LEAST,
    PAUSE_SECONDS,
    SPECIALS,
    same,
    variants,
)
from project_j_scorer.text import RefWord


def unknown(reason: str) -> dict:
    return {"status": "unknown", "reason": reason}


def score_posteriors(
    log_probs: np.ndarray,
    vocab: Mapping[str, int],
    words: Sequence[RefWord],
    frame_seconds: float = FRAME_SECONDS,
) -> dict:
    specials = frozenset(vocab[token] for token in SPECIALS if token in vocab)
    inverse = {index: token for token, index in vocab.items()}
    targets: list[tuple[int, str]] = []  # (word index, vocabulary phone)
    for index, word in enumerate(words):
        for phone in word.phones:
            token = phone if phone in vocab else phone.replace("ː", "")
            if token in vocab:  # Unmappable phones are skipped, never judged.
                targets.append((index, token))
    if not targets:
        return unknown("failed")
    decoded = greedy_decode(log_probs, specials)
    if len(decoded) < MIN_DECODED_PHONES:
        return unknown("no_speech")
    # Align on each expected phone or its best equivalent, one column per distinct phone so that
    # identical neighbours still need a blank between them.
    columns = list(dict.fromkeys(token for _, token in targets))
    emission = np.column_stack(
        [log_probs[:, vocab[BLANK]]]
        + [log_probs[:, [vocab[v] for v in variants(c) if v in vocab]].max(axis=1) for c in columns]
    )
    spans = ctc_align(emission, [columns.index(token) + 1 for _, token in targets], 0)
    if spans is None:
        return unknown("too_short")

    # GOP: expected phone (or an equivalent) against the strongest non-special phone per frame.
    others = [i for i in range(log_probs.shape[1]) if i not in specials]
    strongest = log_probs[:, others].max(axis=1)
    phone_scores: dict[int, list[float]] = {i: [] for i in range(len(words))}
    word_spans: dict[int, tuple[int, int]] = {}
    target_scores: list[float] = []
    for (index, token), (start, end) in zip(targets, spans, strict=True):
        ids = [vocab[v] for v in variants(token) if v in vocab]
        expected = log_probs[start:end, ids].max(axis=1)
        gop = float(np.mean(expected - strongest[start:end]))
        phone_scores[index].append(100 * math.exp(gop))
        target_scores.append(100 * math.exp(gop))
        first = word_spans.get(index, (start, end))[0]
        word_spans[index] = (first, end)

    # Free decoding against the reference: deletions mean omitted, long insertion runs extra.
    ops = edit_ops([token for _, token in targets], [inverse[d[0]] for d in decoded], same)
    deleted: Counter[int] = Counter()
    inserted: list[tuple[int, list[str]]] = []
    run: list[str] = []
    last = -1
    for kind, ref, hyp in ops + [("end", None, None)]:
        if kind == "ins":
            run.append(inverse[decoded[hyp][0]])
            continue
        if len(run) >= INSERT_MIN_PHONES:
            inserted.append((last, run))
        run = []
        if ref is not None:
            last = targets[ref][0]
            # Greedy decoding lets blank swallow short phones the alignment still finds clearly.
            if kind == "del" and target_scores[ref] < ABSENT_BELOW:
                deleted[last] += 1

    entries, read, spoken = [], [], False
    for at, phones in inserted:
        if at < 0:
            entries.append(extra(phones))
    for index, word in enumerate(words):
        scores = phone_scores[index]
        if not scores:  # Nothing to judge: lenient.
            entries.append(
                {"word": word.text, "accuracy": 100.0, "min_phone": None, "error": "none"}
            )
            read.append(100.0)
        else:
            accuracy, lowest = mean(scores), min(scores)
            if deleted[index] / len(scores) >= OMIT_DELETED_AT_LEAST:
                error = "omitted"
            elif word.text.lower() not in FUNCTION_WORDS and lowest < MISREAD_BELOW:
                error = "mispronounced"
            else:
                error = "none"
            entries.append(
                {
                    "word": word.text,
                    "accuracy": round(accuracy, 1),
                    "min_phone": round(lowest, 1),
                    "error": error,
                }
            )
            if error != "omitted":
                read.append(accuracy)
                spoken = True
        entries += [extra(phones) for at, phones in inserted if at == index]
    if not spoken:  # Every judged word omitted.
        return unknown("no_speech")

    ordered = [word_spans[i] for i in range(len(words)) if i in word_spans]
    pauses = sum(
        (b[0] - a[1]) * frame_seconds >= PAUSE_SECONDS
        for a, b in zip(ordered, ordered[1:], strict=False)
    )
    minutes = (ordered[-1][1] - ordered[0][0]) * frame_seconds / 60
    return {
        "status": "scored",
        "words": entries,
        "accuracy": round(mean(read), 1),
        "completeness": round(100 * len(read) / len(words), 1),
        "pauses": int(pauses),
        "words_per_minute": round(len(read) / minutes, 1) if minutes > 0 else None,
    }


def extra(phones: list[str]) -> dict:
    return {"word": " ".join(phones), "accuracy": 0.0, "min_phone": None, "error": "inserted"}
