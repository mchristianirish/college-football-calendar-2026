import os
import json
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path


# =========================================================
# SETTINGS
# =========================================================

CFBD_BASE_URL = "https://api.collegefootballdata.com"

OUTPUT_FILE = "Top25Football.ics"

# Your local timezone
LOCAL_TZ = ZoneInfo("America/Chicago")

# How many days ahead to look for games.
# Running Sunday evening means this captures the upcoming
# Thursday/Friday/Saturday/Sunday games.
DAYS_AHEAD = 8


# =========================================================
# COLLEGE FOOTBALL DATA API
# =========================================================

def cfbd_get(endpoint, params=None):
    """
    Make an authenticated request to CollegeFootballData.
    """

    api_key = os.environ.get("CFBD_API_KEY")

    if not api_key:
        raise RuntimeError(
            "CFBD_API_KEY is missing. "
            "Make sure it exists under GitHub "
            "Settings > Secrets and variables > Actions."
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

    try:
        with urllib.request.urlopen(
            request,
            timeout=30
        ) as response:

            data = response.read().decode("utf-8")

            return json.loads(data)

    except urllib.error.HTTPError as error:

        error_body = error.read().decode(
            "utf-8",
            errors="replace"
        )

        raise RuntimeError(
            f"CFBD API returned HTTP {error.code}.\n"
            f"Endpoint: {endpoint}\n"
            f"Response: {error_body}"
        )

    except urllib.error.URLError as error:

        raise RuntimeError(
            f"Could not connect to CFBD API: "
            f"{error.reason}"
        )


# =========================================================
# FIND THE LATEST AP TOP 25
# =========================================================

def get_latest_ap_poll(year):
    """
    Retrieve the season rankings and find the most recent
    AP Top 25 poll.

    If no AP poll exists yet, return an empty dictionary
    instead of stopping the workflow.
    """

    print()
    print("------------------------------------------")
    print("CHECKING AP TOP 25")
    print("------------------------------------------")

    rankings = cfbd_get(
        "/rankings",
        {
            "year": year
        }
    )

    if not rankings:

        print(
            f"No rankings of any kind are currently "
            f"available for {year}."
        )

        return {}, None

    ap_polls = []

    for ranking_week in rankings:

        week = ranking_week.get("week")

        season_type = (
            ranking_week.get("seasonType")
            or ranking_week.get("season_type")
            or "regular"
        )

        polls = ranking_week.get("polls", [])

        for poll in polls:

            poll_name = str(
                poll.get("poll", "")
            ).strip()

            poll_name_upper = poll_name.upper()

            # Match names such as:
            # "AP Top 25"
            # "AP"
            # "Associated Press"
            is_ap_poll = (
                poll_name_upper == "AP"
                or "AP TOP" in poll_name_upper
                or "ASSOCIATED PRESS" in poll_name_upper
            )

            if not is_ap_poll:
                continue

            ranks = poll.get("ranks", [])

            if not ranks:
                continue

            ap_polls.append(
                {
                    "week": week,
                    "season_type": season_type,
                    "poll_name": poll_name,
                    "ranks": ranks,
                }
            )

    # -----------------------------------------------------
    # IMPORTANT PRESEASON FIX
    # -----------------------------------------------------

    if not ap_polls:

        print()
        print(
            f"No AP Top 25 poll is currently available "
            f"through CollegeFootballData for {year}."
        )

        print()
        print(
            "This is NOT treated as an error."
        )

        print(
            "An empty calendar will be generated now."
        )

        print(
            "The workflow will check again the next time "
            "it runs."
        )

        return {}, None

    # Find most recent poll.

    def week_sort_value(poll):

        week = poll.get("week")

        if week is None:
            return -1

        try:
            return int(week)
        except (TypeError, ValueError):
            return -1

    ap_polls.sort(
        key=week_sort_value
    )

    latest_poll = ap_polls[-1]

    top25 = {}

    for ranked_team in latest_poll["ranks"]:

        rank = ranked_team.get("rank")

        school = ranked_team.get("school")

        if rank is None or not school:
            continue

        try:
            rank_number = int(rank)
        except (TypeError, ValueError):
            continue

        if 1 <= rank_number <= 25:

            top25[school.strip()] = rank_number

    print()
    print(
        f"Using: {latest_poll['poll_name']}"
    )

    print(
        f"Poll week: {latest_poll['week']}"
    )

    print(
        f"Ranked teams found: {len(top25)}"
    )

    print()

    for school, rank in sorted(
        top25.items(),
        key=lambda item: item[1]
    ):

        print(
            f"#{rank:>2} {school}"
        )

    return top25, latest_poll["week"]


# =========================================================
# DATE AND TIME
# =========================================================

def parse_game_datetime(value):
    """
    Convert CFBD's ISO game date into a timezone-aware
    Python datetime.
    """

    if not value:
        return None

    value = str(value).strip()

    # Convert the common trailing Z into an explicit
    # UTC offset that Python can parse.
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    try:

        dt = datetime.fromisoformat(value)

    except ValueError:

        print(
            f"WARNING: Could not parse game time: {value}"
        )

        return None

    if dt.tzinfo is None:

        dt = dt.replace(
            tzinfo=timezone.utc
        )

    return dt


def upcoming_window():
    """
    Establish the date/time window for games that should
    appear on the calendar.
    """

    now = datetime.now(LOCAL_TZ)

    start = now

    end = now + timedelta(days=DAYS_AHEAD)

    return start, end


# =========================================================
# MATCH TEAM NAMES
# =========================================================

def normalize_team_name(name):
    """
    Normalize team names enough to reduce matching problems.
    """

    if not name:
        return ""

    return (
        str(name)
        .strip()
        .lower()
        .replace("&", "and")
        .replace(".", "")
        .replace("'", "")
    )


def build_rank_lookup(top25):
    """
    Build a normalized lookup table.

    Returns:
        normalized name -> (official name, rank)
    """

    lookup = {}

    for school, rank in top25.items():

        lookup[
            normalize_team_name(school)
        ] = (
            school,
            rank
        )

    return lookup


# =========================================================
# GET UPCOMING TOP-25-vs-TOP-25 GAMES
# =========================================================

def get_ranked_matchups(year, top25):
    """
    Find upcoming games in which BOTH teams appear in
    the latest AP Top 25.
    """

    print()
    print("------------------------------------------")
    print("CHECKING UPCOMING GAMES")
    print("------------------------------------------")

    games = cfbd_get(
        "/games",
        {
            "year": year,
            "seasonType": "regular"
        }
    )

    if not games:

        print(
            f"No regular-season games were returned "
            f"for {year}."
        )

        return []

    rank_lookup = build_rank_lookup(top25)

    window_start, window_end = upcoming_window()

    print()
    print(
        "Looking for games between:"
    )

    print(
        window_start.strftime(
            "%A, %B %d %Y %I:%M %p"
        )
    )

    print("and")

    print(
        window_end.strftime(
            "%A, %B %d %Y %I:%M %p"
        )
    )

    print()

    selected_games = []

    for game in games:

        home = (
            game.get("homeTeam")
            or game.get("home_team")
        )

        away = (
            game.get("awayTeam")
            or game.get("away_team")
        )

        start_date = (
            game.get("startDate")
            or game.get("start_date")
        )

        game_time = parse_game_datetime(
            start_date
        )

        if not game_time:
            continue

        game_local = game_time.astimezone(
            LOCAL_TZ
        )

        if not (
            window_start
            <= game_local
            <= window_end
        ):
            continue

        normalized_home = normalize_team_name(
            home
        )

        normalized_away = normalize_team_name(
            away
        )

        home_info = rank_lookup.get(
            normalized_home
        )

        away_info = rank_lookup.get(
            normalized_away
        )

        # BOTH teams must be ranked.
        if not home_info or not away_info:
            continue

        home_official, home_rank = home_info

        away_official, away_rank = away_info

        venue = game.get("venue")

        game_id = game.get("id")

        week = game.get("week")

        selected_games.append(
            {
                "id": game_id,
                "home": home_official,
                "away": away_official,
                "home_rank": home_rank,
                "away_rank": away_rank,
                "start": game_time,
                "venue": venue,
                "week": week,
            }
        )

    selected_games.sort(
        key=lambda game: game["start"]
    )

    if not selected_games:

        print(
            "No AP Top-25-vs-Top-25 games "
            "were found in the upcoming window."
        )

    else:

        print(
            f"Found {len(selected_games)} "
            f"Top-25 matchup(s):"
        )

        print()

        for game in selected_games:

            game_local = (
                game["start"]
                .astimezone(LOCAL_TZ)
            )

            print(
                f"#{game['away_rank']} "
                f"{game['away']} at "
                f"#{game['home_rank']} "
                f"{game['home']}"
            )

            print(
                "   "
                + game_local.strftime(
                    "%A %B %d, %I:%M %p Central"
                )
            )

    return selected_games


# =========================================================
# ICS CALENDAR HELPERS
# =========================================================

def ics_escape(value):
    """
    Escape characters that have special meaning inside
    an iCalendar file.
    """

    if value is None:
        return ""

    text = str(value)

    text = text.replace(
        "\\",
        "\\\\"
    )

    text = text.replace(
        ";",
        "\\;"
    )

    text = text.replace(
        ",",
        "\\,"
    )

    text = text.replace(
        "\r\n",
        "\\n"
    )

    text = text.replace(
        "\n",
        "\\n"
    )

    return text


def format_utc(dt):
    """
    Format a datetime using the UTC format expected
    by iCalendar.
    """

    return (
        dt
        .astimezone(timezone.utc)
        .strftime("%Y%m%dT%H%M%SZ")
    )


def make_uid(game):
    """
    Generate a stable event ID so Apple Calendar recognizes
    an updated game as the same event.
    """

    if game.get("id"):

        return (
            f"cfbd-{game['id']}"
            "@top25football-calendar"
        )

    start = game["start"].strftime(
        "%Y%m%d"
    )

    away = normalize_team_name(
        game["away"]
    ).replace(" ", "-")

    home = normalize_team_name(
        game["home"]
    ).replace(" ", "-")

    return (
        f"{away}-{home}-{start}"
        "@top25football-calendar"
    )


# =========================================================
# CREATE THE APPLE CALENDAR
# =========================================================

def create_calendar(games, poll_week):
    """
    Generate Top25Football.ics.
    """

    print()
    print("------------------------------------------")
    print("CREATING APPLE CALENDAR")
    print("------------------------------------------")

    generated = datetime.now(
        timezone.utc
    )

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        (
            "PRODID:-//Top 25 College Football "
            "Calendar//EN"
        ),
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:CFB Top 25 Matchups",
        "X-WR-TIMEZONE:America/Chicago",
        (
            "REFRESH-INTERVAL;"
            "VALUE=DURATION:PT6H"
        ),
        "X-PUBLISHED-TTL:PT6H",
    ]

    for game in games:

        start = game["start"]

        # Four-hour event window.
        end = start + timedelta(
            hours=4
        )

        home = game["home"]

        away = game["away"]

        home_rank = game["home_rank"]

        away_rank = game["away_rank"]

        # Top-10-vs-Top-10 gets a fire icon.
        if (
            home_rank <= 10
            and away_rank <= 10
        ):

            icon = "🔥"

        else:

            icon = "🏈"

        summary = (
            f"{icon} "
            f"#{away_rank} {away} "
            f"at "
            f"#{home_rank} {home}"
        )

        local_start = (
            start
            .astimezone(LOCAL_TZ)
        )

        description_parts = [
            "AP Top 25 matchup"
        ]

        if poll_week is not None:

            description_parts.append(
                f"AP Poll Week {poll_week}"
            )

        description_parts.extend(
            [
                "",
                (
                    f"#{away_rank} "
                    f"{away}"
                ),
                "at",
                (
                    f"#{home_rank} "
                    f"{home}"
                ),
                "",
                local_start.strftime(
                    "%A, %B %d, %Y "
                    "at %I:%M %p Central"
                ),
            ]
        )

        if game.get("venue"):

            description_parts.extend(
                [
                    "",
                    (
                        "Venue: "
                        f"{game['venue']}"
                    ),
                ]
            )

        description = "\n".join(
            description_parts
        )

        uid = make_uid(game)

        lines.append(
            "BEGIN:VEVENT"
        )

        lines.append(
            f"UID:{ics_escape(uid)}"
        )

        lines.append(
            f"DTSTAMP:{format_utc(generated)}"
        )

        lines.append(
            f"DTSTART:{format_utc(start)}"
        )

        lines.append(
            f"DTEND:{format_utc(end)}"
        )

        lines.append(
            f"SUMMARY:{ics_escape(summary)}"
        )

        lines.append(
            "DESCRIPTION:"
            f"{ics_escape(description)}"
        )

        if game.get("venue"):

            lines.append(
                "LOCATION:"
                f"{ics_escape(game['venue'])}"
            )

        lines.append(
            "STATUS:CONFIRMED"
        )

        # This prevents football games from making
        # you appear "busy" on calendars that honor
        # transparency.
        lines.append(
            "TRANSP:TRANSPARENT"
        )

        lines.append(
            "END:VEVENT"
        )

    lines.append(
        "END:VCALENDAR"
    )

    calendar_text = (
        "\r\n".join(lines)
        + "\r\n"
    )

    Path(OUTPUT_FILE).write_text(
        calendar_text,
        encoding="utf-8"
    )

    print()
    print(
        f"SUCCESS: {OUTPUT_FILE} created."
    )

    print(
        f"Calendar contains "
        f"{len(games)} game(s)."
    )


