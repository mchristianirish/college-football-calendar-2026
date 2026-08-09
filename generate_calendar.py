import os
import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

# ---------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------

CFBD_BASE_URL = "https://api.collegefootballdata.com"
OUTPUT_FILE = "Top25Football.ics"

# Calendar timezone
LOCAL_TZ = ZoneInfo("America/Chicago")

# We specifically want the AP Top 25
POLL_NAME = "AP Top 25"


# ---------------------------------------------------------
# API HELPERS
# ---------------------------------------------------------

def cfbd_get(endpoint, params=None):
    """
    Makes an authenticated request to CollegeFootballData.
    """
    api_key = os.environ.get("CFBD_API_KEY")

    if not api_key:
        raise RuntimeError(
            "CFBD_API_KEY environment variable is missing."
        )

    params = params or {}
    query_string = urllib.parse.urlencode(params)

    url = f"{CFBD_BASE_URL}{endpoint}"

    if query_string:
        url += f"?{query_string}"

    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "Top25FootballCalendar/1.0",
        },
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


# ---------------------------------------------------------
# GET LATEST AP TOP 25
# ---------------------------------------------------------

def get_latest_ap_poll(year):
    """
    Retrieves all rankings for the season and finds
    the most recent AP Top 25 poll.
    """

    rankings = cfbd_get(
        "/rankings",
        {
            "year": year,
        },
    )

    ap_polls = []

    for ranking_week in rankings:

        week = ranking_week.get("week")
        season_type = ranking_week.get(
            "seasonType",
            ranking_week.get("season_type", "regular")
        )

        polls = ranking_week.get("polls", [])

        for poll in polls:
            poll_name = poll.get("poll", "")

            # Be a little flexible in case CFBD uses
            # slightly different capitalization/naming.
            if "AP" in poll_name.upper():

                ranks = poll.get("ranks", [])

                if ranks:
                    ap_polls.append(
                        {
                            "week": week,
                            "season_type": season_type,
                            "ranks": ranks,
                            "poll_name": poll_name,
                        }
                    )

    if not ap_polls:
        raise RuntimeError(
            f"No AP poll was found for the {year} season."
        )

    # Sort by week and choose the latest poll.
    ap_polls.sort(
        key=lambda p: (
            p["week"] if p["week"] is not None else -1
        )
    )

    latest_poll = ap_polls[-1]

    top25 = {}

    for team in latest_poll["ranks"]:
        rank = team.get("rank")
        school = team.get("school")

        if rank and school and int(rank) <= 25:
            top25[school] = int(rank)

    print(
        f"Using {latest_poll['poll_name']} "
        f"Week {latest_poll['week']}"
    )

    print(f"Found {len(top25)} ranked teams.")

    return top25, latest_poll["week"]


# ---------------------------------------------------------
# DATE HANDLING
# ---------------------------------------------------------

def parse_game_datetime(value):
    """
    Converts CFBD's ISO date/time into a timezone-aware
    datetime object.
    """

    if not value:
        return None

    value = value.replace("Z", "+00:00")

    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt


def upcoming_week_window():
    """
    Creates a window covering the upcoming college
    football week.

    When run Sunday evening:
        Starts immediately
        Ends after the following Sunday

    The extra time allows Thursday/Friday/Saturday/
    Sunday games to all be captured.
    """

    now = datetime.now(LOCAL_TZ)

    start = now

    end = now + timedelta(days=8)

    return start, end


# ---------------------------------------------------------
# GET UPCOMING GAMES
# ---------------------------------------------------------

def get_ranked_matchups(year, top25):
    """
    Retrieves the season schedule and selects games:
      - occurring during the next 8 days
      - where BOTH teams are in the current AP Top 25
    """

    games = cfbd_get(
        "/games",
        {
            "year": year,
            "seasonType": "regular",
        },
    )

    window_start, window_end = upcoming_week_window()

    selected_games = []

    for game in games:

        home = game.get(
            "homeTeam",
            game.get("home_team")
        )

        away = game.get(
            "awayTeam",
            game.get("away_team")
        )

        start_date = game.get(
            "startDate",
            game.get("start_date")
        )

        game_time = parse_game_datetime(start_date)

        if not game_time:
            continue

        game_local = game_time.astimezone(LOCAL_TZ)

        if not (
            window_start <= game_local <= window_end
        ):
            continue

        if home not in top25 or away not in top25:
            continue

        selected_games.append(
            {
                "id": game.get("id"),
                "home": home,
                "away": away,
                "home_rank": top25[home],
                "away_rank": top25[away],
                "start": game_time,
                "venue": game.get("venue"),
                "week": game.get("week"),
            }
        )

    selected_games.sort(key=lambda g: g["start"])

    return selected_games


