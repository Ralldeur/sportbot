"""
Collecte de données sportives via APIs
Supporte: API-Football, The Odds API, Sportradar
"""
import httpx
import asyncio
from datetime import datetime, date, timedelta
from config import API_FOOTBALL_KEY, ODDS_API_KEY, SPORTRADAR_KEY, BOOKMAKERS
import logging

logger = logging.getLogger(__name__)

API_FOOTBALL_BASE = "https://v3.football.api-sports.io"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
SPORTRADAR_BASE = "https://api.sportradar.com"


async def fetch(url: str, headers: dict = None, params: dict = None) -> dict:
    """Requête HTTP async avec gestion d'erreurs."""
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error(f"HTTP error {url}: {e}")
            return {}
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            return {}


# ══════════════════════════════════════════
#  FOOTBALL (via API-Football)
# ══════════════════════════════════════════

async def get_football_matches_today() -> list:
    """Récupère les matchs de football du jour."""
    today = date.today().strftime("%Y-%m-%d")
    headers = {
        "x-apisports-key": API_FOOTBALL_KEY,
        "x-rapidapi-host": "v3.football.api-sports.io"
    }
    data = await fetch(f"{API_FOOTBALL_BASE}/fixtures",
                       headers=headers,
                       params={"date": today, "timezone": "Africa/Abidjan"})

    matches = []
    for fixture in data.get("response", []):
        f = fixture["fixture"]
        teams = fixture["teams"]
        league = fixture["league"]
        goals = fixture.get("goals", {})
        score = fixture.get("score", {})

        matches.append({
            "match_id": str(f["id"]),
            "sport": "football",
            "home_team": teams["home"]["name"],
            "away_team": teams["away"]["name"],
            "league": league["name"],
            "country": league["country"],
            "kickoff": f["date"],
            "status": f["status"]["short"],
            "home_score": goals.get("home"),
            "away_score": goals.get("away"),
            "venue": f.get("venue", {}).get("name", "N/A"),
        })
    return matches


async def get_team_stats(team_id: int, league_id: int, season: int) -> dict:
    """Récupère les statistiques d'une équipe."""
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    data = await fetch(f"{API_FOOTBALL_BASE}/teams/statistics",
                       headers=headers,
                       params={"team": team_id, "league": league_id, "season": season})
    return data.get("response", {})


async def get_head_to_head(team1_id: int, team2_id: int, last: int = 10) -> list:
    """Récupère l'historique des confrontations directes."""
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    data = await fetch(f"{API_FOOTBALL_BASE}/fixtures/headtohead",
                       headers=headers,
                       params={"h2h": f"{team1_id}-{team2_id}", "last": last})
    return data.get("response", [])


async def get_injuries(team_id: int) -> list:
    """Récupère les blessés/suspendus d'une équipe."""
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    data = await fetch(f"{API_FOOTBALL_BASE}/injuries",
                       headers=headers,
                       params={"team": team_id})
    return data.get("response", [])


async def get_fixture_result(fixture_id: str) -> dict:
    """Récupère le résultat final d'un match."""
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    data = await fetch(f"{API_FOOTBALL_BASE}/fixtures",
                       headers=headers,
                       params={"id": fixture_id})
    resp = data.get("response", [])
    if resp:
        fixture = resp[0]
        return {
            "status": fixture["fixture"]["status"]["short"],
            "home_score": fixture["goals"]["home"],
            "away_score": fixture["goals"]["away"],
            "winner": fixture["teams"]["home"]["winner"],  # True=home, False=away, None=draw
        }
    return {}


# ══════════════════════════════════════════
#  COTES (via The Odds API - gratuit)
# ══════════════════════════════════════════

SPORT_KEYS = {
    "football": "soccer_epl",       # À adapter selon la ligue
    "basketball": "basketball_nba",
    "tennis": "tennis_atp_wimbledon",
    "mma": "mma_mixed_martial_arts",
    "american_football": "americanfootball_nfl",
}

async def get_odds(sport_key: str, regions: str = "eu") -> list:
    """Récupère les cotes des bookmakers."""
    data = await fetch(f"{ODDS_API_BASE}/sports/{sport_key}/odds",
                       params={
                           "apiKey": ODDS_API_KEY,
                           "regions": regions,
                           "markets": "h2h,totals",
                           "oddsFormat": "decimal",
                           "bookmakers": ",".join(BOOKMAKERS)
                       })
    
    odds_data = []
    for event in data if isinstance(data, list) else []:
        match_odds = {
            "match_id": event.get("id"),
            "home_team": event.get("home_team"),
            "away_team": event.get("away_team"),
            "commence_time": event.get("commence_time"),
            "bookmakers": {}
        }
        for bookmaker in event.get("bookmakers", []):
            bm_name = bookmaker["key"]
            match_odds["bookmakers"][bm_name] = {}
            for market in bookmaker.get("markets", []):
                if market["key"] == "h2h":
                    for outcome in market["outcomes"]:
                        match_odds["bookmakers"][bm_name][outcome["name"]] = outcome["price"]
        odds_data.append(match_odds)
    return odds_data


async def get_best_odds(match_id: str, selection: str, sport_key: str) -> dict:
    """Trouve la meilleure cote disponible pour une sélection."""
    all_odds = await get_odds(sport_key)
    best = {"bookmaker": None, "odds": 0}
    for event in all_odds:
        if event["match_id"] == match_id:
            for bm, outcomes in event["bookmakers"].items():
                if selection in outcomes and outcomes[selection] > best["odds"]:
                    best = {"bookmaker": bm, "odds": outcomes[selection]}
    return best


# ══════════════════════════════════════════
#  UTILITAIRES
# ══════════════════════════════════════════

def get_recent_form(results: list, n: int = 5) -> str:
    """Calcule la forme récente (ex: VVDVD)."""
    form = []
    for r in results[-n:]:
        if r.get("winner") is True:
            form.append("V")
        elif r.get("winner") is False:
            form.append("D")
        else:
            form.append("N")
    return "".join(form)


def calculate_implied_probability(odds: float) -> float:
    """Convertit une cote décimale en probabilité implicite (%)."""
    if odds <= 1:
        return 0.0
    return round((1 / odds) * 100, 2)


def calculate_value_bet(our_prob: float, bookmaker_odds: float) -> float:
    """Calcule la valeur d'un pari (value bet)."""
    implied_prob = calculate_implied_probability(bookmaker_odds)
    value = (our_prob / 100) * bookmaker_odds - 1
    return round(value, 3)


async def fetch_all_today_data() -> dict:
    """Récupère toutes les données du jour pour analyse."""
    football_matches = await get_football_matches_today()
    football_odds = await get_odds("soccer_epl")
    
    return {
        "football": {"matches": football_matches, "odds": football_odds},
        "fetched_at": datetime.now().isoformat()
    }
