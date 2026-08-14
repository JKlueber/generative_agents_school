"""
calculate_bfi2.py
==================
Calculates BFI-2 Big Five personality scores from agent survey JSON files.

USAGE:
  python calculate_bfi2.py <json_file>
  python calculate_bfi2.py Mohammed_Idrissi_20260813_210349.json

  Or to process all JSON files in a folder:
  python calculate_bfi2.py --all <folder_path>

OUTPUT:
  Prints Big Five domain scores and facet scores for each agent.
  Optionally saves results to a CSV file.

BFI-2 SCORING KEY (Soto & John, 2017):
  60 items, rated 1-5.
  Items marked with (R) are reverse-scored: score = 6 - rating

  EXTRAVERSION (E): items 1,6,11,16,21,26,31,36,41,46,51,56
    Sociability:    1, 16(R), 31, 46
    Assertiveness:  6, 21, 36(R), 51
    Energy Level:  11(R), 26, 41, 56

  AGREEABLENESS (A): items 2,7,12,17,22,27,32,37,42,47,52,57
    Compassion:     2, 17(R), 32, 47(R)
    Respectfulness: 7, 22(R), 37(R), 52
    Trust:         12(R), 27, 42(R), 57

  CONSCIENTIOUSNESS (C): items 3,8,13,18,23,28,33,38,43,48,53,58
    Organization:   3(R), 18, 33, 48(R)
    Productiveness: 8(R), 23(R), 38, 53
    Responsibility:13, 28(R), 43, 58(R)

  NEGATIVE EMOTIONALITY (N): items 4,9,14,19,24,29,34,39,44,49,54,59
    Anxiety:        4(R), 19, 34, 49(R)
    Depression:     9(R), 24, 39, 54
    Emotional Volatility: 14, 29(R), 44(R), 59

  OPEN-MINDEDNESS (O): items 5,10,15,20,25,30,35,40,45,50,55,60
    Aesthetic Sensitivity: 5(R), 20, 35, 50(R)
    Intellectual Curiosity:10, 25(R), 40, 55(R)
    Creative Imagination:  15, 30(R), 45(R), 60
"""

import json
import sys
import os
import csv
from pathlib import Path

# ── BFI-2 Scoring Key ─────────────────────────────────────────────────────────
# Format: {item_number: (domain, facet, reverse_scored)}
SCORING_KEY = {
    # EXTRAVERSION
    1:  ("Extraversion", "Sociability", False),
    6:  ("Extraversion", "Assertiveness", False),
    11: ("Extraversion", "Energy Level", True),
    16: ("Extraversion", "Sociability", True),
    21: ("Extraversion", "Assertiveness", False),
    26: ("Extraversion", "Energy Level", False),
    31: ("Extraversion", "Sociability", False),
    36: ("Extraversion", "Assertiveness", True),
    41: ("Extraversion", "Energy Level", False),
    46: ("Extraversion", "Sociability", False),
    51: ("Extraversion", "Assertiveness", False),
    56: ("Extraversion", "Energy Level", False),

    # AGREEABLENESS
    2:  ("Agreeableness", "Compassion", False),
    7:  ("Agreeableness", "Respectfulness", False),
    12: ("Agreeableness", "Trust", True),
    17: ("Agreeableness", "Compassion", True),
    22: ("Agreeableness", "Respectfulness", True),
    27: ("Agreeableness", "Trust", False),
    32: ("Agreeableness", "Compassion", False),
    37: ("Agreeableness", "Respectfulness", True),
    42: ("Agreeableness", "Trust", True),
    47: ("Agreeableness", "Compassion", True),
    52: ("Agreeableness", "Respectfulness", False),
    57: ("Agreeableness", "Trust", False),

    # CONSCIENTIOUSNESS
    3:  ("Conscientiousness", "Organization", True),
    8:  ("Conscientiousness", "Productiveness", True),
    13: ("Conscientiousness", "Responsibility", False),
    18: ("Conscientiousness", "Organization", False),
    23: ("Conscientiousness", "Productiveness", True),
    28: ("Conscientiousness", "Responsibility", True),
    33: ("Conscientiousness", "Organization", False),
    38: ("Conscientiousness", "Productiveness", False),
    43: ("Conscientiousness", "Responsibility", False),
    48: ("Conscientiousness", "Organization", True),
    53: ("Conscientiousness", "Productiveness", False),
    58: ("Conscientiousness", "Responsibility", True),

    # NEGATIVE EMOTIONALITY
    4:  ("Negative Emotionality", "Anxiety", True),
    9:  ("Negative Emotionality", "Depression", True),
    14: ("Negative Emotionality", "Emotional Volatility", False),
    19: ("Negative Emotionality", "Anxiety", False),
    24: ("Negative Emotionality", "Depression", False),
    29: ("Negative Emotionality", "Emotional Volatility", True),
    34: ("Negative Emotionality", "Anxiety", False),
    39: ("Negative Emotionality", "Depression", False),
    44: ("Negative Emotionality", "Emotional Volatility", True),
    49: ("Negative Emotionality", "Anxiety", True),
    54: ("Negative Emotionality", "Depression", False),
    59: ("Negative Emotionality", "Emotional Volatility", False),

    # OPEN-MINDEDNESS
    5:  ("Open-Mindedness", "Aesthetic Sensitivity", True),
    10: ("Open-Mindedness", "Intellectual Curiosity", False),
    15: ("Open-Mindedness", "Creative Imagination", False),
    20: ("Open-Mindedness", "Aesthetic Sensitivity", False),
    25: ("Open-Mindedness", "Intellectual Curiosity", True),
    30: ("Open-Mindedness", "Creative Imagination", True),
    35: ("Open-Mindedness", "Aesthetic Sensitivity", False),
    40: ("Open-Mindedness", "Intellectual Curiosity", False),
    45: ("Open-Mindedness", "Creative Imagination", True),
    50: ("Open-Mindedness", "Aesthetic Sensitivity", True),
    55: ("Open-Mindedness", "Intellectual Curiosity", True),
    60: ("Open-Mindedness", "Creative Imagination", False),
}

