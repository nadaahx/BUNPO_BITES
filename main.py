
import os
import time
from pathlib import Path
import pykakasi
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
import json
import re
from typing import Literal
from dataclasses import dataclass

@dataclass(frozen=True)
class ModelSpec:
    name: str
    thinking_level: str | None = "low"   # None if the model doesn't support it
    max_output_tokens: int = 1024


MODEL_CHAIN = [
    ModelSpec("gemini-3.1-flash-lite", max_output_tokens=512),
    ModelSpec("gemini-3.5-flash-lite", max_output_tokens=512),
    ModelSpec("gemini-3.6-flash", max_output_tokens=768),
    ModelSpec("gemini-3.8-flash", max_output_tokens=1024),
]

COOLDOWN_SECONDS = 60
_cooldown_until: dict[str, float] = {}



BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

app = FastAPI(title="Japanese Grammar Practice")

api_key = os.getenv("GEMINI_API_KEY")
# A hard timeout stops a slow or hung model from blocking the user.
client = (
    genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=15_000),  # milliseconds
    )
    if api_key else None
)

# # MODEL = "gemini-3.8-flash"
# MODEL = "gemini-3.1-flash-lite"


class TransientError(Exception):
    """Busy or temporary problem: retry once, then fail over."""


class FailoverError(Exception):
    """Retrying this model won't help: go to the next one now."""


def is_transient(e: Exception) -> bool:
    status = getattr(e, "code", None) or getattr(e, "status_code", None)
    if status in (429, 500, 502, 503, 504):
        return True
    text = str(e)
    if any(c in text for c in (
        "RESOURCE_EXHAUSTED", "INTERNAL", "BAD_GATEWAY",
        "UNAVAILABLE", "DEADLINE_EXCEEDED",
    )):
        return True
    return "Timeout" in type(e).__name__  # httpx timeouts



app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


class GrammarRequest(BaseModel):
    grammar: str = Field(min_length=1, max_length=300)


class AnswerRequest(BaseModel):
    grammar: str = Field(min_length=1, max_length=300)
    situation: str = Field(min_length=1, max_length=3000)
    answer: str = Field(min_length=1, max_length=2000)

class Exercise(BaseModel):
    situation: str = Field(description="One English sentence describing the situation.")
    a_line: str = Field(description="One natural Japanese line spoken by person A.")


class Feedback(BaseModel):
    verdict: Literal["correct", "incorrect"]
    reason: str = Field(
        description="English. One short sentence naming the mistake in the learner's reply. "
                    "Must mention the learner's actual wording. Empty string if correct."
    )
    corrected: str = Field(
        description="ONLY one Japanese sentence that the learner could say as their reply. "
                    "No explanation, no advice, no English. Empty string if correct."
    )
    better: str = Field(
        description="ONLY one alternative Japanese sentence the learner could say as their reply. "
                    "Never advice or explanation. Empty string if none."
    )


@app.get("/")
def home():
    return FileResponse(BASE_DIR / "static" / "index.html")

def clean(text: str) -> str:
    """Remove markdown-style symbols the model might still sneak in."""
    return re.sub(r"[*#`_~>|]", "", text).strip()


class TransientError(Exception):
    """Busy or temporary problem: retry once, then fail over."""


class FailoverError(Exception):
    """Retrying this model won't help: go to the next one now."""


def is_transient(e: Exception) -> bool:
    status = getattr(e, "code", None) or getattr(e, "status_code", None)
    if status in (429, 500, 502, 503, 504):
        return True
    text = str(e)
    if any(c in text for c in (
        "RESOURCE_EXHAUSTED", "INTERNAL", "BAD_GATEWAY",
        "UNAVAILABLE", "DEADLINE_EXCEEDED",
    )):
        return True
    return "Timeout" in type(e).__name__  # httpx timeouts


