#!/usr/bin/env python3
"""
update_agent_stats.py

Generates an AI agent activity heatmap SVG from AgentsView SQLite data
and updates README.md with the new section.

Usage:
    python3 update_agent_stats.py [--db-path PATH] [--readme-path PATH]
                                   [--svg-path PATH] [--days N]
"""

import argparse
import os
import sqlite3
from datetime import date, timedelta, timezone, datetime


# ---------------------------------------------------------------------------
# Data Access
# ---------------------------------------------------------------------------

def load_sessions(db_path: str, days: int) -> tuple[dict[str, int], dict]:
    """Return (daily_counts, summary_stats) for the last `days` days."""
    db_path = os.path.expanduser(db_path)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cutoff = (date.today() - timedelta(days=days)).isoformat()

    # Daily session counts
    cur.execute(
        """
        SELECT DATE(started_at) AS day, COUNT(*) AS cnt
        FROM sessions
        WHERE deleted_at IS NULL
          AND DATE(started_at) >= ?
        GROUP BY day
        ORDER BY day
        """,
        (cutoff,),
    )
    daily_counts: dict[str, int] = {row[0]: row[1] for row in cur.fetchall()}

    # Summary stats
    cur.execute(
        """
        SELECT
            COUNT(*)                          AS total_sessions,
            COUNT(DISTINCT project)           AS total_projects,
            SUM(message_count)                AS total_messages,
            COUNT(DISTINCT agent)             AS total_agents,
            MIN(DATE(started_at))             AS earliest_date
        FROM sessions
        WHERE deleted_at IS NULL
        """
    )
    row = cur.fetchone()
    summary = {
        "total_sessions": row[0] or 0,
        "total_projects": row[1] or 0,
        "total_messages": row[2] or 0,
        "total_agents": row[3] or 0,
        "earliest_date": row[4] or date.today().isoformat(),
    }

    conn.close()
    return daily_counts, summary


# ---------------------------------------------------------------------------
# SVG Generation
# ---------------------------------------------------------------------------

COLORS = {
    0: "#161B22",      # empty
    1: "#2d1b69",      # low
    2: "#5b21b6",      # med-low
    3: "#7c3aed",      # med
    4: "#a78bfa",      # high
}

CELL = 13   # px
GAP = 3     # px
STEP = CELL + GAP


def _color_level(count: int, p25: float, p50: float, p75: float, p90: float) -> int:
    if count == 0:
        return 0
    if count <= p25:
        return 1
    if count <= p50:
        return 2
    if count <= p75:
        return 3
    return 4


def _compute_thresholds(daily_counts: dict[str, int]) -> tuple[float, float, float, float]:
    nonzero = sorted(v for v in daily_counts.values() if v > 0)
    if not nonzero:
        return 1.0, 1.0, 1.0, 1.0

    def percentile(data, pct):
        idx = (len(data) - 1) * pct / 100
        lo, hi = int(idx), min(int(idx) + 1, len(data) - 1)
        return data[lo] + (data[hi] - data[lo]) * (idx - lo)

    return (
        percentile(nonzero, 25),
        percentile(nonzero, 50),
        percentile(nonzero, 75),
        percentile(nonzero, 90),
    )


def _build_grid(days: int) -> list[date]:
    """
    Return a list of dates covering the heatmap grid.
    The grid starts on the Monday on or before (today - days + 1),
    so that the last cell is today and alignment is correct.
    """
    today = date.today()
    start_raw = today - timedelta(days=days - 1)
    # Roll back to Monday (weekday 0)
    start = start_raw - timedelta(days=start_raw.weekday())
    grid = []
    d = start
    while d <= today:
        grid.append(d)
        d += timedelta(days=1)
    return grid


