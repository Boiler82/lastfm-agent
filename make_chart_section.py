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
