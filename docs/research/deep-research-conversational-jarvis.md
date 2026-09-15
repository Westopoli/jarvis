Deep Research Report: Architecture and Design of a Local, Privacy-First Voice Assistant (Jarvis)

Executive Summary

The engineering of a fully localized, privacy-first voice assistant bounded by strict hardware constraints—specifically a single RTX 3060 with 12 GB of VRAM—presents a multifaceted systems architecture challenge. The specified technology stack utilizes Pipecat for real-time orchestration, faster-whisper for local Speech-to-Text (STT), Ollama running Qwen3:8B for Large Language Model (LLM) inference, Kokoro for Text-to-Speech (TTS), and Twilio for telephony transport. While this pipeline is highly capable, it is uniquely susceptible to conversational friction regarding turn-taking, barge-in recovery, and hardware resource contention.

This report provides an exhaustive analysis of three critical design domains required to elevate the system from a transactional command-line interface to a fluid conversational agent. The first domain investigates the transition from heuristic silence timeouts to semantic and acoustic End-of-Utterance (EOU) detection, allowing the assistant to distinguish between cognitive pauses and yielded turns. The second domain explores architectural patterns for enhancing conversational naturalness, including deterministic latency masking and robust interruption handling. Finally, the third domain addresses the physical limitations of the shared GPU, proposing stateful orchestration strategies to gracefully manage inevitable LLM eviction stalls without disrupting secondary background workloads.

Research Question 1: Turn Continuation vs. New Message

The reliance on a fixed silence timeout, such as Pipecat's SpeechTimeoutUserTurnStopStrategy, fundamentally degrades the naturalness of conversational voice agents. Human speech is inherently disfluent. Academic literature on conversational analysis, notably Shriberg's taxonomy of spontaneous speech, establishes that natural dialogue is heavily populated with filled pauses (e.g., "um," "uh"), unfilled pauses (silence), repetitions, and false starts. These disfluencies are not mere noise; they serve critical floor-holding functions, signaling to the listener that the speaker is retrieving information or planning an utterance and has not yet yielded the conversational floor. A static timeout applies a binary threshold to an analog, highly contextual signal. If the timeout is configured too aggressively (e.g., 500ms), the system repeatedly interrupts the user mid-thought, creating a false cutoff. If the timeout is extended to accommodate cognitive pauses (e.g., 2000ms), the assistant feels sluggish, introducing unnatural latency into the conversation.

To enable the assistant to distinguish between a mid-sentence pause and a true End-of-Utterance (EOU), the architecture must shift toward semantic and acoustic evaluation.

Open-Source EOU and Turn-Taking Solutions

Project / Component    Link    License    Description    Architecture Fit
Pipecat LocalSmartTurnAnalyzerV3    GitHub    BSD 2-Clause    An 8M parameter acoustic EOU model built natively into Pipecat. Analyzes raw audio prosody to predict turn completion.    High. Native integration. Runs on CPU via ONNX (~12ms latency), preserving GPU VRAM. Requires precise 16kHz audio routing.
Pipecat FilterIncompleteUserTurns    Docs    Apache 2.0    An LLM-judged semantic completion strategy. Prompts the LLM to output specific completion markers before generating text.    Medium. Native to Pipecat, but invoking the local Qwen3:8B model strictly for turn evaluation introduces TTFT (Time To First Token) latency.
LiveKit Turn Detector (v1)    HuggingFace    LiveKit Model License    State-of-the-art model fusing semantic text history with acoustic prosody cues, scoring 9.9% false-cutoff on eot-bench.    Incompatible. The license explicitly prohibits usage outside the LiveKit Agents framework. Cannot be used with Pipecat.
TurnWave    HuggingFace    MIT (Audio branch)    An open-source EOU model benchmarking competitively against LiveKit, evaluating acoustic features for end-of-turn prediction.    Low. Requires building custom Pipecat integrations and frame processors.
Voice Activity Projection (VAP)    GitHub    MIT    A research model by Ekstedt & Skantze utilizing dyadic attention networks on stereo audio to predict real-time backchanneling and turn-yielding.    Low. Highly experimental. Designed for continuous stereo dialogue tracking, requiring extensive engineering to adapt to half-duplex WebSockets.

Evaluation of Endpointing Paradigms