def call_model(spec: ModelSpec, prompt: str, schema, temperature: float):
    config_kwargs = dict(
        temperature=temperature,
        max_output_tokens=spec.max_output_tokens,
        response_mime_type="application/json",
        response_schema=schema,
    )
    if spec.thinking_level:
        config_kwargs["thinking_config"] = types.ThinkingConfig(
            thinking_level=spec.thinking_level
        )

    try:
        response = client.models.generate_content(
            model=spec.name,
            contents=prompt,
            config=types.GenerateContentConfig(**config_kwargs),
        )
    except Exception as e:
        if is_transient(e):
            raise TransientError(str(e)) from e
        status = getattr(e, "code", None) or getattr(e, "status_code", None)
        if status == 404:  # model name unavailable: try the next one
            raise FailoverError(f"{spec.name} not found") from e
        raise  # 400/401/403 etc.: a different model won't help

    # Token limit hit (thinking tokens count toward the limit too)
    finish = ""
    if response.candidates:
        finish = str(response.candidates[0].finish_reason or "")
    if "MAX_TOKENS" in finish:
        raise FailoverError(f"{spec.name} hit the token limit")

    if not response.text:
        raise FailoverError(f"{spec.name} returned an empty response ({finish})")

    # Truncated or malformed JSON, or fields that break the schema
    try:
        return schema.model_validate(json.loads(response.text))
    except Exception as e:  # JSONDecodeError or pydantic ValidationError
        raise FailoverError(f"{spec.name} returned invalid output") from e


def ask_gemini(prompt: str, schema, temperature: float):
    if client is None:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is missing from .env")

    now = time.monotonic()
    chain = [m for m in MODEL_CHAIN if now >= _cooldown_until.get(m.name, 0)]
    if not chain:               # everything is cooling down: try all anyway
        chain = list(MODEL_CHAIN)

    last_reason = "unknown"

    for spec in chain:
        for attempt in range(2):            # at most one quick retry per model
            try:
                result = call_model(spec, prompt, schema, temperature)
                if spec is not MODEL_CHAIN[0]:
                    print(f"Fallback used: {spec.name}")
                return result

            except TransientError as e:
                last_reason = f"{spec.name}: {e}"
                if attempt == 0:
                    time.sleep(0.5)
                    continue
                _cooldown_until[spec.name] = time.monotonic() + COOLDOWN_SECONDS
                break                        # go to next model

            except FailoverError as e:
                last_reason = str(e)
                print(f"Failover: {e}")
                break                        # go to next model

            except Exception as e:
                print(f"Gemini error: {type(e).__name__}: {e}")
                raise HTTPException(
                    status_code=502,
                    detail="Gemini request failed. Check the server terminal.",
                ) from e

    print(f"All models failed. Last reason: {last_reason}")
    raise HTTPException(
        status_code=503,
        detail="Gemini is busy. Please try again shortly.",
    )


@app.post("/api/generate-situation")
def generate_situation(request: GrammarRequest):
    prompt = f"""You create short Japanese practice exercises for learners.

    Target grammar: {request.grammar}

    Task:
    Create ONE everyday situation that strongly requires the learner to use the target grammar in their reply.

    Core rules:
    - The situation must establish a specific context in which the target grammar expresses the intended meaning.
    - Person A's line must guide the learner toward that meaning without giving away the answer.
    - The target grammar must be genuinely appropriate for the intended meaning, not merely one possible way to phrase an otherwise unrelated response.
    - Design the exercise so that a reply without the target grammar would fail to express the intended meaning, sound unnatural, or miss an important nuance in context.
    - Do not create a situation where the target grammar is only loosely related to the topic.
    - Do not rely on the learner guessing what you intended. The context must make the intended meaning clear.
    - Before finalizing, mentally test at least three plausible replies. If a natural reply can answer A without using the target grammar and without losing the intended meaning, redesign the situation.
    - Never force the target grammar into a sentence where it is unnatural, illogical, or changes the intended meaning.
    - Use the target grammar according to its actual Japanese meaning and usage. Do not confuse similar meanings or grammatical functions.

    Output rules:
    - situation: exactly one English sentence, under 25 words.
    - a_line: exactly one natural Japanese sentence spoken by person A, with a question or remark that elicits the intended meaning.
    - Use common, simple vocabulary (JLPT N5 to N3 level).
    - Do not include the answer, a translation, or the target grammar in a_line.
    - Output only these two lines, using this exact format:
    situation: ...
    a_line: ...
    - Plain text only. No markdown or additional commentary.

    Example for the grammar "〜たことがある":
    situation: You are asking a coworker whether they have ever visited Japan.
    a_line: 日本に行ったことがありますか？

    Example for the grammar "〜っぽい":
    situation: Your friend sees a child acting impatiently and asks what you think about the child's behavior.
    a_line: あの子、どう思いますか？

    Important:
    For grammar such as "〜っぽい", the situation must elicit its actual function, such as describing something as having a certain quality or tendency, or expressing an impression of resemblance. Do not use a context about where something was purchased if the intended answer is supposed to describe its appearance.

    Quality check:
    1. Identify the exact meaning of the target grammar being practiced.
    2. Create a situation and A's line that elicit that meaning.
    3. Verify that the expected reply is logical and natural in Japanese.
    4. Reject and regenerate any exercise where the target grammar is merely optional, the intended meaning is unclear, or the example requires an unnatural sentence."""
    data = ask_gemini(prompt, Exercise, temperature=0.9) 

    text = (
        f"Situation: {clean(data.situation)}\n"
        f"A: {clean(data.a_line)}\n"
        f"B (your reply): "
    )
    return {"situation": text}

