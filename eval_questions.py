"""
Test questions for the Last.fm Music Agent.

Each question has:
  - question:   what we ask the agent, in plain English
  - truth_sql:  YOUR Snowflake query that calculates the correct answer (the "answer key")
  - type:       "number" or "text"
  - tolerance:  (numbers) how far off the agent may be and still count as correct
  - level:      easy / medium / hard / tricky

The data grows every day, so the correct answers are calculated LIVE from Snowflake
each time the test runs: the answer key never goes out of date.
"""

T = "LASTFM.STAGING.STG_TOP_TRACKS"

# A reusable piece of SQL: every track compared with the PREVIOUS DAY's chart.
# prev_pos is NULL when the track was not in the chart the day before.
DAY_OVER_DAY = f"""
WITH snaps AS (SELECT DISTINCT loaded_at FROM {T}),
pairs AS (SELECT loaded_at, LAG(loaded_at) OVER (ORDER BY loaded_at) AS prev_at FROM snaps),
moves AS (
  SELECT s.loaded_at, s.track_name, s.artist_name, s.chart_position AS pos,
         p.chart_position AS prev_pos, pairs.prev_at
  FROM {T} s
  JOIN pairs ON pairs.loaded_at = s.loaded_at
  LEFT JOIN {T} p ON p.loaded_at = pairs.prev_at
                 AND p.track_name = s.track_name AND p.artist_name = s.artist_name)
"""

QUESTIONS = [
    # ---------- EASY: one table, one calculation ----------
    {
        "question": "How many daily chart snapshots are in the data?",
        "truth_sql": f"SELECT COUNT(DISTINCT loaded_at) FROM {T}",
        "type": "number", "tolerance": 0, "level": "easy",
    },
    {
        "question": "How many different artists have appeared in the Top 50?",
        "truth_sql": f"SELECT COUNT(DISTINCT artist_name) FROM {T}",
        "type": "number", "tolerance": 0, "level": "easy",
    },
    {
        # A track is name + artist together, not the name alone.
        "question": "How many different tracks have appeared in the Top 50?",
        "truth_sql": f"SELECT COUNT(*) FROM (SELECT DISTINCT track_name, artist_name FROM {T})",
        "type": "number", "tolerance": 0, "level": "easy",
    },
    {
        "question": "Which track is #1 in the latest chart? Give the track name.",
        "truth_sql": f"SELECT track_name FROM {T} WHERE chart_position = 1 AND loaded_at = (SELECT MAX(loaded_at) FROM {T})",
        "type": "text", "level": "easy",
    },
    {
        "question": "What is the best (highest) chart position that SUNGLASSES by Tinashe has reached?",
        "truth_sql": f"SELECT MIN(chart_position) FROM {T} WHERE track_name = 'SUNGLASSES' AND artist_name = 'Tinashe'",
        "type": "number", "tolerance": 0, "level": "easy",
    },

    # ---------- MEDIUM: grouping and comparing ----------
    {
        "question": "Which artist has the most tracks in the latest chart?",
        "truth_sql": f"""SELECT artist_name FROM {T} WHERE loaded_at = (SELECT MAX(loaded_at) FROM {T})
                         GROUP BY artist_name ORDER BY COUNT(*) DESC LIMIT 1""",
        "type": "text", "level": "medium",
    },
    {
        "question": "Which artist has had the most different tracks in the Top 50 over the whole period?",
        "truth_sql": f"""SELECT artist_name FROM {T} GROUP BY artist_name
                         ORDER BY COUNT(DISTINCT track_name) DESC LIMIT 1""",
        "type": "text", "level": "medium",
    },
    {
        "question": "How many tracks have been in the Top 50 on every single day of the data?",
        "truth_sql": f"""SELECT COUNT(*) FROM (SELECT track_name, artist_name FROM {T} GROUP BY 1, 2
                         HAVING COUNT(DISTINCT loaded_at) = (SELECT COUNT(DISTINCT loaded_at) FROM {T}))""",
        "type": "number", "tolerance": 0, "level": "medium",
    },
    {
        # Trap: a LOWER average position is better.
        "question": ("Among tracks that were in the chart on at least 10 days, which track has the "
                     "second-best average chart position? Give the track name."),
        "truth_sql": f"""SELECT track_name FROM {T} GROUP BY track_name, artist_name
                         HAVING COUNT(*) >= 10 ORDER BY AVG(chart_position) ASC LIMIT 1 OFFSET 1""",
        "type": "text", "level": "medium",
    },

    # ---------- HARD: comparing each day with the day before ----------
    {
        "question": "What is the biggest number of positions a track has climbed from one day to the next?",
        "truth_sql": DAY_OVER_DAY + "SELECT MAX(prev_pos - pos) FROM moves",
        "type": "number", "tolerance": 0, "level": "hard",
    },
    {
        "question": "What is the biggest number of positions a track has dropped from one day to the next?",
        "truth_sql": DAY_OVER_DAY + "SELECT MAX(pos - prev_pos) FROM moves",
        "type": "number", "tolerance": 0, "level": "hard",
    },
    {
        "question": ("How many tracks in the latest chart were not in the chart the day before "
                     "(new entries or re-entries)?"),
        "truth_sql": DAY_OVER_DAY + """SELECT COUNT_IF(prev_pos IS NULL) FROM moves
                         WHERE loaded_at = (SELECT MAX(loaded_at) FROM snaps)""",
        "type": "number", "tolerance": 0, "level": "hard",
    },

    # ---------- TRICKY: a careless analyst would get these wrong ----------
    {
        # Trap: comparing with a track's PREVIOUS APPEARANCE (LAG per track) never finds a re-entry.
        "question": ("How many times has a track come back into the Top 50 after having dropped out "
                     "(a re-entry)? Count every return."),
        "truth_sql": DAY_OVER_DAY + f"""SELECT COUNT(*) FROM moves m
                         WHERE m.prev_pos IS NULL AND m.prev_at IS NOT NULL
                           AND EXISTS (SELECT 1 FROM {T} x WHERE x.track_name = m.track_name
                                       AND x.artist_name = m.artist_name AND x.loaded_at < m.prev_at)""",
        "type": "number", "tolerance": 0, "level": "tricky",
    },
    {
        # Trap: a share of the chart, not a count. 50 tracks per day.
        "question": ("What percentage of the latest chart belongs to the artist with the most tracks in it? "
                     "Round to 1 decimal."),
        "truth_sql": f"""SELECT ROUND(100 * MAX(n) / 50, 1) FROM (SELECT COUNT(*) AS n FROM {T}
                         WHERE loaded_at = (SELECT MAX(loaded_at) FROM {T}) GROUP BY artist_name)""",
        "type": "number", "tolerance": 0.1, "level": "tricky",
    },
]