The central challenge in EOU detection is optimizing the Pareto frontier between false cutoffs and response latency. Standard Voice Activity Detection (VAD) baselines tested against the eot-bench dataset—a rigorous, multi-lingual corpus of human-to-agent interactions—demonstrate a severe 55.6% false cutoff rate when constrained to a 300ms latency budget. Surpassing this baseline requires analyzing either the acoustic properties of the audio or the semantic completeness of the transcript.

Acoustic EOU: Pipecat's LocalSmartTurnAnalyzerV3

The LocalSmartTurnAnalyzerV3 represents the most viable acoustic EOU solution for the specified stack. It operates directly on the raw audio waveform, utilizing a Whisper-Tiny encoder backbone to evaluate prosody, pitch, and rhythm. These paralinguistic cues are vital for endpointing because a speaker trailing off with a rising or sustained pitch typically implies continuation, whereas a falling pitch coupled with a decrease in volume strongly implies a yielded turn. By processing raw audio, the model bypasses the latency penalty of waiting for faster-whisper to finalize a transcript. Furthermore, the model is quantized to INT8, resulting in an 8MB footprint that executes on standard CPUs via the ONNX runtime in approximately 12 milliseconds. This offloads the entire acoustic evaluation from the heavily contended RTX 3060.

However, a critical implementation hazard exists regarding telephony audio formats. Twilio media streams natively transport 8kHz ‭$\mu$‬-law audio. If the Pipecat pipeline is explicitly configured with an audio_in_sample_rate of 8000, the TwilioFrameSerializer will output 8kHz PCM directly into the pipeline. The LocalSmartTurnAnalyzerV3 relies on a WhisperFeatureExtractor that is hardcoded to expect a 16kHz sampling rate. When fed 8kHz audio without internal resampling, the analyzer processes the speech at double speed with a distorted, upward-shifted pitch. This silent failure results in catastrophic classification flips—such as a confident "complete" prediction suddenly registering as "incomplete"—with zero runtime warnings. To utilize this model safely, the pipeline must retain the default audio_in_sample_rate of 16000, allowing the TwilioFrameSerializer to automatically upsample the inbound telephony stream before it reaches the turn analyzer.

Semantic EOU: Pipecat's FilterIncompleteUserTurns

Acoustic models occasionally fail when an utterance is prosodically complete but semantically hanging, or when a user explicitly requests a pause. To address this, Pipecat offers a semantic gate via the FilterIncompleteUserTurnStrategies. This orchestrator strategy injects a background instruction into the LLM's system prompt, requiring the model to assess the semantic completeness of the user's input and output a specific unicode marker before generating its response text.

The system relies on three distinct markers. The solid circle (●) indicates the user has finished, prompting the assistant to speak its generated text. The left-half circle (◐) indicates the user was cut off mid-thought, triggering an incomplete_short_timeout (defaulting to 5.0 seconds). During this window, the assistant suppresses its response, allowing the user to resume; if the silence persists, the LLM is automatically prompted to gently re-engage the user. Finally, the open circle (○) indicates an explicit request for time, such as the user stating "let me think about that." This triggers an incomplete_long_timeout (defaulting to 10.0 seconds) before re-prompting. While highly accurate at interpreting ambiguous utterances, this semantic approach forces the local Qwen3:8B model to generate tokens simply to decide if it should speak. In a contended GPU environment, relying exclusively on LLM evaluation for endpointing will introduce massive conversational lag.

Recommendation for Turn Continuation

A cascaded, hybrid architecture offers the optimal balance of accuracy, hardware efficiency, and implementation cost, establishing a middle ground between simplistic heuristics and heavy neural EOU models.

The primary gate must be the acoustic LocalSmartTurnAnalyzerV3, operating on the CPU to evaluate prosody and rhythm without taxing the GPU. This ensures rapid, immediate endpointing for standard utterances. Interwoven with this should be a lightweight heuristic layer built as a custom Pipecat frame processor immediately downstream of the faster-whisper STT instance. This processor should monitor interim transcripts for trailing lexical floor-holders (e.g., "um," "so," "let me check"). If a transcript matches this disfluency pattern, the processor dynamically emits a UserSpeakingFrame, which artificially extends the VAD window and overrides the acoustic model's decision to close the turn.