# ---------------------------------------------------------
# ICS HELPERS
# ---------------------------------------------------------

def ics_escape(text):
    """
    Escapes characters required by the iCalendar format.
    """

    if text is None:
        return ""

    text = str(text)

    return (
        text
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def format_utc(dt):
    """
    Formats a datetime for an ICS file in UTC.
    """

    return (
        dt.astimezone(timezone.utc)
        .strftime("%Y%m%dT%H%M%SZ")
    )


# ---------------------------------------------------------
# CREATE ICS FILE
# ---------------------------------------------------------

def create_calendar(games, poll_week):
    """
    Generates the actual Apple-compatible .ics file.
    """

    generated = datetime.now(timezone.utc)

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Top 25 College Football Calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:CFB Top 25 Matchups",
        "X-WR-TIMEZONE:America/Chicago",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
    ]

    for game in games:

        start = game["start"]

        # Allow 4 hours for each football game.
        end = start + timedelta(hours=4)

        away_rank = game["away_rank"]
        home_rank = game["home_rank"]

        away = game["away"]
        home = game["home"]

        # Fire emoji for Top-10-vs-Top-10.
        if away_rank <= 10 and home_rank <= 10:
            prefix = "🔥 "
        else:
            prefix = "🏈 "

        summary = (
            f"{prefix}#{away_rank} {away} "
            f"at #{home_rank} {home}"
        )

        local_start = start.astimezone(LOCAL_TZ)

        description_lines = [
            f"AP Top 25 matchup",
            f"AP Poll Week {poll_week}",
            "",
            f"#{away_rank} {away}",
            f"at",
            f"#{home_rank} {home}",
            "",
            local_start.strftime(
                "%A, %B %-d at %-I:%M %p Central"
            ),
        ]

        if game["venue"]:
            description_lines.extend(
                [
                    "",
                    f"Venue: {game['venue']}",
                ]
            )

        description = "\n".join(description_lines)

        # Stable unique ID keeps Apple Calendar from
        # treating the same game as a brand-new event.
        if game["id"]:
            uid = f"cfbd-{game['id']}@top25football"
        else:
            uid = (
                f"{away}-{home}-"
                f"{start.strftime('%Y%m%d')}@top25football"
            )

        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{ics_escape(uid)}",
                f"DTSTAMP:{format_utc(generated)}",
                f"DTSTART:{format_utc(start)}",
                f"DTEND:{format_utc(end)}",
                f"SUMMARY:{ics_escape(summary)}",
                f"DESCRIPTION:{ics_escape(description)}",
            ]
        )

        if game["venue"]:
            lines.append(
                f"LOCATION:{ics_escape(game['venue'])}"
            )

        lines.extend(
            [
                "STATUS:CONFIRMED",
                "TRANSP:TRANSPARENT",
                "END:VEVENT",
            ]
        )

    lines.append("END:VCALENDAR")

    Path(OUTPUT_FILE).write_text(
        "\r\n".join(lines) + "\r\n",
        encoding="utf-8",
    )

    print(
        f"Created {OUTPUT_FILE} "
        f"with {len(games)} game(s)."
    )


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

def main():

    now = datetime.now(LOCAL_TZ)

    year = now.year

    print(
        f"Generating Top 25 calendar for {year}..."
    )

    top25, poll_week = get_latest_ap_poll(year)

    print()
    print("Current AP Top 25:")

    for team, rank in sorted(
        top25.items(),
        key=lambda item: item[1],
    ):
        print(f"  #{rank} {team}")

    games = get_ranked_matchups(year, top25)

    print()
    print("Upcoming ranked-vs-ranked games:")

    if not games:
        print("  None found.")

    for game in games:
        local_time = game["start"].astimezone(LOCAL_TZ)

        print(
            f"  #{game['away_rank']} {game['away']} "
            f"at "
            f"#{game['home_rank']} {game['home']} "
            f"- "
            f"{local_time.strftime('%a %b %d %I:%M %p')}"
        )

    create_calendar(games, poll_week)


if __name__ == "__main__":
    main()
