#!/usr/bin/env python3
"""
Analyze a chess game PGN with Stockfish and save structured JSON + Markdown.

Usage:
  python analyze.py game.pgn
  python analyze.py game.pgn --depth 20
  python analyze.py game.pgn --multipv 5
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
import re
import sys
import io
from pathlib import Path

CRITICAL_THRESHOLD_CP = 50
DEFAULT_DEPTH = 18
DEFAULT_PV_LENGTH = 8
CRITICAL_PV_LENGTH = 20
DEFAULT_MULTIPV = 3

_CLK_RE = re.compile(r'\[%clk (\d+:\d+:\d+\.?\d*)\]')

_CLASSIFICATION_THRESHOLDS = [
    (5,   "best"),
    (15,  "excellent"),
    (25,  "good"),
    (100, "inaccuracy"),
    (300, "mistake"),
]


def _classify(cpl):
    for threshold, label in _CLASSIFICATION_THRESHOLDS:
        if cpl <= threshold:
            return label
    return "blunder"


def render_board(fen):
    """Return a labeled ASCII board diagram from a FEN string."""
    board = chess.Board(fen)
    rows = str(board).split("\n")
    lines = ["  a b c d e f g h"]
    for rank_idx, row in enumerate(rows):
        rank_num = 8 - rank_idx
        lines.append(f"{rank_num} {row}")
    return "\n".join(lines)


def get_white_cp(score):
    """Centipawn score from White's perspective. Forced mate maps to ±10000."""
    return score.white().score(mate_score=10000)


def _get_mover_cp(score, turn):
    """Centipawn score from the mover's perspective (positive = good for mover)."""
    wcp = score.white().score(mate_score=10000)
    return wcp if turn == chess.WHITE else -wcp


def cp_to_label(cp):
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


def pv_to_san(board, pv, max_moves=DEFAULT_PV_LENGTH):
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


def _parse_clock(comment):
    if not comment:
        return None
    m = _CLK_RE.search(comment)
    return m.group(1) if m else None


def _format_wdl(wdl):
    if wdl is None:
        return None
    # info["wdl"] is a PovWdl; .white() gives the Wdl from White's perspective
    wdl_w = wdl.white()
    total = wdl_w.wins + wdl_w.draws + wdl_w.losses
    if total == 0:
        return None
    w = round(wdl_w.wins * 100 / total)
    d = round(wdl_w.draws * 100 / total)
    l = round(wdl_w.losses * 100 / total)
    return f"{w}%/{d}%/{l}%"