Finally, the FilterIncompleteUserTurns LLM strategy should be implemented strictly as a tertiary fallback. It should only be invoked for genuinely ambiguous cases that bypass both the acoustic model and the lexical heuristic. By positioning the LLM as the ultimate arbiter rather than the frontline detector, the architecture prevents premature responses to hanging thoughts while minimizing the frequency of TTFT latency penalties during routine interactions.

Research Question 2: Making a Local Voice Assistant More Conversational

Elevating a resource-constrained voice assistant beyond a voice-activated command-line tool requires mimicking the paralinguistic mechanisms of human conversation. This involves masking inherent pipeline processing latency with natural thinking sounds, managing barge-in recovery seamlessly, and maintaining a consistent persona despite the limitations of small-parameter models.

Conversational Enhancements Landscape

Technique / Component    Link    Description    Architecture Fit
Kokoro TTS (82M)    GitHub    A lightweight, highly natural TTS model running via ONNX. Supports audio streaming and rapid generation.    High. Already selected. Delivers ~300ms Time-To-First-Audio on CPU, preserving GPU VRAM for the LLM.
Deterministic Latency Masking    Architectural Pattern    Injecting pre-rendered PCM audio frames (e.g., "Hmm") directly into the transport layer while the LLM generates tokens.    High. Eliminates the need for the LLM to generate filler text, decoupling latency masking from GPU performance bottlenecks.
Context Truncation (Barge-in)    Pipecat Feature    Recovering from user interruptions by truncating the assistant's context history to the exact words spoken before being cut off.    High. Managed natively by Pipecat's LLMAssistantAggregator, though susceptible to specific frame-ordering bugs.
Tool-Calling Segregation    Prompt Engineering    Utilizing OpenAI-compatible tool calling to handle task execution, leaving raw text generation purely for conversational chit-chat.    High. Forces small models (like Qwen3:8B) to separate deterministic logic from natural language generation.

Evaluation of Conversational Strategies

Latency Masking and Disfluency Synthesis

Conversational naturalness relies heavily on the management of processing delays. In human-to-human dialogue, silence is rarely absolute; listeners use backchanneling, and speakers use filled pauses to hold the floor while formulating thoughts. If the voice assistant remains completely silent for two to three seconds while faster-whisper finalizes a transcript and Qwen3:8B processes the prompt, the user naturally assumes the system failed to hear them. The user will often repeat themselves, which triggers a new VAD start frame, aborts the in-flight LLM generation, and initiates a cascading pipeline collapse.

While it is tempting to prompt the LLM to generate filler words (e.g., instructing it to start every response with "Hmm..."), this approach defeats the purpose of latency masking. The delay occurs before the LLM emits its first token. If the GPU is heavily loaded, the user will still experience seconds of silence before the synthesized "Hmm" arrives. The optimal strategy in a Pipecat architecture is deterministic latency masking. This involves pre-rendering a bank of filler audio files using the selected Kokoro TTS voice (e.g., "Let me check...", "Just a second.", "Hmm...") and storing them in memory as raw PCM byte arrays. A custom Pipecat FrameProcessor can be engineered to monitor the temporal delta between the UserTurnInferenceCompletedFrame (indicating the user has stopped speaking) and the first TextFrame emitted by the LLM. If this delay exceeds a natural conversational threshold (e.g., 800ms), the processor injects the pre-rendered PCM audio directly into the Twilio transport as an AudioRawFrame. This provides immediate acoustic feedback that the assistant is working, entirely independent of the LLM's TTFT or GPU load.

Barge-In and Interruption Recovery

Because Twilio enables full-duplex telephony, the user can speak over the assistant. Pipecat handles this via the Silero VAD layer, which emits a ProposedUserStartedSpeakingFrame upon detecting speech. This proposal is resolved by external turn strategies into a definitive UserStartedSpeakingFrame, broadcasting an interruption signal that halts downstream TTS playback and flushes queued audio frames.

