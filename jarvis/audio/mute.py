"""Half-duplex mute: ignore the microphone while Jarvis is speaking.

On a desktop with speakers there is no echo cancellation, so Jarvis hears
himself, whisper transcribes it, and the min-words rule reads it as the user
barging in. The result is self-interruption: clipped, stuttering speech.

This strategy mutes user frames from ``BotStartedSpeakingFrame`` until
``BotStoppedSpeakingFrame``. It costs barge-in, so it is only for speaker
setups; with headphones or a phone call (which does its own echo
cancellation) leave it off and keep full duplex.
"""
from __future__ import annotations

from pipecat.frames.frames import BotStartedSpeakingFrame, BotStoppedSpeakingFrame, Frame
from pipecat.turns.user_mute.base_user_mute_strategy import BaseUserMuteStrategy


class BotSpeakingUserMuteStrategy(BaseUserMuteStrategy):
    def __init__(self) -> None:
        super().__init__()
        self._bot_speaking = False

    @property
    def bot_speaking(self) -> bool:
        return self._bot_speaking

    async def process_frame(self, frame: Frame) -> bool:
        await super().process_frame(frame)
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
        return self._bot_speaking
