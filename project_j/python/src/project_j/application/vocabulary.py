"""Coordinator use cases of the student's words: the lookup after reading, its audio, and the
learner record written once the class is sealed (design/learning/profile.md)."""

import asyncio
import logging
from collections.abc import Coroutine
from contextlib import aclosing
from datetime import UTC, datetime
from time import perf_counter

from sqlalchemy.exc import SQLAlchemyError

from project_j.application.documents import document, identity, publication, successor
from project_j.classroom.harness import Harness
from project_j.contracts import Document
from project_j.learning.lookup import accepted, lookup_request
from project_j.learning.vocabulary import (
    LookedUp,
    VocabularyBody,
    WordEntry,
    update_after_lesson,
)
from project_j.platform.storage import SaveKey
from project_j.platform.tts.contracts import SynthesisFailed
from project_j.platform.tts.recorded import encode_wav

log = logging.getLogger(__name__)


class Vocabulary:
    vocabulary: VocabularyBody  # The learner's record as loaded at class start.
    word_audio: dict[str, bytes]  # WAV by lemma, held in memory while teaching.
    word_jobs: set[asyncio.Task]

    def spawn(self, work: Coroutine) -> None:
        job = asyncio.get_running_loop().create_task(work)
        self.word_jobs.add(job)
        job.add_done_callback(self.word_jobs.discard)

    def after_control(self, core: Harness, command: str) -> None:
        """Reading finished with marked words: look them up while the first question is asked."""
        pending = core.pending_words() if command == "finish_reading" else []
        if not pending:
            return
        for item in pending:  # The page shows "getting ready" before the lookup runs.
            core.box[item["word"].lower()]["status"] = "pending"
        self.spawn(self.lookup_words(core))

    def regenerate_word(self, surface: str) -> None:
        core = self.core
        if core is None or surface not in core.box:
            raise LookupError("no such word in this class's box")
        core.box[surface]["status"] = "pending"
        self.spawn(self.lookup_words(core, only={surface}, reuse=False))

    def word_audio_of(self, lemma: str) -> bytes:
        try:
            return self.word_audio[lemma]
        except KeyError:
            raise LookupError("no audio for this word yet") from None

    def known_word(self, surface: str) -> WordEntry | None:
        """A recorded entry with a meaning whose lemma or marked forms match this surface."""
        for entry in self.vocabulary.words:
            if entry.definition and (surface == entry.lemma or surface in entry.forms):
                return entry
        return None

    async def lookup_words(self, core: Harness, only: set[str] | None = None, reuse: bool = True):
        started = perf_counter()
        pending = [
            item for item in core.pending_words() if only is None or item["word"].lower() in only
        ]
        reused, requested = 0, []
        for item in pending:
            key = item["word"].lower()
            entry = self.known_word(key) if reuse else None
            if entry is None:
                requested.append(item)
                continue
            audio = entry.lemma in self.word_audio  # Read before class; no disk while teaching.
            core.set_word(
                key,
                LookedUp(
                    **(item | entry.model_dump(include={"lemma", "part_of_speech", "definition"}))
                ),
                audio,
            )
            reused += 1
        ok = failed = 0
        if requested:
            reply = await self.models.run(lookup_request(requested))
            found = accepted(reply.output if reply.ok else None, requested)
            for item in requested:
                key = item["word"].lower()
                definition = found[key]
                if definition is None:
                    core.set_word(key, None, False)
                    failed += 1
                    continue
                wav = await self.synthesize(definition.lemma)
                if wav is not None:
                    self.word_audio[definition.lemma] = wav
                looked = LookedUp(
                    **item,
                    lemma=definition.lemma,
                    part_of_speech=definition.part_of_speech,
                    definition=definition.definition,
                )
                core.set_word(key, looked, wav is not None)
                ok += 1
        log.info(
            "vocabulary lookup words=%d reused=%d requested=%d ok=%d failed=%d elapsed_ms=%d",
            len(pending),
            reused,
            len(requested),
            ok,
            failed,
            round((perf_counter() - started) * 1000),
        )

    async def synthesize(self, text: str) -> bytes | None:
        """One word as a WAV, or None when the synthesizer failed."""
        started = perf_counter()
        pcm = bytearray()
        try:
            async with aclosing(self.synthesizer.stream(text)) as live:
                async for chunk in live:
                    pcm.extend(chunk)
            outcome, wav = "completed", encode_wav(bytes(pcm), self.synthesizer.sample_rate)
        except SynthesisFailed as error:
            outcome, wav = f"failed:{error.reason}", None
        log.info(
            "vocabulary synth outcome=%s elapsed_ms=%d",
            outcome,
            round((perf_counter() - started) * 1000),
        )
        return wav

    # The learner record, written once the class is sealed (never while teaching).

    def vocabulary_record(self, learner_id: str) -> tuple[Document | None, VocabularyBody]:
        saved = self.store.current(identity(learner_id, "vocabulary"), learner_id)
        return saved, VocabularyBody.model_validate(saved.body) if saved else VocabularyBody()

    def save_vocabulary(self, saved: Document | None, body: VocabularyBody, learner_id, key, why):
        """One new revision (or the first record) under key; a second save of the same key is
        answered by its receipt."""
        if self.store.receipt(key, learner_id):
            return
        obj = (
            successor(saved, key.operation_id, body)
            if saved
            else document(
                "vocabulary", identity(learner_id, "vocabulary"), key.operation_id, learner_id, body
            )
        )
        self.store.save(key, learner_id, [obj], (publication(obj, why),))

    def record_vocabulary(self, core: Harness) -> None:
        """Marked words gain marks, words met unmarked gain exits; failures only log."""
        learner_id = core.learner_id
        key = SaveKey(
            operation_id=identity(core.activity_id, "vocabulary"),
            step_id="vocabulary",
            source_revision=1,
        )
        try:
            saved, body = self.vocabulary_record(learner_id)
            marked = []
            for value in core.box.values():
                ready = value["status"] == "ready"  # Otherwise the word is kept without a meaning.
                wav = self.word_audio.get(value["lemma"]) if ready else None
                meaning = {
                    "lemma": value["lemma"],
                    "part_of_speech": value["part_of_speech"],
                    "definition": value["definition"],
                    "audio": self.files.binary_asset(wav, "wav") if wav else None,
                }
                marked.append(
                    LookedUp(
                        word=value["word"], sentence=value["sentence"], **(meaning if ready else {})
                    )
                )
            texts = list(self.material_cache.values())
            finished = any(event["kind"] == "reading_finished" for event in core.events)
            story = "\n".join(text.partition("\n")[2] for text in texts) if finished else None
            title = texts[0].partition("\n")[0] if texts else ""
            body, counts = update_after_lesson(
                body, tuple(marked), story, title, datetime.now(UTC).date(), core.activity_id
            )
            if any(counts.values()):
                self.save_vocabulary(
                    saved, body, learner_id, key, "Words marked in class and words met again"
                )
            log.info(
                "vocabulary update new=%d marked=%d exits=%d mastered=%d total=%d saved=%s",
                counts["new"],
                counts["marked"],
                counts["exits"],
                counts["mastered"],
                len(body.words),
                "true" if any(counts.values()) else "false",
            )
        except (OSError, ValueError, SQLAlchemyError) as error:
            log.warning(
                "vocabulary update failed activity=%s %s", core.activity_id, type(error).__name__
            )

    async def regenerate_entry(self, request_id: str, learner_id: str, lemma: str) -> None:
        """Look one recorded word up again and replace its meaning and audio."""
        started = perf_counter()
        saved, body = self.vocabulary_record(learner_id)
        entry = next((item for item in body.words if item.lemma == lemma), None)
        if saved is None or entry is None:
            raise LookupError("This word is not in your vocabulary.")
        key = SaveKey(
            operation_id=identity(learner_id, f"vocabulary-regenerate:{request_id}"),
            step_id="vocabulary",
            source_revision=1,
        )
        if self.store.receipt(key, learner_id):
            return
        requested = [{"word": entry.forms[0], "sentence": entry.marks[-1].sentence}]
        reply = await self.models.run(lookup_request(requested))
        definition = accepted(reply.output if reply.ok else None, requested)[entry.forms[0]]
        if definition is None:
            log.warning(
                "vocabulary regenerate outcome=failed elapsed_ms=%d",
                round((perf_counter() - started) * 1000),
            )
            raise ValueError("The meaning could not be made; try again.")
        wav = await self.synthesize(lemma)
        updated = entry.model_copy(
            update={
                "part_of_speech": definition.part_of_speech,
                "definition": definition.definition,
                "audio": self.files.binary_asset(wav, "wav") if wav else entry.audio,
            }
        )
        words = tuple(updated if item.lemma == lemma else item for item in body.words)
        self.save_vocabulary(
            saved,
            body.model_copy(update={"words": words}),
            learner_id,
            key,
            "The student asked for a new meaning",
        )
        log.info(
            "vocabulary regenerate outcome=ok audio=%s elapsed_ms=%d",
            "true" if wav else "false",
            round((perf_counter() - started) * 1000),
        )

    def word_asset(self, learner_id: str, lemma: str) -> bytes:
        _, body = self.vocabulary_record(learner_id)
        entry = next((item for item in body.words if item.lemma == lemma and item.audio), None)
        if entry is None:
            raise LookupError("no audio for this word")
        return self.files.read_binary(entry.audio)
