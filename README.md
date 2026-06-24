# Chess Coach

A pipeline that turns a chess PGN into a rich analysis report you can paste into any AI chat (Claude.ai, ChatGPT, etc.) and ask plain-language questions about. Stockfish handles all the chess logic; the AI handles the explanation.

## How it works

1. **Paste a PGN** — the game you want to understand
2. **Stockfish analyses every position** — eval, top 3 engine lines, W/D/L%, move quality (CPL), clock times
3. **You get two output files:**
   - `game_analysis.json` — structured data for programmatic use
   - `game_analysis.md` — a self-contained Markdown report ready to paste into an AI chat

The Markdown report includes a critical positions summary, move-by-move breakdown with FEN + eval + engine lines, ASCII board diagrams at key moments, and the full PGN at the end. Paste the whole file and ask things like:

> *"Why did my position collapse after move 28?"*
> *"What should I have played instead of Nxe4?"*
> *"Summarise the key mistakes in this game."*

There is also a **mobile-friendly web interface** (FastAPI + Claude API) that lets you paste a PGN and chat with an AI about it directly in the browser.

---

## Requirements

- Python 3.10+
- [Stockfish](https://stockfishchess.org/download/)
- An [Anthropic API key](https://console.anthropic.com/) (only needed for the web interface and `chat.py`)

```bash
# Install Stockfish
# Mac:
brew install stockfish
# Linux (Ubuntu/Debian):
sudo apt-get update && sudo apt-get install stockfish

# Install Python dependencies
pip install -r requirements.txt
```

---

## Usage

### CLI — analyse a game and get a Markdown report

```bash
# From a PGN file
python3 analyze.py my_game.pgn

# Paste PGN directly (press Ctrl+D when done)
python3 analyze.py

# Output files: my_game_analysis.json + my_game_analysis.md
```

**Options:**

| Flag | Default | Description |
|------|---------|-------------|
| `--depth N` | 18 | Stockfish search depth for all positions |
| `--critical-depth N` | 25 | Deeper search for ⚠️ critical positions only |
| `--multipv N` | 3 | Top engine lines to record per position |
| `--stockfish PATH` | `stockfish` | Path to Stockfish binary if not on PATH |
| `--output PATH` | `game_analysis.json` | Override output filename |

Set `--critical-depth` equal to `--depth` to disable the targeted depth bump. Increase `--multipv` to 5 for more alternatives; set to 1 to roughly halve analysis time.

**Typical time:** ~10–20 minutes for a 40-move game at the defaults (depth 18, critical depth 25, top 3 lines).

---

### CLI — chat about the analysis

After running `analyze.py`, use `chat.py` for a terminal Q&A loop:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python3 chat.py game_analysis.json
```

---

### Web interface (mobile browser)

A mobile-friendly single-page app: paste PGN → live Stockfish progress → chat view.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python3 -m uvicorn server:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` in a browser. In a GitHub Codespace, go to **Ports → 8000 → Make Public** and open the forwarded URL on your phone.

---

## What's in the report

Every move gets:
- **FEN** — exact board state
- **Evaluation** — centipawn score from White's perspective (positive = White better; 100cp ≈ one pawn)
- **CPL + classification** — centipawn loss vs engine best; tagged as best / excellent / good / inaccuracy / mistake / blunder
- **W/D/L %** — win/draw/loss probabilities (Stockfish NNUE)
- **Top 3 engine lines** — best alternatives from the pre-move position with evals
- **Best continuation** — engine's recommended follow-up (up to 20 moves at critical positions)
- **Clock time** — time remaining before the move, if present in the PGN

⚠️ **Critical positions** (eval swing ≥ 0.5 pawns) also get:
- ASCII board diagram
- Full list of what you could have played instead
- Re-analysis at the deeper `--critical-depth` for trustworthy tactical verdicts

The report header includes:
- Engine name and depth settings used
- **ACPL** (average centipawn loss) for both sides — a quick summary of overall accuracy

---

## Project structure

```
analyze.py        # PGN → JSON + Markdown (main pipeline, no API key needed)
chat.py           # Terminal Q&A loop using Claude API
server.py         # FastAPI web server (SSE streaming)
static/
  index.html      # Mobile-friendly single-page UI
requirements.txt
```

---

## Notes on accuracy

- **Positional verdicts** (pawn structure, piece activity, strategic plans) are reliable at depth 18 and won't change at higher depths.
- **Tactical / forcing lines** with mate threats are re-analysed at `--critical-depth 25` by default, which resolves most flip-flopping mate-in-N evaluations.
- `delta_cp` (eval swing) is always computed at base depth for consistency. Only the authoritative eval at critical positions is upgraded to the deeper depth.
- Centipawn loss (CPL) compares the best available move to the move actually played, measured from the mover's perspective.