The primary architectural challenge of barge-in is context management. If Qwen3:8B generated a 50-word response, but the user interrupted after the tenth word, committing the entire 50-word response to the conversation history will cause severe hallucination on subsequent turns. The LLM will operate under the assumption that it successfully communicated data the user never actually heard. Pipecat's LLMAssistantAggregator is designed to mitigate this by truncating the context to the exact text spoken up to the point of interruption. However, significant race conditions have been documented in the Pipecat repository regarding this mechanism. Specifically, if the LLMFullResponseEndFrame arrives at the aggregator before the final TTSTextFrame chunks for that same response have traversed the transport layer, the aggregator may finalize and push an incomplete context to the LLM. To ensure graceful interruption recovery, the pipeline must enforce strict logical ordering, ensuring that the end-of-response signal routes through the identical queue as the text frames used for assistant context aggregation. Furthermore, to handle topic switches smoothly upon barge-in, the system prompt should instruct the LLM to acknowledge the interruption directly (e.g., "Sorry, you were saying?"), bridging the truncated thought to the new user input.

Persona Consistency and Small-Talk Segregation

Operating a 7-8B class model with reasoning disabled (reasoning_effort=none) guarantees low latency but restricts the model's capacity for nuanced self-correction and tonal consistency. Small models frequently regress to standard, verbose AI tropes, repeatedly generating phrases like "As an AI language model..." or relying heavily on bulleted lists, which sound deeply unnatural when passed through a TTS engine.

Maintaining a crisp, concise "Jarvis" persona requires aggressive negative constraint prompting. Small models respond better to rigid boundaries than to abstract personality descriptions. The system prompt must explicitly ban verbose structures, mandating 1-2 sentence conversational English and explicitly forbidding AI disclaimers. Moreover, to gracefully handle chit-chat alongside complex system tasks, the architecture should leverage OpenAI-compatible tool calling as a segregation mechanism. By mapping all task-oriented actions (e.g., checking tmux sessions, querying local logs) strictly to defined JSON tools, the raw text generation pathway is reserved entirely for conversational glue. If the user engages in small talk, the model simply generates text. If a task is requested, the model calls a tool, allowing the Python orchestration layer to format the tool's output into a highly concise, speech-optimized string before handing it back to the LLM for final synthesis.

Recommendation for Conversational Design

To construct a natural interface, avoid burdening the LLM with latency masking. Instead, implement a deterministic audio injection layer within Pipecat that streams pre-rendered Kokoro PCM files directly to the Twilio transport if the LLM's TTFT exceeds 800 milliseconds. For interruption handling, rely on Pipecat's native UserStartedSpeakingFrame to halt audio, but rigorously audit the LLMAssistantAggregator to ensure the context history accurately reflects the exact truncation point, guarding against known frame-ordering race conditions. Finally, enforce extreme brevity through negative constraints in the Qwen3:8B system prompt, and isolate all complex tmux interactions behind tool calls, ensuring the LLM's raw text generation remains strictly conversational and highly responsive.

Research Question 3: Coping with Shared GPU Contention

The most severe physical constraint within this architecture is the 12 GB VRAM limit of the single RTX 3060. Running Qwen3:8B with an optimized 4-bit quantization (e.g., Q4_K_M) requires approximately 5 to 6 GB of VRAM, inclusive of the expanding KV cache generated during a conversational session. When a secondary workload invokes a 20-30B parameter model, that larger model demands 12 to 18 GB of VRAM. It is mathematically impossible for both models to reside in the RTX 3060 simultaneously. The user's explicit requirement—that the secondary workloads must not be stopped or restricted—dictates that the eviction of Qwen3:8B from VRAM is an inevitable, accepted operating condition.

When a voice query arrives and Qwen3:8B has been evicted, the inference server must flush the 30B model from VRAM, read the 8B model's weights from the storage drive back into memory, and rebuild the context window. This disk-to-VRAM loading phase is the root cause of the reported 20 to 30-second response stalls.

Contention Management Landscape

Mechanism / Pattern    Link    Description    Effectiveness
Ollama OLLAMA_KEEP_ALIVE    Docs    An environment variable dictating how long an idle model remains loaded in memory before eviction.    Ineffective. Regardless of keep-alive settings, Ollama's scheduler will aggressively evict the 8B model to satisfy the memory requirements of an incoming 30B load to prevent OOM crashes.
Ollama OLLAMA_MAX_LOADED_MODELS    Docs    Instructs the server to hold multiple models in memory simultaneously.    Ineffective. The physical 12 GB limit prevents an 8B and a 30B model from coexisting. Forcing this will cause severe thrashing, system RAM spilling, or outright failure.
Llama.cpp --mlock    Docs    Running a dedicated llama-server instance with the --mlock flag forces the OS to pin the model in RAM/VRAM, preventing any eviction.    Violates Constraints. While it guarantees zero latency for the voice assistant, locking the VRAM ensures the secondary 30B workloads will crash or run entirely on CPU.
Stateful Residency Polling    Architectural Pattern    Querying the inference server's API to confirm model residency prior to inference, dynamically triggering fallback audio if an eviction is detected.    Optimal. Respects the physical hardware constraints and allows secondary workloads to run, while gracefully masking the 30-second cold-start latency from the user.

