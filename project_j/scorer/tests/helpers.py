import numpy as np

from project_j_scorer.text import RefWord

TOKENS = [
    "<pad>", "<s>", "</s>", "<unk>", "m", "iː", "ə", "h", "æ", "z", "ɐ", "t", "ɔɪ", "k", "ɑːɹ",
    "iə", "ɚ",
]  # fmt: skip
VOCAB = {token: i for i, token in enumerate(TOKENS)}
WORDS = [
    RefWord("Mia", ("m", "iː", "ə")),
    RefWord("has", ("h", "æ", "z")),
    RefWord("a", ("ɐ",)),
    RefWord("toy", ("t", "ɔɪ")),
    RefWord("car", ("k", "ɑːɹ")),
]
PAUSE = "pause"  # 30 blank frames, 0.6 s


def posteriors(frames: list[str]) -> np.ndarray:
    """One token per frame holds 0.96 of the mass, a neighbour 0.02, the rest is spread."""
    lp = np.full((len(frames), len(VOCAB)), np.log(0.02 / (len(VOCAB) - 2)), dtype=np.float32)
    last = None
    for t, token in enumerate(frames):
        if token.endswith("?"):  # A weak phone: blank wins the frame by a little.
            lp[t, VOCAB["<pad>"]], lp[t, VOCAB[token[:-1]]] = np.log(0.5), np.log(0.45)
            continue
        if token.endswith("~"):  # A faint phone: blank dominates, the phone is still the top one.
            lp[t, VOCAB["<pad>"]], lp[t, VOCAB[token[:-1]]] = np.log(0.97), np.log(0.005)
            continue
        lp[t, VOCAB[token]] = np.log(0.96)
        if token == "<pad>" and last:  # Like real CTC output, blanks echo the previous phone.
            lp[t, VOCAB[last]] = np.log(0.02)
        elif token != "<pad>":
            lp[t, VOCAB["<pad>"]] = np.log(0.02)
            last = token
    return lp


def speak(*items, gap=2, lead=5) -> list[str]:
    """Each item is a phone tuple held 3 frames per phone, or PAUSE."""
    frames = ["<pad>"] * lead
    for item in items:
        if item == PAUSE:
            frames += ["<pad>"] * 30
            continue
        for phone in item:
            frames += [phone] * 3
        frames += ["<pad>"] * gap
    return frames + ["<pad>"] * lead
