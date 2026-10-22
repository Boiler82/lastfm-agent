"""
Last.fm Music Agent
Ask questions about the Last.fm global Top 50 in plain English.
The agent writes its own SQL, runs it on Snowflake (read-only) and answers
in text or with a chart. Follow-up questions remember the conversation.

Run:  python agent.py
"""

import atexit 
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import anthropic
from dotenv import load_dotenv
load_dotenv()   # reads the API key and Snowflake settings from the .env file
import matplotlib
matplotlib.use("Agg")               # draw charts to files, without opening windows
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
import pandas as pd
import snowflake.connector

MODEL = os.getenv("AGENT_MODEL", "claude-sonnet-5-5")   # default model, can be changed per run
MAX_STEPS = 10                      # safety limit so the loop can't run forever
MAX_ROWS = 50                       # rows sent back to Claude per query
CHART_FOLDER = Path("charts")

# Chart colors: one calm blue for the data, soft greys for everything else
BAR_COLOR = "#2a78d6"
BACKGROUND = "#fcfcfb"
TEXT_DARK = "#0b0b0b"
TEXT_SOFT = "#52514e"
GRID = "#e4e3df"


# ---------- Snowflake connection (opened once, reused) ----------
_conn = None

def get_connection():
    """Log in as the read-only user with key-pair authentication."""
    global _conn
    if _conn is None:
        params = dict(
            account=os.environ["SNOWFLAKE_ACCOUNT"],
            user=os.environ["SNOWFLAKE_USER"],
            role=os.environ["SNOWFLAKE_ROLE"],
            warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
            database="LASTFM",
            private_key_file=os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"],
            session_parameters={"STATEMENT_TIMEOUT_IN_SECONDS": 30},  # no runaway queries
            enable_server_session_keep_alive_auto_detection=False,    # always log out cleanly
        )
        if os.getenv("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"):
            params["private_key_file_pwd"] = os.environ["SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"]
        _conn = snowflake.connector.connect(**params)
        atexit.register(_conn.close)   # log out of Snowflake when the program ends
    return _conn


def is_read_only(query: str) -> bool:
    """Extra safety on top of the read-only Snowflake role: only SELECT / WITH."""
    first_word = re.sub(r"^\s*(--[^\n]*\n|/\*.*?\*/|\s)*", "", query, flags=re.S).split(None, 1)
    return bool(first_word) and first_word[0].upper() in {"SELECT", "WITH"} and ";" not in query.strip().rstrip(";")


def query_to_df(query: str) -> pd.DataFrame:
    if not is_read_only(query):
        raise ValueError("Only a single SELECT (or WITH ... SELECT) query is allowed.")
    cur = get_connection().cursor()
    try:
        cur.execute(query)
        rows = cur.fetchmany(1000)
        columns = [c[0].lower() for c in cur.description]
        return pd.DataFrame(rows, columns=columns)
    finally:
        cur.close()


# ---------- Tool 1: run SQL ----------
def run_sql(query: str) -> str:
    try:
        df = query_to_df(query)
        if df.empty:
            return "The query returned no rows."
        note = f"\n(Showing the first {MAX_ROWS} of {len(df)} rows.)" if len(df) > MAX_ROWS else ""
        return df.head(MAX_ROWS).to_string(index=False) + note
    except Exception as e:
        # Sending the error back lets the agent fix its own query
        return f"SQL error: {e}. Fix the query and try again."


# ---------- Tool 2: make a chart ----------
def short_date_labels(labels: pd.Series) -> pd.Series | None:
    """Turn '2026-10-01' into '1 Oct' (and '2026-10' into 'Oct 2026'). None if the labels aren't dates."""
    if not labels.str.match(r"^\d{4}-\d{2}(-\d{2})?").all():
        return None
    dates = pd.to_datetime(labels.str[:10], errors="coerce")
    if dates.isna().any():
        return None
    if labels.str.len().max() == 7:                       # months, e.g. 2026-10
        return dates.dt.strftime("%b %Y")
    one_year = dates.dt.year.nunique() == 1
    return dates.dt.day.astype(str) + dates.dt.strftime(" %b" if one_year else " %b %y")