Evaluation of Mitigation Strategies

Because the physical VRAM boundaries dictate that eviction must occur, infrastructure-level variables provide no defense. Adjusting OLLAMA_KEEP_ALIVE to a negative value (infinity) or increasing OLLAMA_MAX_LOADED_MODELS will not force 20 GB of model weights into 12 GB of VRAM. When the 30B model is requested, the Ollama scheduler calculates the available GPU memory; upon realizing the budget is exceeded, it forcefully terminates the existing runner and evicts the 8B model.

Attempting to bypass Ollama by running a standalone llama-server process with the --mlock argument would create a hard memory reservation. While this completely eliminates cold-start latency for the voice assistant, it actively sabotages the secondary workloads, directly violating the user's architectural constraints. Similarly, deploying a smaller fallback model (e.g., a 1.5B model) during contended periods does not solve the underlying I/O physics. Loading any new model into VRAM while a 30B model is actively generating tokens will still trigger a slow eviction and context switch, causing massive latency spikes and disrupting the background task.

Therefore, the solution must exist entirely at the orchestration layer, shifting the engineering focus from preventing the stall to detecting and masking the stall.

Pipeline Fallback Detection via Residency Polling

The Ollama API exposes a /api/ps endpoint, which returns a structured list of currently loaded models, their precise VRAM footprint (size_vram), and their scheduled expiration timestamps (expires_at). This provides a lightweight mechanism to peek into the GPU's state.

Before the Pipecat pipeline forwards the UserTurnInferenceCompletedFrame to the Ollama LLM service, a custom Python function should asynchronously poll http://localhost:11434/api/ps.

⚬ Fast Path: If qwen3:8b is present in the response array, the model is currently hot in VRAM. The pipeline proceeds instantly, invoking the standard inference logic.

⚬ Slow Path: If qwen3:8b is absent, the orchestration layer definitively knows that a 20 to 30-second disk-to-VRAM cold-start is imminent.

Adaptive Latency Masking

Once the slow path is detected, the standard 800ms latency masking strategy (e.g., a brief "Hmm") is insufficient to cover a 30-second delay. Instead, the Pipecat pipeline must deploy a contextual fallback.

Upon identifying the evicted state, the pipeline should instantly push a long-form, pre-rendered Kokoro TTS audio frame into the Twilio transport. This audio should contextually explain the delay to the user (e.g., "I'm paging my context back into memory, please give me about thirty seconds to process that."). Because this audio is pre-rendered as PCM data, it requires zero GPU resources and plays immediately over the phone call. Crucially, this manages the user's expectations, preventing them from speaking again out of frustration. If the user were to repeat their query during a silent stall, the VAD would trigger a new start frame, aborting the loading sequence and initiating an infinite loop of delays. Following the audio injection, the Pipecat LLM service makes its standard request to Ollama, allowing the scheduler to handle the complex eviction of the 30B model and the reloading of Qwen3:8B organically.

Recommendation for Shared-GPU Contention

Do not attempt to solve the eviction problem by modifying Ollama environment variables or utilizing memory-locking flags in llama.cpp, as doing so defies the physical limits of the RTX 3060 and actively breaks the background LLM workloads.

Instead, implement a stateful residency polling architecture within the Pipecat orchestration layer. Build an asynchronous function that polls the Ollama /api/ps endpoint immediately after a user turn completes. If qwen3:8b is not resident in VRAM, instantly trigger an AudioRawFrame containing a pre-generated, contextual TTS message indicating a prolonged loading period. This strategy embraces the hardware constraints, utilizing intelligent audio feedback to seamlessly mask the massive disk-to-VRAM I/O delay, thereby preserving the illusion of a responsive assistant without compromising the host machine's secondary computing tasks.
