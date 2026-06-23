#!/usr/bin/env python3
"""
Analyze a chess game PGN with Stockfish and save structured JSON.

Usage:
  python analyze.py                  # paste PGN, press Ctrl+D when done
  python analyze.py game.pgn
  python analyze.py game.pgn --depth 20
  python analyze.py game.pgn --stockfish /usr/local/bin/stockfish
  python analyze.py game.pgn --output my_analysis.json

Requires Stockfish installed: https://stockfishchess.org/download/
  Mac:   brew install stockfish
  Linux: sudo apt install stockfish  (or download from site)
"""

import argparse
import chess
import chess.pgn
import chess.engine
import json
import sys
import io
from pathlib import Path

CRITICAL_THRESHOLD_CP = 50   # eval swing that marks a move as critical
DEFAULT_DEPTH = 18
DEFAULT_PV_LENGTH = 8        # how many moves deep to record best lines
MULTIPV_AT_CRITICAL = 3      # extra lines to store at critical positions


def get_white_cp(score: chess.engine.PovScore) -> int:
    """Centipawn score from White's perspective. Forced mate maps to ±10000."""
    return score.white().score(mate_score=10000)


def cp_to_label(cp: int) -> str:
    """Human-readable description of a centipawn score."""
    if abs(cp) >= 9000:
        return f"Forced mate for {'White' if cp > 0 else 'Black'}"
    p = cp / 100
    if abs(p) < 0.2:
        return f"{p:+.2f} (roughly equal)"
    elif abs(p) < 0.5:
        return f"{p:+.2f} ({'White' if p > 0 else 'Black'} slightly better)"
    elif abs(p) < 1.5:
        return f"{p:+.2f} ({'White' if p > 0 else 'Black'} clearly better)"
    elif abs(p) < 3.0:
        return f"{p:+.2f} ({'White' if p > 0 else 'Black'} much better)"
    else:
        return f"{p:+.2f} ({'White' if p > 0 else 'Black'} winning)"


def pv_to_san(board: chess.Board, pv: list, max_moves: int = DEFAULT_PV_LENGTH) -> list:
    """Convert a list of Move objects to SAN notation starting from board."""
    san_list = []
    temp = board.copy()
    for move in pv[:max_moves]:
        try:
            san_list.append(temp.san(move))
            temp.push(move)
        except Exception:
            break
    return san_list


def analyze_game(pgn_text: str, stockfish_path: str = "stockfish", depth: int = DEFAULT_DEPTH) -> dict:
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        raise ValueError("Could not parse PGN — check the format and try again.")

    metadata = dict(game.headers)
    board = game.board()
    moves = []

    with chess.engine.SimpleEngine.popen_uci(stockfish_path) as engine:
        # Evaluate the starting position
        init_info = engine.analyse(board, chess.engine.Limit(depth=depth))
        initial_cp = get_white_cp(init_info["score"])
        prev_cp = initial_cp

        print(f"Starting position: {cp_to_label(initial_cp)}")
        print(f"Analyzing {sum(1 for _ in game.mainline_moves())} moves at depth {depth}...\n")

        half_move = 0
        for node in game.mainline():
            move = node.move
            parent_board = board.copy()
            san = parent_board.san(move)
            side = "white" if parent_board.turn == chess.WHITE else "black"
            move_num = parent_board.fullmove_number

            board.push(move)
            half_move += 1

            # Evaluate position after the move
            info = engine.analyse(board, chess.engine.Limit(depth=depth))
            cur_cp = get_white_cp(info["score"])
            delta_cp = cur_cp - prev_cp
            is_critical = abs(delta_cp) >= CRITICAL_THRESHOLD_CP

            best_continuation = pv_to_san(board, info.get("pv", []))

            move_data = {
                "half_move": half_move,
                "move_number": move_num,
                "side": side,
                "san": san,
                "fen_after": board.fen(),
                "eval_cp": cur_cp,
                "eval_label": cp_to_label(cur_cp),
                "eval_delta_cp": delta_cp,
                "is_critical": is_critical,
                "best_continuation": best_continuation,
            }

            # At critical positions, run MultiPV for alternative lines
            if is_critical:
                multi_info = engine.analyse(
                    board,
                    chess.engine.Limit(depth=depth),
                    multipv=MULTIPV_AT_CRITICAL,
                )
                alt_lines = []
                for alt in multi_info[1:]:
                    alt_pv = pv_to_san(board, alt.get("pv", []))
                    if alt_pv:
                        alt_lines.append({
                            "eval_cp": get_white_cp(alt["score"]),
                            "eval_label": cp_to_label(get_white_cp(alt["score"])),
                            "continuation": alt_pv,
                        })
                move_data["alternative_lines"] = alt_lines

            moves.append(move_data)
            prev_cp = cur_cp

            # Progress line
            label = f"{move_num}{'.' if side == 'white' else '...'} {san}"
            flag = " *** CRITICAL ***" if is_critical else ""
            print(f"  {label:<20}  {cp_to_label(cur_cp):<38}  Δ{delta_cp:+d}cp{flag}")

    critical_count = sum(1 for m in moves if m["is_critical"])
    print(f"\nDone. {len(moves)} moves, {critical_count} critical positions.")

    return {
        "metadata": metadata,
        "initial_eval_cp": initial_cp,
        "initial_eval_label": cp_to_label(initial_cp),
        "moves": moves,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Analyze a chess PGN with Stockfish and save structured JSON.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "pgn",
        nargs="?",
        help="Path to PGN file. Omit to paste PGN directly (end with Ctrl+D).",
    )
    parser.add_argument(
        "--stockfish",
        default="stockfish",
        help="Path to Stockfish binary (default: 'stockfish', must be on PATH)",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=DEFAULT_DEPTH,
        help=f"Engine search depth (default: {DEFAULT_DEPTH}; higher = slower but stronger)",
    )
    parser.add_argument(
        "--output",
        default="game_analysis.json",
        help="Output JSON path (default: game_analysis.json, or <pgn_name>_analysis.json if file given)",
    )
    args = parser.parse_args()

    if args.pgn:
        pgn_path = Path(args.pgn)
        if not pgn_path.exists():
            print(f"Error: File not found: {pgn_path}", file=sys.stderr)
            sys.exit(1)
        pgn_text = pgn_path.read_text()
        output_path = Path(args.output) if args.output != "game_analysis.json" else pgn_path.with_name(pgn_path.stem + "_analysis.json")
    else:
        print("Paste your PGN below, then press Ctrl+D when done:\n")
        pgn_text = sys.stdin.read().strip()
        if not pgn_text:
            print("Error: No PGN provided.", file=sys.stderr)
            sys.exit(1)
        output_path = Path(args.output)
        print()

    try:
        result = analyze_game(
            pgn_text,
            stockfish_path=args.stockfish,
            depth=args.depth,
        )
    except FileNotFoundError:
        print(f"\nError: Stockfish not found at '{args.stockfish}'.", file=sys.stderr)
        print("Install it first:", file=sys.stderr)
        print("  Mac:   brew install stockfish", file=sys.stderr)
        print("  Linux: sudo apt install stockfish", file=sys.stderr)
        print("  Or:    https://stockfishchess.org/download/", file=sys.stderr)
        print("\nIf installed in a non-standard location, use: --stockfish /path/to/stockfish", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    output_path.write_text(json.dumps(result, indent=2))
    print(f"Saved: {output_path}")
    print(f"\nNext: python chat.py {output_path}")


if __name__ == "__main__":
    main()