def make_chart(query: str, chart_type: str, x: str, y: str, title: str,
               y_label: str = "", invert_y: bool = False) -> str:
    try:
        df = query_to_df(query)
    except Exception as e:
        return f"SQL error: {e}. Fix the query and try again."
    x, y = x.lower(), y.lower()
    if df.empty:
        return "The query returned no rows, so no chart was made."
    if x not in df.columns or y not in df.columns:
        return (f"Column not found. The query returned these columns: "
                f"{list(df.columns)}. Use those names for x and y.")
    if len(df) > 30 and chart_type != "line":
        return f"Too many bars ({len(df)}). Limit the query to 30 rows or fewer."

    fig, ax = plt.subplots(figsize=(10, 6), facecolor=BACKGROUND)
    ax.set_facecolor(BACKGROUND)
    labels = df[x].astype(str)
    dates = short_date_labels(labels)
    if dates is not None:
        labels = dates                                    # short, readable dates
    values = pd.to_numeric(df[y])
    long_labels = labels.str.len().max() > 8

    if chart_type == "bar":
        ax.bar(range(len(df)), values, color=BAR_COLOR, width=0.6)
        ax.set_xticks(range(len(df)), labels)
        ax.yaxis.grid(True, color=GRID)
        if long_labels and len(df) > 6:
            plt.xticks(rotation=45, ha="right")
    elif chart_type == "barh":
        ax.barh(labels[::-1], values[::-1], color=BAR_COLOR, height=0.6)
        ax.xaxis.grid(True, color=GRID)
    elif chart_type == "line":
        ax.plot(range(len(df)), values, color=BAR_COLOR, linewidth=2.5, marker="o", markersize=4,
                solid_capstyle="round", solid_joinstyle="round")
        ax.yaxis.grid(True, color=GRID)
        # About 7 evenly spaced labels, starting at the first point and ending near the last
        step = max(1, -(-(len(df) - 1) // 6))             # rounds up
        ticks = list(range(0, len(df), step))
        if len(df) - 1 - ticks[-1] >= step / 2:           # add the last point if there is room
            ticks.append(len(df) - 1)
        ax.set_xticks(ticks, [labels.iloc[i] for i in ticks])
        if long_labels:
            plt.xticks(rotation=45, ha="right")
    else:
        plt.close(fig)
        return "Unknown chart_type. Use 'bar', 'barh' or 'line'."

    # Chart positions: #1 belongs at the top, so flip the axis and label it #1, #10, ...
    if invert_y:
        top = int(values.max())
        step = 5 if top <= 30 else 10
        ticks = [1] + list(range(step, top + step, step))   # always show #1
        position_format = FuncFormatter(lambda v, _: f"#{int(v)}")
        if chart_type == "barh":
            ax.set_xticks(ticks); ax.set_xlim(top + 1, 0); ax.xaxis.set_major_formatter(position_format)
        else:
            ax.set_yticks(ticks); ax.set_ylim(top + 1, 0.5); ax.yaxis.set_major_formatter(position_format)
    elif values.abs().max() >= 1000:
        number_axis = ax.xaxis if chart_type == "barh" else ax.yaxis
        number_axis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))

    ax.set_axisbelow(True)
    for side in ["top", "right", "left"]:
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=TEXT_SOFT, length=0, labelsize=11, pad=6)
    ax.set_title(title, loc="left", fontsize=15, color=TEXT_DARK, pad=16)
    if chart_type == "barh":
        ax.set_xlabel(y_label or y, color=TEXT_SOFT)
    else:
        ax.set_ylabel(y_label or y, color=TEXT_SOFT)
    fig.tight_layout()

    CHART_FOLDER.mkdir(exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]
    path = CHART_FOLDER / f"{datetime.now():%Y-%m-%d_%H%M%S}_{slug}.png"
    fig.savefig(path, dpi=150, facecolor=BACKGROUND)
    plt.close(fig)
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)])
    return f"Chart saved to {path}.\nData used:\n{df.head(30).to_string(index=False)}"



# ---------- Tool descriptions for Claude ----------
TOOLS = [
    {
        "name": "run_sql",
        "description": "Run a Snowflake SELECT query on the LASTFM database and return the result as text.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "One Snowflake SELECT query"}},
            "required": ["query"],
        },
    },
    {
        "name": "make_chart",
        "description": (
            "Run a Snowflake SELECT query and save the result as a chart image. Use it when the "
            "user asks for a chart, or when a comparison or trend is clearer as a picture. The query "
            "must return one label column (x) and one number column (y), in the order to show them."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "A Snowflake SELECT query returning the x and y columns"},
                "chart_type": {
                    "type": "string", "enum": ["bar", "barh", "line"],
                    "description": "bar = compare a few items; barh = rankings or long names; line = change over time",
                },
                "x": {"type": "string", "description": "Column name for the labels (tracks, artists or dates)"},
                "y": {"type": "string", "description": "Column name for the numbers"},
                "title": {"type": "string", "description": "A short title that states the main finding"},
                "y_label": {"type": "string", "description": "Axis label for the numbers, e.g. 'Chart position'"},
                "invert_y": {"type": "boolean", "description": "true when y is a chart position, so #1 is at the top"},
            },
            "required": ["query", "chart_type", "x", "y", "title"],
        },
    },
]