@app.post("/api/check-answer")
def check_answer(request: AnswerRequest):
    prompt = f"""You are a strict but fair Japanese teacher. Check the learner's reply.

Target grammar: {request.grammar}
Situation: {request.situation}
Learner's reply: {request.answer}

Decide in this order:
1. Is the reply grammatically correct, natural, fitting the situation, and does it use the target grammar properly?
   - If yes: verdict = "correct", corrected = "", reason = "".
   - If no: verdict = "incorrect".
2. If incorrect: corrected = the fixed Japanese sentence (change as little as possible). reason = one short English sentence naming the single main problem.
3. better = a more natural or polite Japanese alternative ONLY if one clearly exists. Otherwise "".
CRITICAL CONTEXT AND GRAMMAR VALIDATION RULES:

- Before grading, identify the exact meaning expressed by the learner's answer in the given context.
- Check whether the learner's answer actually responds to the situation and A's line. Do not judge grammar usage in isolation.
- Verify that the target grammar can naturally express the intended meaning in this specific context. Different grammar patterns have different meanings and usage restrictions.
- Never create a corrected sentence that is unrelated to the learner's intended meaning or the original situation just to include the target grammar.
- Never assume that replacing the learner's grammar pattern with the target grammar will preserve the original meaning. Verify the meaning of the resulting sentence.
- Check that the situation, A's line, target grammar, learner's answer, and corrected answer are semantically consistent with one another.
- If the learner's answer is unrelated to the situation, explain the meaning mismatch accurately. Do not invent a target-grammar correction that changes the intended meaning.
- If the learner's answer expresses the intended meaning naturally but uses a different grammar pattern, distinguish correct Japanese from successful completion of the target-grammar exercise.
- If the target grammar cannot naturally express the meaning required by the situation, flag the exercise as poorly designed rather than forcing an unnatural sentence.
- If the target grammar has multiple meanings or uses, evaluate only the meaning that fits the context. Do not confuse it with another use of the same grammar.
- Before returning a correction, verify that a native Japanese speaker would naturally use that sentence in the given situation.
- The corrected answer must be relevant, natural Japanese, preserve the intended meaning, and use the target grammar only when it genuinely fits.
- The better answer must also be natural, relevant, and meaning-preserving. It may improve conversational style but must not introduce a different meaning.
- Never treat the target grammar as mandatory at the expense of correct Japanese or logical meaning.
- If the exercise itself is flawed, do not penalize the learner for the flaw. Explain the problem in reason.

GRADING PRIORITIES:
1. Contextual relevance
2. Meaning accuracy
3. Grammatical correctness
4. Natural Japanese usage
5. Correct use of the assigned target grammar

A response must not be described as grammatically incorrect when the actual issue is only that it does not use the assigned target grammar.

OUTPUT LANGUAGE RULES:
- Japanese only in corrected and better.
- English only in reason.
- Plain text only, no markdown.
"""

    data = ask_gemini(prompt, Feedback, temperature=0.2) 

    lines = []
    if data.verdict == "correct":
        lines.append("Result: Correct")
    else:
        lines.append("Result: Not correct")
        if data.corrected.strip():
            lines.append(f"Correct version: {clean(data.corrected)}")
        if data.reason.strip():
            lines.append(f"Reason: {clean(data.reason)}")

    if data.better.strip() and clean(data.better) != clean(request.answer):
        lines.append(f"Better way: {clean(data.better)}")

    return {"feedback": "\n".join(lines)}

kakasi = pykakasi.kakasi()


class FuriganaRequest(BaseModel):
    text: str = Field(min_length=1, max_length=3000)


@app.post("/api/furigana")
def get_furigana(request: FuriganaRequest):
    segments = kakasi.convert(request.text)

    return {
        "segments": [
            {
                "text": segment["orig"],
                "reading": segment["hira"],
            }
            for segment in segments
        ]
    }