def format_report(analysis):
    """Convert analysis JSON into a Markdown report ready to paste into an AI chat."""
    meta = analysis["metadata"]
    eng = analysis.get("engine", {})
    lines = []

    # ── Header ────────────────────────────────────────────────────────────────────────
    lines.append("# Chess Game Analysis\n")
    lines.append(f"**White:** {meta.get('White', '?')}  ")
    lines.append(f"**Black:** {meta.get('Black', '?')}  ")
    if meta.get("Event"):
        lines.append(f"**Event:** {meta['Event']}  ")
    if meta.get("Date"):
        lines.append(f"**Date:** {meta['Date']}  ")
    lines.append(f"**Result:** {meta.get('Result', '?')}  ")
    if meta.get("Opening"):
        lines.append(f"**Opening:** {meta['Opening']}  ")
    elif meta.get("ECO"):
        lines.append(f"**ECO:** {meta['ECO']}  ")

    if eng:
        lines.append(
            f"**Engine:** {eng.get('name', 'Stockfish')} · "
            f"depth {eng.get('depth', '?')} · "
            f"top {eng.get('multipv', '?')} lines  "
        )

    lines.append(f"\n**Starting evaluation:** {analysis['initial_eval_label']}")

    # ── ACPL summary ────────────────────────────────────────────────────────────────
    all_moves = analysis["moves"]
    white_moves = [m for m in all_moves if m["side"] == "white" and m.get("cpl") is not None]
    black_moves = [m for m in all_moves if m["side"] == "black" and m.get("cpl") is not None]
    if white_moves or black_moves:
        w_acpl = round(sum(m["cpl"] for m in white_moves) / len(white_moves)) if white_moves else "N/A"
        b_acpl = round(sum(m["cpl"] for m in black_moves) / len(black_moves)) if black_moves else "N/A"
        lines.append(f"**ACPL:** White {w_acpl} / Black {b_acpl}  ")

    # ── Centipawn convention note ─────────────────────────────────────────────────────────
    white = meta.get("White", "White")
    black = meta.get("Black", "Black")
    lines.append(
        f"\n> Centipawn convention: positive scores favour **{white}** (White), "
        f"negative scores favour **{black}** (Black). 100cp ≈ one pawn of advantage. "
        f"Moves marked ⚠️ had an evaluation swing of ≥0.5 pawns."
    )

    # ── Critical positions summary ────────────────────────────────────────────────────
    critical = [m for m in all_moves if m["is_critical"]]
    lines.append("\n---\n")
    lines.append("## Critical Positions\n")
    if critical:
        for m in critical:
            num = m["move_number"]
            san = m["san"]
            side = m["side"]
            delta = m["eval_delta_cp"]
            cls = m.get("classification", "")
            label = f"{num}. {san}" if side == "white" else f"{num}... {san}"
            direction = "↑ White gains" if delta > 0 else "↓ Black gains"
            cls_str = f" · **{cls}**" if cls else ""
            lines.append(f"- **{label}** ({side.title()}) — Δ{delta:+d}cp {direction} → {m['eval_label']}{cls_str}")
    else:
        lines.append("_No moves with evaluation swings ≥0.5 pawns detected._")

    # ── Move-by-move ─────────────────────────────────────────────────────────────────
    lines.append("\n---\n")
    lines.append("## Move-by-Move Analysis\n")

    for move in all_moves:
        num = move["move_number"]
        san = move["san"]
        side = move["side"]
        delta = move["eval_delta_cp"]
        is_critical = move["is_critical"]
        cls = move.get("classification", "")
        cpl = move.get("cpl")

        heading = f"### Move {num}. {san} (White)" if side == "white" else f"### Move {num}... {san} (Black)"
        if is_critical:
            heading += " ⚠️ CRITICAL"
        lines.append(heading)

        lines.append(f"\n**FEN:** `{move['fen_after']}`  ")
        lines.append(f"**Evaluation:** {move['eval_label']} ({move['eval_cp']:+d}cp)  ")
        lines.append(f"**Change from previous move:** {delta:+d}cp  ")

        if cls and cpl is not None:
            lines.append(f"**Quality:** {cls} (CPL: {cpl})  ")

        wdl_str = move.get("wdl_str")
        if wdl_str:
            lines.append(f"**W/D/L:** {wdl_str}  ")

        clock = move.get("clock_before")
        if clock:
            lines.append(f"**Clock before move:** {clock}  ")

        # Top alternatives from pre-move analysis
        top_lines = move.get("top_lines", [])
        if top_lines:
            parts = [f"{t['move_san']} ({t['eval_cp']:+d}cp)" for t in top_lines]
            played_note = f"{san} ({move['eval_cp']:+d}cp)" + (" ⚠️" if is_critical else "")
            lines.append(f"**Best moves:** {' · '.join(parts)} | **Played:** {played_note}")

        if move.get("best_continuation"):
            lines.append(f"**Engine's best continuation:** {' '.join(move['best_continuation'])}")

        if is_critical:
            lines.append(f"\n**Board position:**\n```\n{render_board(move['fen_after'])}\n```")

            if top_lines:
                lines.append("\n**What you could have played instead:**")
                for i, t in enumerate(top_lines, 1):
                    cont = " ".join(t.get("continuation", []))
                    lines.append(
                        f"- Line {i}: **{t['move_san']}** ({t['eval_label']}, {t['eval_cp']:+d}cp)"
                        + (f": {cont}" if cont else "")
                    )

        lines.append("")

    # ── Full PGN at end for reference ────────────────────────────────────────────────
    if analysis.get("pgn"):
        lines.append("\n---\n")
        lines.append("## Full PGN\n")
        lines.append(f"```\n{analysis['pgn'].strip()}\n```")

    return "\n".join(lines)


