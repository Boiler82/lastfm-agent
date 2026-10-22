"""
Evaluate the Last.fm Music Agent.

Asks the agent every question in eval_questions.py, compares its answer with the
correct answer (calculated live by your own SQL), and prints a score.

Run all questions:        python evaluate.py
Run only question 3:      python evaluate.py 3
Use another model:        AGENT_MODEL=claude-haiku-4-5-20251001 python evaluate.py
"""

import os
import re
import sys
import time
import unicodedata
from datetime import datetime

import pandas as pd

from agent import ask, query_to_df, MODEL
from eval_questions import QUESTIONS

# Added to every question, so the agent ends with an answer we can check automatically
EVAL_INSTRUCTION = (
    "\n\n(Evaluation mode: do not make charts. End your reply with one final line "
    "in the form 'FINAL: <answer>', where <answer> is only the number or the short "
    "name - no units, no extra words.)"
)


# ---------- 1. The correct answer, from your SQL ----------
def get_truth(sql: str):
    return query_to_df(sql).iloc[0, 0]


# ---------- 2. Read the agent's answer from its 'FINAL:' line ----------
def extract_final(reply: str) -> str | None:
    matches = re.findall(r"FINAL:\s*(.+)", reply)
    if not matches:
        return None
    return matches[-1].strip().strip("*`'\". ")


def to_number(text: str) -> float | None:
    # "1,200 rows" -> 1200  and  "24.0%" -> 24.0
    match = re.search(r"-?\d[\d,]*\.?\d*", text)
    if not match:
        return None
    return float(match.group().replace(",", ""))


def normalize(text: str) -> str:
    # "ADÉLA" -> "adela", "Ain't In LA" -> "aintinla": accents, case and punctuation don't matter
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", text.lower())


# ---------- 3. Compare the two ----------
def grade(q: dict, final: str | None, truth) -> bool:
    if final is None or truth is None:
        return False
    if q["type"] == "number":
        value = to_number(final)
        return value is not None and abs(value - float(truth)) <= q["tolerance"]
    # Text: the correct name must appear in the answer, e.g. "NICOLE KIDMAN by ADÉLA" contains "NICOLE KIDMAN"
    return normalize(truth) in normalize(final)


# ---------- 4. Run the test ----------
def main():
    selected = list(enumerate(QUESTIONS, start=1))
    if len(sys.argv) > 1:
        number = int(sys.argv[1])
        selected = [(number, QUESTIONS[number - 1])]

    rows = []
    for i, q in selected:
        print(f"\n[{i}/{len(QUESTIONS)}] ({q['level']}) {q['question']}")
        truth = get_truth(q["truth_sql"])

        start = time.time()
        try:
            reply = ask(q["question"] + EVAL_INSTRUCTION)   # a fresh conversation for every question
        except Exception as e:
            reply = f"ERROR: {e}"
        seconds = round(time.time() - start, 1)

        final = extract_final(reply)
        correct = grade(q, final, truth)
        print(f"   Correct answer: {truth}")
        print(f"   Agent answered: {final}")
        print(f"   {'✅ CORRECT' if correct else '❌ WRONG'}  ({seconds}s)")

        rows.append({
            "nr": i, "level": q["level"], "question": q["question"],
            "correct_answer": truth, "agent_answer": final,
            "correct": correct, "seconds": seconds, "full_reply": reply,
        })

    results = pd.DataFrame(rows)
    score = results["correct"].sum()
    total = len(results)
    print("\n" + "=" * 50)
    print(f"SCORE: {score}/{total} correct ({score / total:.0%})   model: {MODEL}")
    print(results.groupby("level", sort=False)["correct"].agg(["sum", "count"])
                 .rename(columns={"sum": "correct", "count": "questions"}))

    os.makedirs("results", exist_ok=True)
    filename = f"results/eval_results_{datetime.now():%Y-%m-%d_%H%M}.csv"
    results.to_csv(filename, index=False)
    print(f"\nFull results saved to {filename}")


if __name__ == "__main__":
    main()