TOOL_FUNCTIONS = {"run_sql": run_sql, "make_chart": make_chart}


# ---------- Instructions for Claude, including the table structure ----------
def get_schema() -> str:
    df = query_to_df(
        "SELECT table_schema || '.' || table_name AS table_name, column_name, data_type "
        "FROM LASTFM.INFORMATION_SCHEMA.COLUMNS WHERE table_schema = 'STAGING' "
        "ORDER BY table_schema, table_name, ordinal_position"
    )
    lines = []
    for table, cols in df.groupby("table_name", sort=False):
        col_text = ", ".join(f"{c} ({t})" for c, t in zip(cols["column_name"], cols["data_type"]))
        lines.append(f"LASTFM.{table}: {col_text}")
    return "\n".join(lines)


def build_system_prompt() -> str:
    return f"""You are a music data analyst. Answer questions about the Last.fm global Top 50,
stored in Snowflake (database LASTFM). Write Snowflake SQL.

Tables and columns:
{get_schema()}

Notes about the data:
- STAGING.STG_TOP_TRACKS is the full history: one row per track per daily snapshot of the global Top 50.
  LOADED_AT is when the snapshot was taken (one snapshot per day); use DATE(LOADED_AT) for the day.
- CHART_POSITION 1 is the top of the chart and 50 the bottom. A lower number is better.
- A DROP means the position number gets bigger, a CLIMB means it gets smaller.
  Drop = CHART_POSITION - previous position (#10 -> #50 is a drop of 40).
  Climb = previous position - CHART_POSITION (#30 -> #10 is a climb of 20).
- A track is identified by TRACK_NAME and ARTIST_NAME together, never by the name alone.
- To compare a track with the day before, join to the PREVIOUS DAY's snapshot (the latest earlier
  LOADED_AT), not just the track's previous appearance: a track missing from the previous day is a
  new entry or a re-entry, not a climb.
- Only the Top 50 is recorded: a track that leaves the chart has no data until it returns.
- "Today" or "now" means the latest LOADED_AT in the data. Say which dates your answer covers.

Rules:
- Use run_sql to get data. Never guess numbers.
- Use make_chart when the user asks for a chart, or when a picture makes the answer clearer.
  For chart positions, set invert_y to true.
- Only write SELECT queries.
- If a query fails, read the error and try a corrected query.
- If a question is vague (for example "best" or "most popular"), say how you interpreted it.
- Finish with a short, clear answer in plain English. If you made a chart, say where it was saved."""


_system_prompt = None

def system_prompt() -> str:
    global _system_prompt
    if _system_prompt is None:
        _system_prompt = build_system_prompt()
    return _system_prompt


# ---------- The agent loop ----------
def ask(question: str, messages: list | None = None) -> str:
    """Answer one question. Pass the same `messages` list again for follow-up questions."""
    client = anthropic.Anthropic()
    messages = [] if messages is None else messages
    messages.append({"role": "user", "content": question})

    for step in range(MAX_STEPS):
        response = client.messages.create(
            model=MODEL, max_tokens=1500, system=system_prompt(), tools=TOOLS, messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            return "".join(b.text for b in response.content if b.type == "text")

        results = []
        for block in response.content:
            if block.type == "tool_use":
                print(f"  [step {step + 1}] {block.name}: {block.input['query']}")
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": TOOL_FUNCTIONS[block.name](**block.input),
                })
        messages.append({"role": "user", "content": results})

    return "Stopped: reached the maximum number of steps."


if __name__ == "__main__":
    print("Last.fm Music Agent - ask about the global Top 50.")
    print("Type 'new' to start a new conversation, or 'quit' to exit.\n")
    conversation = []
    while True:
        question = input("You: ").strip()
        if question.lower() in {"quit", "exit"}:
            break
        if question.lower() == "new":
            conversation = []
            print("(New conversation started.)\n")
            continue
        if question:
            print(f"\nAgent: {ask(question, conversation)}\n")
