import random
import time
from datetime import datetime

import anthropic
import config
import database as db

_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

_WORD_LISTS = [
    ["apple", "river", "truck", "mountain", "blanket"],
    ["coffee", "engine", "sunset", "hammer", "carpet"],
    ["pencil", "window", "forest", "pillow", "orange"],
    ["bottle", "farmer", "thunder", "saddle", "mirror"],
]

_MATH_QUESTIONS = [
    ("What is 8 plus 6?", 14),
    ("What is 15 minus 7?", 8),
    ("What is 4 times 3?", 12),
    ("What is 9 plus 5?", 14),
    ("What is 18 minus 9?", 9),
    ("What is 6 times 4?", 24),
    ("What is 13 plus 8?", 21),
    ("What is 20 minus 6?", 14),
    ("What is 7 times 3?", 21),
    ("What is 17 minus 8?", 9),
]

_WORD_TO_NUM = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "twenty one": 21,
    "twenty-one": 21, "twenty four": 24, "twenty-four": 24,
}


def _parse_number(text: str) -> int | None:
    t = text.lower().strip()
    if t in _WORD_TO_NUM:
        return _WORD_TO_NUM[t]
    try:
        return int(t)
    except ValueError:
        return None


class DrowsyTest:
    def __init__(self, voice_engine):
        self._voice = voice_engine

    def run(self) -> dict:
        """Run all three alertness tests and return an assessment dict."""
        self._voice.speak(
            "Starting alertness test. Three quick checks: memory, math, and reaction. "
            "Please respond quickly and accurately."
        )
        time.sleep(0.5)

        scores = {
            "memory": self._test_memory(),
            "math": self._test_math(),
            "reaction": self._test_reaction(),
        }

        assessment = self._assess(scores)
        self._voice.speak(assessment["message"])
        self._save(scores, assessment)
        return assessment

    # ── Test 1: Word Recall ──────────────────────────────────────────────────

    def _test_memory(self) -> dict:
        words = random.choice(_WORD_LISTS)
        self._voice.speak("Memory test. Listen to these five words.")
        time.sleep(0.3)
        self._voice.speak(". ".join(words))
        time.sleep(3)
        self._voice.speak("Now repeat all five words.")

        start = time.time()
        response = self._voice.listen(timeout=15, phrase_limit=12)
        elapsed = time.time() - start

        if not response:
            return {"score": 0.0, "recalled": 0, "total": 5, "response_time": elapsed}

        recalled = sum(1 for w in words if w in response.lower())
        return {
            "score": recalled / 5.0,
            "recalled": recalled,
            "total": 5,
            "response_time": round(elapsed, 2),
        }

    # ── Test 2: Mental Math ──────────────────────────────────────────────────

    def _test_math(self) -> dict:
        questions = random.sample(_MATH_QUESTIONS, 3)
        self._voice.speak("Math test. Answer each question out loud as fast as you can.")
        time.sleep(0.5)

        correct = 0
        total_time = 0.0

        for question, answer in questions:
            self._voice.speak(question)
            start = time.time()
            response = self._voice.listen(timeout=10, phrase_limit=5)
            elapsed = time.time() - start
            total_time += elapsed

            if response and _parse_number(response) == answer:
                correct += 1

        avg_time = total_time / 3
        accuracy = correct / 3.0
        # Penalty for slow responses: full credit up to 3s, zero credit at 13s
        time_factor = max(0.0, 1.0 - max(0.0, avg_time - 3.0) / 10.0)
        return {
            "score": round(accuracy * time_factor, 3),
            "accuracy": round(accuracy, 3),
            "correct": correct,
            "avg_time": round(avg_time, 2),
        }

    # ── Test 3: Reaction Time ────────────────────────────────────────────────

    def _test_reaction(self) -> dict:
        self._voice.speak(
            "Reaction test. Say anything the instant you hear me say 'Now'."
        )
        time.sleep(1)

        times = []
        for _ in range(3):
            delay = random.uniform(2.0, 4.5)
            self._voice.speak("Ready.")
            time.sleep(delay)
            self._voice.speak("Now!")
            start = time.time()
            response = self._voice.listen(timeout=5, phrase_limit=3)
            elapsed = time.time() - start
            # 6.0 s penalty if no response detected
            times.append(elapsed if response else 6.0)
            time.sleep(0.8)

        avg_time = sum(times) / len(times)
        # Score: 1.0 at ≤1.0 s, 0.0 at ≥4.0 s
        score = max(0.0, 1.0 - max(0.0, avg_time - 1.0) / 3.0)
        return {
            "score": round(score, 3),
            "avg_time": round(avg_time, 2),
            "times": [round(t, 2) for t in times],
        }

    # ── Assessment ───────────────────────────────────────────────────────────

    def _assess(self, scores: dict) -> dict:
        overall = (
            scores["memory"]["score"]
            + scores["math"]["score"]
            + scores["reaction"]["score"]
        ) / 3.0

        prompt = (
            f"A commercial truck driver just completed a roadside alertness test.\n"
            f"Results:\n"
            f"- Memory: recalled {scores['memory']['recalled']} of 5 words\n"
            f"- Math: {scores['math']['correct']}/3 correct, avg {scores['math']['avg_time']:.1f}s per question\n"
            f"- Reaction: avg {scores['reaction']['avg_time']:.1f}s response time\n"
            f"- Overall score: {overall:.0%}\n\n"
            f"Give a 1-2 sentence safety recommendation. "
            f"Be direct: should they continue driving, take a break, or stop immediately?"
        )

        response = _client.messages.create(
            model=config.MODEL,
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        message = next(
            (b.text for b in response.content if b.type == "text"), ""
        )

        if overall >= 0.75:
            level = "alert"
        elif overall >= 0.50:
            level = "warning"
        else:
            level = "danger"

        return {
            "level": level,
            "overall_score": round(overall, 3),
            "message": message,
            "scores": scores,
        }

    # ── Logging ──────────────────────────────────────────────────────────────

    def _save(self, scores: dict, assessment: dict):
        db.save_alertness_log(
            timestamp=datetime.now().isoformat(),
            level=assessment["level"],
            overall_score=assessment["overall_score"],
            memory_recalled=scores["memory"]["recalled"],
            math_correct=scores["math"]["correct"],
            math_avg_time=scores["math"]["avg_time"],
            reaction_avg_time=scores["reaction"]["avg_time"],
        )