# =========================================================
# MAIN PROGRAM
# =========================================================

def main():

    print()
    print("==========================================")
    print(" CFB TOP 25 CALENDAR GENERATOR")
    print("==========================================")

    now = datetime.now(
        LOCAL_TZ
    )

    year = now.year

    print()
    print(
        f"Season: {year}"
    )

    print(
        "Timezone: America/Chicago"
    )

    # -----------------------------------------------------
    # STEP 1: AP POLL
    # -----------------------------------------------------

    top25, poll_week = (
        get_latest_ap_poll(year)
    )

    # -----------------------------------------------------
    # PRESEASON / NO POLL CASE
    # -----------------------------------------------------

    if not top25:

        print()
        print("------------------------------------------")
        print("NO AP POLL AVAILABLE YET")
        print("------------------------------------------")

        print()
        print(
            "Creating a valid empty calendar."
        )

        print(
            "This allows the GitHub workflow "
            "to finish successfully."
        )

        print(
            "When an AP Top 25 becomes available, "
            "the next scheduled run will "
            "automatically populate the calendar."
        )

        create_calendar(
            [],
            poll_week
        )

        print()
        print("==========================================")
        print(" WORKFLOW COMPLETED SUCCESSFULLY")
        print("==========================================")

        return

    # -----------------------------------------------------
    # STEP 2: UPCOMING GAMES
    # -----------------------------------------------------

    games = get_ranked_matchups(
        year,
        top25
    )

    # -----------------------------------------------------
    # STEP 3: CREATE ICS
    # -----------------------------------------------------

    create_calendar(
        games,
        poll_week
    )

    print()
    print("==========================================")
    print(" WORKFLOW COMPLETED SUCCESSFULLY")
    print("==========================================")

    print()

    if games:

        print(
            "Your Top 25 football calendar "
            "has been updated."
        )

    else:

        print(
            "The AP poll was found, but there "
            "are no Top-25-vs-Top-25 games "
            "during the upcoming calendar window."
        )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()
