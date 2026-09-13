from pipecat.frames.frames import BotStartedSpeakingFrame, BotStoppedSpeakingFrame, TranscriptionFrame

from jarvis.audio.mute import BotSpeakingUserMuteStrategy


async def test_muted_only_while_bot_speaks():
    s = BotSpeakingUserMuteStrategy()
    assert await s.process_frame(TranscriptionFrame(text="hi", user_id="u", timestamp="0")) is False
    assert await s.process_frame(BotStartedSpeakingFrame()) is True
    assert await s.process_frame(TranscriptionFrame(text="echo", user_id="u", timestamp="0")) is True
    assert await s.process_frame(BotStoppedSpeakingFrame()) is False
    assert s.bot_speaking is False