def analyze_game(
    pgn_text,
    stockfish_path="stockfish",
    depth=DEFAULT_DEPTH,
    multipv=DEFAULT_MULTIPV,
    progress_callback=None,
    silent=False,
):
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        raise ValueError("Could not parse PGN — check the format and try again.")

    metadata = dict(game.headers)
    board = game.board()
    moves = []

    with chess.engine.SimpleEngine.popen_uci(stockfish_path) as engine:
        # Enable WDL output if supported (Stockfish 12+)
        try:
            engine.configure({"UCI_ShowWDL": True})
        except Exception:
            pass

        # Capture engine metadata
        engine_meta = {
            "name": engine.id.get("name", "unknown"),
            "depth": depth,
            "multipv": multipv,
            "threads": (engine.options["Threads"].default if "Threads" in engine.options else None) or 1,
            "hash_mb": (engine.options["Hash"].default if "Hash" in engine.options else None) or 16,
        }

        # Evaluate starting position
        init_info = engine.analyse(board, chess.engine.Limit(depth=depth))
        initial_cp = get_white_cp(init_info["score"])
        prev_cp = initial_cp

        if not silent and not progress_callback:
            print(f"Starting position: {cp_to_label(initial_cp)}")
            print(f"Analyzing {sum(1 for _ in game.mainline_moves())} moves at depth {depth}, top {multipv} lines...\n")

        half_move = 0
        for node in game.mainline():
            move = node.move
            parent_board = board.copy()
            san = parent_board.san(move)
            side = "white" if parent_board.turn == chess.WHITE else "black"
            move_num = parent_board.fullmove_number
            clock_before = _parse_clock(node.comment)

            # ── Pre-move MultiPV: top-N alternatives + best eval for ACPL ──
            pre_info = engine.analyse(
                parent_board,
                chess.engine.Limit(depth=depth),
                multipv=multipv,
            )
            best_mover_cp = _get_mover_cp(pre_info[0]["score"], parent_board.turn)

            top_lines = []
            for li in pre_info:
                pv = li.get("pv", [])
                if not pv:
                    continue
                try:
                    first_san = parent_board.san(pv[0])
                except Exception:
                    continue
                top_lines.append({
                    "move_san": first_san,
                    "eval_cp": get_white_cp(li["score"]),
                    "eval_label": cp_to_label(get_white_cp(li["score"])),
                    "continuation": pv_to_san(parent_board, pv, max_moves=8),
                })

            # ── Push move, evaluate resulting position ───────────────────
            board.push(move)
            half_move += 1

            post_info = engine.analyse(board, chess.engine.Limit(depth=depth))
            cur_cp = get_white_cp(post_info["score"])
            delta_cp = cur_cp - prev_cp
            is_critical = abs(delta_cp) >= CRITICAL_THRESHOLD_CP

            wdl_str = _format_wdl(post_info.get("wdl"))

            # ACPL: best-move eval minus played-move eval, from mover's perspective
            played_mover_cp = _get_mover_cp(post_info["score"], parent_board.turn)
            cpl = max(0, best_mover_cp - played_mover_cp)
            classification = _classify(cpl)

            stored_pv_limit = CRITICAL_PV_LENGTH if is_critical else DEFAULT_PV_LENGTH
            best_continuation = pv_to_san(board, post_info.get("pv", []), max_moves=stored_pv_limit)

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
                "top_lines": top_lines,
                "cpl": cpl,
                "classification": classification,
                "wdl_str": wdl_str,
                "clock_before": clock_before,
            }

            moves.append(move_data)
            prev_cp = cur_cp

            if progress_callback:
                progress_callback(move_data)
            elif not silent:
                label = f"{move_num}{'.' if side == 'white' else '...'} {san}"
                flag = " *** CRITICAL ***" if is_critical else ""
                print(f"  {label:<20}  {cp_to_label(cur_cp):<38}  Δ{delta_cp:+d}cp  [{classification}]{flag}")

    critical_count = sum(1 for m in moves if m["is_critical"])
    if not silent and not progress_callback:
        print(f"\nDone. {len(moves)} moves, {critical_count} critical positions.")

    return {
        "engine": engine_meta,
        "pgn": pgn_text,
        "metadata": metadata,
        "initial_eval_cp": initial_cp,
        "initial_eval_label": cp_to_label(initial_cp),
        "moves": moves,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Analyze a chess PGN with Stockfish and save structured JSON + Markdown.",
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
        "--multipv",
        type=int,
        default=DEFAULT_MULTIPV,
        help=f"Top lines per position (default: {DEFAULT_MULTIPV}; use 1 to roughly halve analysis time)",
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
        output_path = (
            Path(args.output)
            if args.output != "game_analysis.json"
            else pgn_path.with_name(pgn_path.stem + "_analysis.json")
        )
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
            multipv=args.multipv,
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
    md_path = output_path.with_suffix(".md")
    md_path.write_text(format_report(result))
    print(f"Saved: {output_path}")
    print(f"Saved: {md_path}")
    print(f"\nPaste {md_path} into your AI chat to ask questions about the game.")


if __name__ == "__main__":
    main()