DOMAINS = ["Extraversion", "Agreeableness", "Conscientiousness",
           "Negative Emotionality", "Open-Mindedness"]

FACETS = {
    "Extraversion": ["Sociability", "Assertiveness", "Energy Level"],
    "Agreeableness": ["Compassion", "Respectfulness", "Trust"],
    "Conscientiousness": ["Organization", "Productiveness", "Responsibility"],
    "Negative Emotionality": ["Anxiety", "Depression", "Emotional Volatility"],
    "Open-Mindedness": ["Aesthetic Sensitivity", "Intellectual Curiosity", "Creative Imagination"],
}


def score_response(rating, reverse):
    """Apply reverse scoring if needed."""
    return (6 - rating) if reverse else rating


def calculate_scores(answers):
    """
    Calculate BFI-2 domain and facet scores from a list of 60 answers.
    Returns dict with domain and facet scores (mean of 4 items each).
    """
    if len(answers) != 60:
        raise ValueError(f"Expected 60 answers, got {len(answers)}")

    # Collect scored values per domain and facet
    domain_scores = {d: [] for d in DOMAINS}
    facet_scores = {d: {f: [] for f in FACETS[d]} for d in DOMAINS}

    for i, answer in enumerate(answers):
        item_num = i + 1
        rating = answer["rating"]

        if item_num not in SCORING_KEY:
            continue

        domain, facet, reverse = SCORING_KEY[item_num]
        scored = score_response(rating, reverse)

        domain_scores[domain].append(scored)
        facet_scores[domain][facet].append(scored)

    # Calculate means
    results = {}
    results["domains"] = {
        d: round(sum(v) / len(v), 3) for d, v in domain_scores.items()
    }
    results["facets"] = {}
    for d in DOMAINS:
        results["facets"][d] = {
            f: round(sum(v) / len(v), 3)
            for f, v in facet_scores[d].items()
        }

    return results


def print_results(persona_name, sim_time, party, results):
    """Pretty print BFI-2 results."""
    print(f"\n{'='*60}")
    print(f"  BFI-2 Results: {persona_name}")
    if party:
        print(f"  Party Environment: {party}")
    if sim_time:
        print(f"  Sim Time: {sim_time}")
    print(f"{'='*60}")

    print("\n  BIG FIVE DOMAIN SCORES (1-5 scale):")
    print(f"  {'Domain':<28} {'Score':>6}")
    print(f"  {'-'*36}")
    for domain, score in results["domains"].items():
        bar = "█" * int(score * 4)
        print(f"  {domain:<28} {score:>6.2f}  {bar}")

    print("\n  FACET SCORES:")
    for domain in DOMAINS:
        print(f"\n  {domain}:")
        for facet, score in results["facets"][domain].items():
            bar = "█" * int(score * 4)
            print(f"    {facet:<26} {score:>5.2f}  {bar}")

    print()


def process_file(filepath):
    """Process a single JSON file and return results dict."""
    with open(filepath) as f:
        data = json.load(f)

    persona_name = data.get("persona_name", "Unknown")
    sim_time = data.get("sim_time", "")
    party = data.get("party", "")
    answers = data.get("answers", [])

    results = calculate_scores(answers)
    print_results(persona_name, sim_time, party, results)

    return {
        "persona_name": persona_name,
        "sim_time": sim_time,
        "party": party,
        **{f"domain_{d.replace(' ', '_')}": s for d, s in results["domains"].items()},
        **{f"facet_{d.replace(' ', '_')}_{f.replace(' ', '_')}": s
           for d in DOMAINS for f, s in results["facets"][d].items()}
    }


def save_csv(rows, output_path):
    """Save all results to a CSV file."""
    if not rows:
        return
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"[SAVED] Results written to: {output_path}")


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python calculate_bfi2.py <json_file>")
        print("  python calculate_bfi2.py --all <folder_path>")
        sys.exit(1)

    rows = []

    if sys.argv[1] == "--all":
        folder = sys.argv[2] if len(sys.argv) > 2 else "."
        files = list(Path(folder).glob("*.json"))
        if not files:
            print(f"No JSON files found in: {folder}")
            sys.exit(1)
        print(f"Processing {len(files)} files...")
        for f in sorted(files):
            try:
                row = process_file(f)
                rows.append(row)
            except Exception as e:
                print(f"[ERROR] {f.name}: {e}")
        save_csv(rows, Path(folder) / "bfi2_results.csv")

    else:
        filepath = sys.argv[1]
        if not os.path.exists(filepath):
            print(f"File not found: {filepath}")
            sys.exit(1)
        row = process_file(filepath)
        rows.append(row)

        # Ask if user wants to save CSV
        save_csv(rows, Path(filepath).stem + "_bfi2_scores.csv")


if __name__ == "__main__":
    main()
