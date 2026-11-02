#!/usr/bin/env python3
"""Generate self-hosted GitHub profile cards (SVG).

Why: the third-party services used before (github-readme-stats.vercel.app,
github-readme-streak-stats.herokuapp.com) are dead / rate-limited / unreachable
from mainland China. These cards are rendered by this repo's own GitHub Action
and served through a CDN, so nothing can rot underneath the README.

Data sources (no secret beyond the default GITHUB_TOKEN needed):
  * REST api.github.com  -> user, repos, stars, languages, PR/issue counts
  * github.com/users/<login>/contributions -> per-day calendar + per-year totals

Usage:
    python scripts/gen_cards.py --login dante329 --out assets
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse  # noqa: F401
import urllib.request
from datetime import date, datetime, timedelta, timezone

UA = "dante329-profile-cards/1.0 (+https://github.com/dante329/dante329)"
TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""

# ---------- palette (tokyonight-ish, matches the README accent) ----------
BG = "#0d1117"
BORDER = "#30363d"
DIVIDER = "#21262d"
TEXT = "#c9d1d9"
MUTED = "#8b949e"
ACCENT = "#7c3aed"
ACCENT_SOFT = "#a78bfa"
GOOD = "#3fb950"
FONT = "'Segoe UI', Ubuntu, 'Helvetica Neue', Helvetica, Arial, sans-serif"

LANG_COLORS = {
    "C": "#555555", "C++": "#f34b7d", "C#": "#178600", "Java": "#b07219",
    "Python": "#3572A5", "JavaScript": "#f1e05a", "TypeScript": "#3178c6",
    "HTML": "#e34c26", "CSS": "#563d7c", "SCSS": "#c6538c", "Shell": "#89e051",
    "Go": "#00ADD8", "Rust": "#dea584", "Kotlin": "#A97BFF", "Swift": "#F05138",
    "PHP": "#4F5D95", "Ruby": "#701516", "Vue": "#41b883", "Dart": "#00B4AB",
    "Jupyter Notebook": "#DA5B0B", "Objective-C": "#438eff", "Lua": "#000080",
    "Makefile": "#427819", "Dockerfile": "#384d54", "Batchfile": "#C1F12E",
    "PowerShell": "#012456", "SQL": "#e38c00", "R": "#198CE7", "Scala": "#c22d40",
    "Assembly": "#6E4C13", "MATLAB": "#e16737", "CMake": "#DA3434",
}


# ---------------------------------------------------------------- networking
def _request(url: str, accept: str = "application/json") -> str:
    headers = {"User-Agent": UA, "Accept": accept}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "replace")


def api(path: str):
    """GET api.github.com/<path> (path starts with '/')."""
    return json.loads(_request("https://api.github.com" + path))


def api_all(path: str, per_page: int = 100):
    sep = "&" if "?" in path else "?"
    out, page = [], 1
    while True:
        chunk = api(f"{path}{sep}per_page={per_page}&page={page}")
        if not chunk:
            return out
        out.extend(chunk)
        if len(chunk) < per_page:
            return out
        page += 1


def search_count(query: str) -> int:
    try:
        return int(api("/search/issues?per_page=1&q=" + urllib.parse.quote(query))["total_count"])
    except Exception as exc:  # noqa: BLE001 - card must never break the build
        print(f"[warn] search failed for {query!r}: {exc}", file=sys.stderr)
        return 0


# ------------------------------------------------------------- contributions
def _parse_calendar(html: str) -> dict[date, int]:
    """(date -> contribution count) out of a GitHub contributions fragment."""
    ids: dict = {}
    for tag in re.findall(r"<td\b[^>]*>", html):
        iso = re.search(r'data-date="(\d{4}-\d{2}-\d{2})"', tag)
        comp = re.search(r'id="contribution-day-component-([\d-]+)"', tag)
        if iso and comp:
            ids[comp.group(1)] = date.fromisoformat(iso.group(1))

    days: dict[date, int] = {}
    for comp, tip in re.findall(
        r'<tool-tip[^>]*for="contribution-day-component-([\d-]+)"[^>]*>([^<]*)</tool-tip>', html
    ):
        day = ids.get(comp)
        if not day:
            continue
        m = re.search(r"(\d+|No)\s+contributions?", tip)
        if not m:
            continue
        days[day] = 0 if m.group(1) == "No" else int(m.group(1))
    return days


def scrape_days(login: str, first_year: int) -> dict[date, int]:
    """Lifetime per-day contribution counts (one fragment per calendar year)."""
    days: dict[date, int] = {}
    this_year = datetime.now(timezone.utc).year
    for year in range(first_year, this_year + 1):
        url = f"https://github.com/users/{login}/contributions?to={year}-12-31"
        try:
            days.update(_parse_calendar(_request(url, "text/html")))
        except Exception as exc:  # noqa: BLE001 - never break the build over a card
            print(f"[warn] contributions for {year}: {exc}", file=sys.stderr)
    return days


def streaks(days: dict[date, int], today: date) -> tuple[int, date | None, int, date | None]:
    """(current_len, current_end, longest_len, longest_end) over the trailing year."""
    if not days:
        return 0, None, 0, None
    ordered = sorted(d for d in days if d <= today)

    current, end = 0, None
    probe = today if days.get(today, 0) > 0 else today - timedelta(days=1)
    while days.get(probe, 0) > 0:
        current += 1
        end = end or probe
        probe -= timedelta(days=1)

    longest, longest_end, run, run_end = 0, None, 0, None
    prev = None
    for day in ordered:
        if days.get(day, 0) > 0:
            run = run + 1 if prev == day - timedelta(days=1) else 1
            run_end = day
        else:
            run, run_end = 0, None
        if run > longest:
            longest, longest_end = run, run_end
        prev = day
    return current, end, longest, longest_end


# ------------------------------------------------------------------- helpers
def esc(text: str) -> str:
    return (
        str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def fmt(n: int) -> str:
    return f"{n:,}"


def month_day(day: date | None) -> str:
    return day.strftime("%b %-d") if day else "-"


def full_date(day: date) -> str:
    return day.strftime("%b %-d, %Y")


def svg_open(w: int, h: int, label: str) -> list[str]:
    return [
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" fill="none" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="{esc(label)}" '
        f'font-family="{FONT}">',
        f"<title>{esc(label)}</title>",
        f'<rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="8" '
        f'fill="{BG}" stroke="{BORDER}" stroke-width="1"/>',
    ]


def header(title: str, w: int) -> list[str]:
    return [
        f'<text x="25" y="36" fill="{ACCENT}" font-size="16.5" font-weight="600">'
        f"{esc(title)}</text>",
        f'<line x1="25" y1="50" x2="{w - 25}" y2="50" stroke="{DIVIDER}" stroke-width="1"/>',
    ]


ICONS = {
    # every icon is drawn on a 16x16 grid and inherits fill/stroke from its group
    "star": '<path d="M8 1.5l1.98 4.02 4.44.65-3.21 3.13.76 4.42L8 11.63l-3.97 2.09.76-4.42L1.58 6.17l4.44-.65z"/>',
    "repo": '<rect x="2.8" y="2.2" width="10.4" height="11.4" rx="1.5" fill="none"/>'
            '<path d="M6 2.4v11" fill="none"/>',
    "pr": '<circle cx="4.2" cy="12.1" r="1.65"/>'
          '<circle cx="11.8" cy="3.9" r="1.65"/>'
          '<path d="M4.2 10.3V5.4a1.5 1.5 0 0 1 1.5-1.5h4.5" fill="none"/>',
    "issue": '<circle cx="8" cy="8" r="5.9" fill="none"/>'
             '<path d="M8 4.7v3.6" fill="none"/>'
             '<circle cx="8" cy="11" r=".95" stroke="none"/>',
    "people": '<circle cx="6" cy="5.3" r="2.3"/>'
              '<path d="M2.3 13.3c0-1.9 1.7-3.2 3.7-3.2s3.7 1.3 3.7 3.2" fill="none"/>'
              '<circle cx="11.9" cy="6.4" r="1.75" fill="none"/>'
              '<path d="M11.4 10.3c1.6.2 2.8 1.3 2.8 2.9" fill="none"/>',
    "graph": '<path d="M2.6 13.3V9.5h2.2v3.8zM6.9 13.3V5.8h2.2v7.5zM11.2 13.3V2.7h2.2v10.6z" stroke="none"/>',
    "flame": '<path d="M8.6.8c.3 2.3 1.5 3.2 2.4 4.6.9 1.3 1.4 2.3 1.4 3.6a4.4 4.4 0 0 1-8.8 0c0-1.5.7-2.6 1.7-3.9.2 1.3.8 1.9 1.4 2.3-.2-2.2 1-4.3 1.9-6.6z" stroke="none"/>',
}


def row_icon(name: str, x: float, y: float, color: str, scale: float = 1.0) -> str:
    return (
        f'<g transform="translate({x},{y}) scale({scale})" fill="{color}" stroke="{color}" '
        f'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
        f"{ICONS[name]}</g>"
    )


# --------------------------------------------------------------------- cards
def render_stats(login: str, rows: list[tuple[str, str, int]], w: int = 495) -> str:
    h = 62 + len(rows) * 30 + 12
    out = svg_open(w, h, f"{login} GitHub stats")
    out += header(f"{login}'s GitHub Stats", w)
    y = 78
    for icon, label, value in rows:
        out.append(row_icon(icon, 25, y - 11, ACCENT))
        out.append(f'<text x="47" y="{y}" fill="{TEXT}" font-size="13.5">{esc(label)}</text>')
        out.append(
            f'<text x="{w - 25}" y="{y}" fill="{ACCENT_SOFT}" font-size="13.5" '
            f'font-weight="600" text-anchor="end">{esc(fmt(value))}</text>'
        )
        y += 30
    out.append("</svg>")
    return "".join(out)


def render_langs(langs: list[tuple[str, int]], w: int = 495) -> str:
    total = sum(n for _, n in langs) or 1
    h = 62 + len(langs) * 32 + 8
    out = svg_open(w, h, "Most used languages")
    out += header("Most Used Languages", w)
    y = 72
    for name, size in langs:
        pct = size * 100.0 / total
        color = LANG_COLORS.get(name, ACCENT)
        out.append(f'<text x="25" y="{y}" fill="{TEXT}" font-size="13">{esc(name)}</text>')
        out.append(
            f'<text x="{w - 25}" y="{y}" fill="{MUTED}" font-size="12" '
            f'text-anchor="end">{pct:.1f}%</text>'
        )
        out.append(
            f'<rect x="25" y="{y + 7}" width="{w - 50}" height="6" rx="3" fill="{DIVIDER}"/>'
        )
        out.append(
            f'<rect x="25" y="{y + 7}" width="{max(2, round((w - 50) * pct / 100))}" '
            f'height="6" rx="3" fill="{color}"/>'
        )
        y += 32
    out.append("</svg>")
    return "".join(out)


def render_streak(
    total: int,
    span: tuple[date, date] | None,
    current: int,
    current_end: date | None,
    longest: int,
    longest_end: date | None,
    w: int = 495,
    h: int = 200,
) -> str:
    out = svg_open(w, h, "Contribution streak")
    cx = (88, 247, 406)
    out.append(f'<line x1="170" y1="26" x2="170" y2="{h - 26}" stroke="{DIVIDER}"/>')
    out.append(f'<line x1="330" y1="26" x2="330" y2="{h - 26}" stroke="{DIVIDER}"/>')

    # left: lifetime total
    out.append(
        f'<text x="{cx[0]}" y="74" fill="{ACCENT}" font-size="34" font-weight="700" '
        f'text-anchor="middle">{esc(fmt(total))}</text>'
    )
    out.append(
        f'<text x="{cx[0]}" y="116" fill="{TEXT}" font-size="13.5" text-anchor="middle">'
        f"Total Contributions</text>"
    )
    if span:
        out.append(
            f'<text x="{cx[0]}" y="142" fill="{GOOD}" font-size="11.5" text-anchor="middle">'
            f"{esc(full_date(span[0]))} &#8211; Present</text>"
        )

    # middle: current streak ring
    r, cy, sw = 35, 86, 6
    circ = 2 * 3.141592653589793 * r
    frac = 0.0 if longest <= 0 else min(1.0, current / float(longest))
    out.append(f'<circle cx="{cx[1]}" cy="{cy}" r="{r}" stroke="{DIVIDER}" stroke-width="{sw}"/>')
    out.append(
        f'<circle cx="{cx[1]}" cy="{cy}" r="{r}" stroke="{ACCENT}" stroke-width="{sw}" '
        f'stroke-linecap="round" fill="none" stroke-dasharray="{circ:.2f}" '
        f'stroke-dashoffset="{circ * (1 - frac):.2f}" transform="rotate(-90 {cx[1]} {cy})"/>'
    )
    out.append(row_icon("flame", cx[1] - 8.5, cy - r - 26, ACCENT, 1.1))
    out.append(
        f'<text x="{cx[1]}" y="{cy + 11}" fill="{ACCENT}" font-size="30" font-weight="700" '
        f'text-anchor="middle">{current}</text>'
    )
    out.append(
        f'<text x="{cx[1]}" y="150" fill="{ACCENT}" font-size="13.5" text-anchor="middle">'
        f"Current Streak</text>"
    )
    if current:
        out.append(
            f'<text x="{cx[1]}" y="174" fill="{GOOD}" font-size="11.5" text-anchor="middle">'
            f"{esc(full_date(current_end))}</text>"
        )
    else:
        out.append(
            f'<text x="{cx[1]}" y="174" fill="{MUTED}" font-size="11.5" text-anchor="middle">'
            f"no active streak</text>"
        )

    # right: longest streak
    out.append(
        f'<text x="{cx[2]}" y="74" fill="{ACCENT}" font-size="34" font-weight="700" '
        f'text-anchor="middle">{longest}</text>'
    )
    out.append(
        f'<text x="{cx[2]}" y="116" fill="{TEXT}" font-size="13.5" text-anchor="middle">'
        f"Longest Streak</text>"
    )
    if longest and longest_end:
        begin = longest_end - timedelta(days=longest - 1)
        out.append(
            f'<text x="{cx[2]}" y="142" fill="{GOOD}" font-size="11.5" text-anchor="middle">'
            f"{esc(full_date(begin))} &#8211; {esc(full_date(longest_end))}</text>"
        )
    out.append("</svg>")
    return "".join(out)


# ---------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", default="dante329")
    ap.add_argument("--out", default="assets")
    ap.add_argument("--top", type=int, default=6, help="how many languages to show")
    args = ap.parse_args()

    login = args.login
    os.makedirs(args.out, exist_ok=True)
    today = datetime.now(timezone.utc).date()

    user = api(f"/users/{login}")
    created = date.fromisoformat(user["created_at"][:10])

    repos = [r for r in api_all(f"/users/{login}/repos?type=owner") if not r["fork"]]
    stars = sum(r["stargazers_count"] for r in repos)

    lang_bytes: dict[str, int] = {}
    lang_failures = 0
    for repo in repos:
        try:
            for name, size in api(f"/repos/{login}/{repo['name']}/languages").items():
                lang_bytes[name] = lang_bytes.get(name, 0) + int(size)
        except Exception as exc:  # noqa: BLE001
            lang_failures += 1
            print(f"[warn] languages for {repo['name']}: {exc}", file=sys.stderr)
    if lang_failures:
        # partial byte counts would silently reshuffle the ranking -> keep the old card
        print(
            f"[warn] {lang_failures} repo(s) without language data; keeping previous top-langs.svg",
            file=sys.stderr,
        )

    langs = sorted(lang_bytes.items(), key=lambda kv: kv[1], reverse=True)[: args.top]

    calendar = scrape_days(login, created.year)
    total = sum(calendar.values())
    current, current_end, longest, longest_end = streaks(calendar, today)

    prs = search_count(f"author:{login} type:pr")
    issues = search_count(f"author:{login} type:issue")

    stats_rows = [
        ("star", "Total Stars", stars),
        ("repo", "Public Repos", user["public_repos"]),
        ("pr", "Pull Requests", prs),
        ("issue", "Issues Opened", issues),
        ("people", "Followers", user["followers"]),
        ("graph", "Contributions", total),
    ]

    outputs = {
        "github-stats.svg": render_stats(login, stats_rows),
        "streak-stats.svg": render_streak(
            total, (created, today), current, current_end, longest, longest_end
        ),
    }
    if langs and not lang_failures:
        outputs["top-langs.svg"] = render_langs(langs)
    for name, svg in outputs.items():
        path = os.path.join(args.out, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(svg)
        print(f"wrote {path} ({len(svg)} bytes)")

    print(
        f"stats: stars={stars} repos={user['public_repos']} prs={prs} issues={issues} "
        f"followers={user['followers']} contributions={total} days={len(calendar)} "
        f"current={current} longest={longest}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