def generate_svg(daily_counts: dict[str, int], summary: dict, days: int) -> str:
    grid = _build_grid(days)

    p25, p50, p75, p90 = _compute_thresholds(daily_counts)

    # Organise grid into (col, row) -> date
    # col = week index, row = day-of-week (0=Mon, 6=Sun)
    start_date = grid[0]
    num_weeks = (len(grid) + 6) // 7

    # Layout constants
    LEFT_PAD = 30      # for day labels
    TOP_PAD = 28       # for month labels
    BOTTOM_PAD = 40    # for legend
    RIGHT_PAD = 10

    canvas_w = LEFT_PAD + num_weeks * STEP + RIGHT_PAD
    canvas_h = TOP_PAD + 7 * STEP + BOTTOM_PAD + 20  # +20 for summary text

    lines = []
    lines.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {canvas_w} {canvas_h}" '
        f'width="{canvas_w}" height="{canvas_h}" '
        f'style="background:#0D1117;">'
    )

    # ----- Month labels -----
    prev_month = None
    for d in grid:
        if d.weekday() == 0:  # start of a new week column
            col = (d - start_date).days // 7
            if d.month != prev_month:
                x = LEFT_PAD + col * STEP
                month_label = d.strftime("%b")
                lines.append(
                    f'<text x="{x}" y="{TOP_PAD - 6}" '
                    f'fill="#8B949E" font-size="10" font-family="monospace">'
                    f'{month_label}</text>'
                )
                prev_month = d.month

    # ----- Day labels (Mon, Wed, Fri) -----
    day_labels = {0: "Mon", 2: "Wed", 4: "Fri"}
    for row_idx, label in day_labels.items():
        y = TOP_PAD + row_idx * STEP + CELL
        lines.append(
            f'<text x="{LEFT_PAD - 4}" y="{y}" '
            f'fill="#8B949E" font-size="9" font-family="monospace" '
            f'text-anchor="end">{label}</text>'
        )

    # ----- Cells -----
    today = date.today()
    for d in grid:
        if d > today:
            continue
        col = (d - start_date).days // 7
        row = d.weekday()  # 0=Mon, 6=Sun
        count = daily_counts.get(d.isoformat(), 0)
        level = _color_level(count, p25, p50, p75, p90)
        color = COLORS[level]
        x = LEFT_PAD + col * STEP
        y = TOP_PAD + row * STEP
        tip = f"{d.isoformat()}: {count} session{'s' if count != 1 else ''}"
        lines.append(
            f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" '
            f'rx="2" ry="2" fill="{color}">'
            f'<title>{tip}</title>'
            f'</rect>'
        )

    # ----- Legend -----
    legend_y = TOP_PAD + 7 * STEP + 8
    lines.append(
        f'<text x="{LEFT_PAD}" y="{legend_y + CELL}" '
        f'fill="#8B949E" font-size="9" font-family="monospace">Less</text>'
    )
    legend_x = LEFT_PAD + 28
    for level in range(5):
        x = legend_x + level * (CELL + 2)
        lines.append(
            f'<rect x="{x}" y="{legend_y}" width="{CELL}" height="{CELL}" '
            f'rx="2" ry="2" fill="{COLORS[level]}"/>'
        )
    more_x = legend_x + 5 * (CELL + 2) + 2
    lines.append(
        f'<text x="{more_x}" y="{legend_y + CELL}" '
        f'fill="#8B949E" font-size="9" font-family="monospace">More</text>'
    )

    # ----- Summary text -----
    since = summary["earliest_date"][:7]  # YYYY-MM
    summary_text = (
        f"{summary['total_sessions']} sessions across "
        f"{summary['total_projects']} projects | "
        f"{summary['total_messages']:,} total messages | "
        f"Since {since}"
    )
    summary_y = legend_y + CELL + 14
    lines.append(
        f'<text x="{canvas_w // 2}" y="{summary_y}" '
        f'fill="#8B949E" font-size="10" font-family="monospace" '
        f'text-anchor="middle">{summary_text}</text>'
    )

    lines.append("</svg>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# README Update
# ---------------------------------------------------------------------------

START_MARKER = "<!-- AGENT_STATS_START -->"
END_MARKER = "<!-- AGENT_STATS_END -->"
INSERTION_ANCHOR = "## Recent Activity"


def build_readme_section(summary: dict, svg_path: str) -> str:
    since = summary["earliest_date"][:7]
    projects = summary["total_projects"]
    return (
        f"{START_MARKER}\n"
        f"## AI Agent Activity\n\n"
        f'<p align="center">\n'
        f'  <img src="{svg_path}" alt="AI Agent Activity Heatmap" width="98%">\n'
        f"</p>\n\n"
        f"> Powered by [AgentsView](https://www.agentsview.io/) — "
        f"tracking AI coding sessions across {projects} projects since {since}\n"
        f"{END_MARKER}"
    )


def update_readme(readme_path: str, section: str) -> None:
    with open(readme_path, "r", encoding="utf-8") as fh:
        content = fh.read()

    if START_MARKER in content and END_MARKER in content:
        # Replace existing section
        before = content[: content.index(START_MARKER)]
        after = content[content.index(END_MARKER) + len(END_MARKER):]
        new_content = before + section + after
    elif INSERTION_ANCHOR in content:
        # Insert before ## Recent Activity
        new_content = content.replace(
            INSERTION_ANCHOR,
            section + "\n\n" + INSERTION_ANCHOR,
        )
    else:
        # Append at end
        new_content = content.rstrip() + "\n\n" + section + "\n"

    with open(readme_path, "w", encoding="utf-8") as fh:
        fh.write(new_content)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate AI agent activity heatmap and update README.md"
    )
    parser.add_argument(
        "--db-path",
        default="~/.agentsview/sessions.db",
        help="Path to AgentsView SQLite database",
    )
    parser.add_argument(
        "--readme-path",
        default="README.md",
        help="Path to README.md to update",
    )
    parser.add_argument(
        "--svg-path",
        default="agent-activity.svg",
        help="Output path for the SVG heatmap",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=365,
        help="Number of past days to include in the heatmap",
    )
    args = parser.parse_args()

    print(f"Loading sessions from {args.db_path} ...")
    daily_counts, summary = load_sessions(args.db_path, args.days)

    print(
        f"  Found {sum(daily_counts.values())} sessions in last {args.days} days "
        f"({len(daily_counts)} active days)"
    )
    print(
        f"  All-time: {summary['total_sessions']} sessions, "
        f"{summary['total_projects']} projects, "
        f"{summary['total_messages']:,} messages"
    )

    print(f"Generating SVG heatmap -> {args.svg_path} ...")
    svg_content = generate_svg(daily_counts, summary, args.days)
    svg_abs = os.path.expanduser(args.svg_path)
    with open(svg_abs, "w", encoding="utf-8") as fh:
        fh.write(svg_content)
    print(f"  SVG written ({len(svg_content):,} bytes)")

    print(f"Updating README -> {args.readme_path} ...")
    section = build_readme_section(summary, args.svg_path)
    update_readme(os.path.expanduser(args.readme_path), section)
    print("  README updated successfully")


if __name__ == "__main__":
    main()
