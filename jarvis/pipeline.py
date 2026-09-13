"""Pipeline assembly: transport -> STT -> user aggregator -> LLM -> narrator
-> TTS -> transport -> assistant aggregator.

Barge-in policy lives in Pipecat's turn strategies, not in custom code:

- ``WakePhraseUserTurnStartStrategy``: nothing reaches the LLM until the user
  says the wake phrase; afterwards the mic stays "awake" for ``wake_timeout``
  seconds of inactivity (refreshed by any speech).
- ``MinWordsUserTurnStartStrategy``: while the bot is speaking, a transcript
  needs at least ``min_words`` words to interrupt it. A honk transcribes to
  nothing; road noise to nothing; a stray word to one. When the bot is quiet,
  a single word starts a turn.
- ``SpeechTimeoutUserTurnStopStrategy``: the turn ends ``stop_secs`` after
  the user pauses, once the transcript has arrived.

Silero VAD sits inside the user aggregator and broadcasts speech-start/stop
frames upstream to the segmented Whisper STT.
"""
from __future__ import annotations

from dataclasses import dataclass

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams as PipecatVADParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.turns.user_start import (
    MinWordsUserTurnStartStrategy,
    WakePhraseUserTurnStartStrategy,
)
from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from jarvis.narrator import Narrator
from jarvis.prompt import SYSTEM_PROMPT
from jarvis.session import JarvisSession
from jarvis.tools.handlers import build_tools


@dataclass(frozen=True)
class TurnConfig:
    """Everything that decides when the user has the floor."""

    wake_phrases: tuple[str, ...] = ("jarvis",)
    wake_timeout_secs: float = 20.0
    single_activation: bool = False
    min_words: int = 3
    vad_confidence: float = 0.7
    vad_start_secs: float = 0.3
    vad_stop_secs: float = 0.5
    vad_min_volume: float = 0.6
    # Turn ends this long after VAD stop once the transcript is in. Whisper
    # itself takes 0.3-0.9 s, so this rarely adds anything.
    user_speech_timeout_secs: float = 0.3
    # Fallback: end the user turn this long after it started if no stop
    # strategy fires (e.g. no VAD frames at all in a text-driven test).
    user_turn_stop_timeout_secs: float = 5.0


@dataclass
class BuiltPipeline:
    pipeline: Pipeline
    context: LLMContext
    narrator: Narrator
    user_aggregator: object
    assistant_aggregator: object


def build_user_turn_strategies(config: TurnConfig) -> UserTurnStrategies:
    return UserTurnStrategies(
        start=[
            WakePhraseUserTurnStartStrategy(
                phrases=list(config.wake_phrases),
                timeout=config.wake_timeout_secs,
                single_activation=config.single_activation,
            ),
            MinWordsUserTurnStartStrategy(min_words=config.min_words),
        ],
        stop=[
            SpeechTimeoutUserTurnStopStrategy(
                user_speech_timeout=config.user_speech_timeout_secs
            )
        ],
    )


def build_pipeline(
    transport,
    session: JarvisSession,
    *,
    llm=None,
    stt=None,
    tts=None,
    config: TurnConfig | None = None,
    vad: bool = True,
) -> BuiltPipeline:
    """Assemble the voice pipeline.

    ``llm``, ``stt`` and ``tts`` are optional so tests can drive the turn
    logic and the narrator with pre-transcribed frames and no models loaded.
    ``vad=False`` likewise skips loading Silero.
    """
    config = config or TurnConfig()

    context = LLMContext(
        messages=[{"role": "system", "content": SYSTEM_PROMPT}],
        tools=build_tools(session),
    )

    vad_analyzer = None
    if vad:
        vad_analyzer = SileroVADAnalyzer(
            params=PipecatVADParams(
                confidence=config.vad_confidence,
                start_secs=config.vad_start_secs,
                stop_secs=config.vad_stop_secs,
                min_volume=config.vad_min_volume,
            )
        )

    aggregators = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=vad_analyzer,
            user_turn_strategies=build_user_turn_strategies(config),
            user_turn_stop_timeout=config.user_turn_stop_timeout_secs,
        ),
    )

    narrator = Narrator(name="Narrator")
    session.narrator = narrator

    processors = [transport.input()]
    if stt is not None:
        processors.append(stt)
    processors.append(aggregators.user())
    if llm is not None:
        processors.append(llm)
    processors.append(narrator)
    if tts is not None:
        processors.append(tts)
    processors.append(transport.output())
    processors.append(aggregators.assistant())

    return BuiltPipeline(
        pipeline=Pipeline(processors),
        context=context,
        narrator=narrator,
        user_aggregator=aggregators.user(),
        assistant_aggregator=aggregators.assistant(),
    )
