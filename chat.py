#!/usr/bin/env python3
"""
Ask plain-language questions about a chess game analysis.

Usage:
  python chat.py game_analysis.json

Requires:
  ANTHROPIC_API_KEY environment variable set to your Anthropic API key.
  Get one at: https://console.anthropic.com/

Example questions:
  Why did my pawn move to e6 drop the evaluation by 6 points?
  What was the turning point of the game?
  What should Black have played instead of Nxe4?
  Summarize the key mistakes in this game.
"""

import json
import os
import sys
from pathlib import Path
import anthropic


def format_game_context(analysis: dict) -> str:
    """Build a readable text representation of the analysis for the LLM."""
    meta = analysis["metadata"]

    lines = [
        "CHESS GAME ANALYSIS",
        "=" * 50,
        f"White:   {meta.get('White', 'Unknown')}",
        f"Black:   {meta.get('Black', 'Unknown')}",
        f"Event:   {meta.get('Event', 'Unknown')}",
        f"Date:    {meta.get('Date', 'Unknown')}",
        f"Result:  {meta.get('Result', 'Unknown')}",
    ]
    if meta.get("Opening"):
        lines.append(f"Opening: {meta['Opening']}")
    elif meta.get("ECO"):
        lines.append(f"ECO:     {meta['ECO']}")

    lines += [
        "",
        f"Starting position evaluation: {analysis['initial_eval_label']}",
        "",
        "MOVE-BY-MOVE ANALYSIS",
        "-" * 50,
    ]

    for move in analysis["moves"]:
        num = move["move_number"]
        side = move["side"]
        san = move["san"]
        delta = move["eval_delta_cp"]

        if side == "white":
            label = f"Move {num}. {san} (White)"
        else:
            label = f"Move {num}... {san} (Black)"

        if move["is_critical"]:
            label += f"  *** CRITICAL (Δ{delta:+d}cp) ***"

        lines.append(label)
        lines.append(f"  FEN after move: {move['fen_after']}")
        lines.append(f"  Evaluation:     {move['eval_label']} ({move['eval_cp']:+d}cp)")
        lines.append(f"  Change vs prev: {delta:+d}cp")

        if move.get("best_continuation"):
            lines.append(f"  Engine's best continuation from here: {' '.join(move['best_continuation'])}")

        if move.get("alternative_lines"):
            lines.append("  Engine's alternative lines at this critical position:")
            for i, alt in enumerate(move["alternative_lines"], 1):
                lines.append(
                    f"    Line {i} ({alt['eval_label']}, {alt['eval_cp']:+d}cp): "
                    f"{' '.join(alt['continuation'])}"
                )

        lines.append("")

    return "\n".join(lines)


def build_system_prompt(analysis: dict) -> str:
    meta = analysis["metadata"]
    white = meta.get("White", "White")
    black = meta.get("Black", "Black")
    context = format_game_context(analysis)

    return f"""You are a chess coach helping a player understand their game. You have been given a complete engine analysis: every move, board state (as FEN), Stockfish evaluation in centipawns, and engine-suggested continuations. Moves marked CRITICAL had an evaluation swing of 0.5+ pawns.

Centipawn convention: positive = better for White ({white}), negative = better for Black ({black}). 100cp = roughly one pawn of advantage.

Your job is to give clear, natural-language explanations — not just repeat numbers. When a move is bad, explain *why* it's bad: what tactical threat it allows, what positional weakness it creates, what the refutation looks like. Use the engine's continuation lines as evidence. Reference specific moves and positions by their notation. Keep explanations grounded in the data provided.

{context}"""


def main():
    if len(sys.argv) < 2:
        print("Usage: python chat.py <analysis.json>")
        print("Run analyze.py first to generate the analysis file.")
        sys.exit(1)

    json_path = Path(sys.argv[1])
    if not json_path.exists():
        print(f"Error: File not found: {json_path}", file=sys.stderr)
        sys.exit(1)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY is not set.", file=sys.stderr)
        print("Set it with:", file=sys.stderr)
        print("  export ANTHROPIC_API_KEY=your_key_here", file=sys.stderr)
        print("Get a key at: https://console.anthropic.com/", file=sys.stderr)
        sys.exit(1)

    analysis = json.loads(json_path.read_text())
    client = anthropic.Anthropic(api_key=api_key)
    system_prompt = build_system_prompt(analysis)

    meta = analysis["metadata"]
    total_moves = len(analysis["moves"])
    critical_count = sum(1 for m in analysis["moves"] if m["is_critical"])

    print(f"\n{'=' * 50}")
    print(f"  {meta.get('White', '?')} vs {meta.get('Black', '?')}")
    print(f"  {meta.get('Event', '?')} — {meta.get('Result', '?')}")
    print(f"  {total_moves} moves analyzed, {critical_count} critical positions")
    print(f"{'=' * 50}")
    print("\nAsk anything about this game. Type 'quit' to exit.\n")

    conversation = []

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            print("Goodbye!")
            break

        conversation.append({"role": "user", "content": user_input})

        try:
            print("\nClaude: ", end="", flush=True)
            full_reply = ""
            with client.messages.stream(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                system=system_prompt,
                messages=conversation,
            ) as stream:
                for text in stream.text_stream:
                    print(text, end="", flush=True)
                    full_reply += text
            print("\n")
        except anthropic.APIError as e:
            print(f"\nAPI error: {e}", file=sys.stderr)
            conversation.pop()
            continue

        conversation.append({"role": "assistant", "content": full_reply})


if __name__ == "__main__":
    main()
