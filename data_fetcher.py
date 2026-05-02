"""
Collecte de données sportives via APIs
Données RÉELLES - 1xBet et Melbet Côte d'Ivoire
"""
import httpx
import asyncio
from datetime import datetime, date
from config import API_FOOTBALL_KEY, ODDS_API_KEY
import logging

logger = logging.getLogger(__name__)

API_FOOTBALL_BASE = "https://v3.football.api-sports.io"

# ══════════════════════════════════════════
#  CACHE SYSTÈME - Évite les requêtes répétées
# ══════════════════════════════════════════
_cache = {}
CACHE_DURATION_MINUTES = 30  # Cache valide 30 minutes


def _get_cache(key: str):
    """Retourne la valeur du cache si encore valide."""
    if key in _cache:
        cached_at, value = _cache[key]
        age_minutes = (datetime.now() - cached_at).total_seconds() / 60
        if age_minutes < CACHE_DURATION_MINUTES:
            logger.info(f"📦 Cache hit: {key} ({age_minutes:.0f}min)")
            return value
    return None


def _set_cache(key: str, value):
    """Stocke une valeur dans le cache."""
    _cache[key] = (datetime.now(), value)
    logger.info(f"💾 Cache set: {key}")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

TARGET_BOOKMAKERS = ["onexbet", "melbet"]

POPULAR_LEAGUES = {
    39: "Premier League", 140: "La Liga", 78: "Bundesliga",
    135: "Serie A", 61: "Ligue 1", 2: "Champions League",
    3: "Europa League", 12: "CAF Champions League", 182: "Ligue 1 CI",
}

ODDS_SPORT_KEYS = [
    "soccer_epl", "soccer_spain_la_liga", "soccer_germany_bundesliga",
    "soccer_italy_serie_a", "soccer_france_ligue_one",
    "soccer_uefa_champs_league", "basketball_nba", "mma_mixed_martial_arts",
]


async def fetch(url, headers=None, params=None):
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            resp = await client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            return {}


async def get_matches_for_date(target_date: str) -> list:
    """Récupère les matchs pour une date donnée (avec cache 30min)."""
    cache_key = f"matches_{target_date}"
    cached = _get_cache(cache_key)
    if cached is not None:
        return cached

    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    data = await fetch(f"{API_FOOTBALL_BASE}/fixtures", headers=headers,
                       params={"date": target_date, "timezone": "Africa/Abidjan"})
    matches = []
    for fixture in data.get("response", []):
        f = fixture["fixture"]
        teams = fixture["teams"]
        league = fixture["league"]
        goals = fixture.get("goals", {})
        matches.append({
            "match_id": str(f["id"]),
            "sport": "football",
            "home_team": teams["home"]["name"],
            "away_team": teams["away"]["name"],
            "home_team_id": teams["home"]["id"],
            "away_team_id": teams["away"]["id"],
            "league": league["name"],
            "league_id": league.get("id", 0),
            "country": league.get("country", ""),
            "kickoff": f["date"],
            "status": f["status"]["short"],
            "home_score": goals.get("home"),
            "away_score": goals.get("away"),
            "is_popular": league.get("id", 0) in POPULAR_LEAGUES,
        })
    return matches


async def get_matches_today():
    """
    Récupère les matchs disponibles pour parier.
    Si peu de matchs dispo aujourd'hui (soir), prend aussi demain.
    """
    today = date.today().strftime("%Y-%m-%d")
    tomorrow = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")

    today_matches = await get_matches_for_date(today)

    # Compter les matchs pas encore terminés
    FINISHED = ["FT", "AET", "PEN", "AWD", "WO", "CANC", "ABD", "INT"]
    available_today = [m for m in today_matches if m["status"] not in FINISHED]

    all_matches = today_matches

    # Si moins de 3 matchs disponibles aujourd'hui → ajouter demain
    if len(available_today) < 3:
        logger.info("⚠️ Peu de matchs aujourd'hui, ajout des matchs de demain...")
        tomorrow_matches = await get_matches_for_date(tomorrow)
        all_matches = today_matches + tomorrow_matches

    logger.info(f"✅ {len(all_matches)} matchs récupérés (aujourd'hui + demain si nécessaire)")
    return all_matches


async def get_team_recent_form(team_id, last=5):
    cache_key = f"form_{team_id}_{last}"
    cached = _get_cache(cache_key)
    if cached is not None:
        return cached

    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    data = await fetch(f"{API_FOOTBALL_BASE}/fixtures", headers=headers,
                       params={"team": team_id, "last": last, "status": "FT"})
    form = ""
    goals_for, goals_against = [], []
    for fixture in data.get("response", []):
        teams = fixture["teams"]
        goals = fixture["goals"]
        is_home = teams["home"]["id"] == team_id
        if is_home:
            scored = goals.get("home", 0) or 0
            conceded = goals.get("away", 0) or 0
            won = teams["home"]["winner"]
        else:
            scored = goals.get("away", 0) or 0
            conceded = goals.get("home", 0) or 0
            won = teams["away"]["winner"]
        goals_for.append(scored)
        goals_against.append(conceded)
        form += "V" if won is True else ("D" if won is False else "N")
    result = {
        "form": form,
        "goals_for_avg": round(sum(goals_for)/len(goals_for), 2) if goals_for else 1.2,
        "goals_against_avg": round(sum(goals_against)/len(goals_against), 2) if goals_against else 1.2,
    }
    _set_cache(f"form_{team_id}_{last}", result)
    return result


async def get_head_to_head(team1_id, team2_id, last=5):
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    data = await fetch(f"{API_FOOTBALL_BASE}/fixtures/headtohead", headers=headers,
                       params={"h2h": f"{team1_id}-{team2_id}", "last": last, "status": "FT"})
    h2h = []
    for fixture in data.get("response", []):
        teams = fixture["teams"]
        h2h.append({
            "home_team_id": teams["home"]["id"],
            "home_winner": teams["home"]["winner"],
        })
    return h2h


async def get_fixture_result(fixture_id):
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    data = await fetch(f"{API_FOOTBALL_BASE}/fixtures", headers=headers,
                       params={"id": fixture_id})
    resp = data.get("response", [])
    if resp:
        fixture = resp[0]
        return {
            "status": fixture["fixture"]["status"]["short"],
            "home_score": fixture["goals"].get("home", 0),
            "away_score": fixture["goals"].get("away", 0),
            "home_winner": fixture["teams"]["home"]["winner"],
        }
    return {}


async def get_all_real_odds():
    cache_key = "all_odds"
    cached = _get_cache(cache_key)
    if cached is not None:
        return cached

    all_odds = []
    for sport_key in ODDS_SPORT_KEYS:
        params = {
            "apiKey": ODDS_API_KEY,
            "regions": "eu",
            "markets": "h2h,totals",
            "oddsFormat": "decimal",
            "bookmakers": "onexbet,melbet",
        }
        data = await fetch(f"{ODDS_API_BASE}/sports/{sport_key}/odds", params=params)
        if isinstance(data, list):
            for event in data:
                match_odds = {
                    "home_team": event.get("home_team", ""),
                    "away_team": event.get("away_team", ""),
                    "kickoff": event.get("commence_time", ""),
                    "sport": sport_key,
                    "bookmakers": {}
                }
                for bm in event.get("bookmakers", []):
                    bm_name = "1xBet" if bm["key"] == "onexbet" else "Melbet"
                    match_odds["bookmakers"][bm_name] = {"h2h": {}, "totals": {}}
                    for market in bm.get("markets", []):
                        if market["key"] == "h2h":
                            for o in market["outcomes"]:
                                match_odds["bookmakers"][bm_name]["h2h"][o["name"]] = o["price"]
                        elif market["key"] == "totals":
                            for o in market["outcomes"]:
                                k = f"{o['name']}_{o.get('point', 2.5)}"
                                match_odds["bookmakers"][bm_name]["totals"][k] = o["price"]
                all_odds.append(match_odds)
        await asyncio.sleep(0.5)
    logger.info(f"✅ {len(all_odds)} événements avec cotes 1xBet/Melbet")
    _set_cache("all_odds", all_odds)
    return all_odds


def find_best_odds(odds_data, home_team, away_team):
    best = {
        "1": {"odds": 0, "bookmaker": None},
        "X": {"odds": 0, "bookmaker": None},
        "2": {"odds": 0, "bookmaker": None},
        "Over_2.5": {"odds": 0, "bookmaker": None},
        "Under_2.5": {"odds": 0, "bookmaker": None},
    }
    home_l = home_team.lower().strip()
    away_l = away_team.lower().strip()

    for event in odds_data:
        ev_home = event.get("home_team", "").lower().strip()
        ev_away = event.get("away_team", "").lower().strip()
        if not (home_l in ev_home or ev_home in home_l or
                away_l in ev_away or ev_away in away_l):
            continue
        for bm_name, bm_data in event.get("bookmakers", {}).items():
            h2h = bm_data.get("h2h", {})
            for team_name, odds in h2h.items():
                tl = team_name.lower()
                if tl in ev_home or ev_home in tl:
                    if odds > best["1"]["odds"]:
                        best["1"] = {"odds": odds, "bookmaker": bm_name}
                elif "draw" in tl:
                    if odds > best["X"]["odds"]:
                        best["X"] = {"odds": odds, "bookmaker": bm_name}
                elif tl in ev_away or ev_away in tl:
                    if odds > best["2"]["odds"]:
                        best["2"] = {"odds": odds, "bookmaker": bm_name}
            for key, odds in bm_data.get("totals", {}).items():
                if "Over" in key and "2.5" in key:
                    if odds > best["Over_2.5"]["odds"]:
                        best["Over_2.5"] = {"odds": odds, "bookmaker": bm_name}
                elif "Under" in key and "2.5" in key:
                    if odds > best["Under_2.5"]["odds"]:
                        best["Under_2.5"] = {"odds": odds, "bookmaker": bm_name}
    return best


async def get_full_match_data(match, all_odds):
    home_id = match.get("home_team_id")
    away_id = match.get("away_team_id")
    home_form, away_form, h2h = await asyncio.gather(
        get_team_recent_form(home_id),
        get_team_recent_form(away_id),
        get_head_to_head(home_id, away_id),
    )
    real_odds = find_best_odds(all_odds, match["home_team"], match["away_team"])

    odds_map = {}
    if real_odds["1"]["odds"] > 0:
        odds_map["1"] = real_odds["1"]["odds"]
        odds_map["1_bookmaker"] = real_odds["1"]["bookmaker"]
    if real_odds["X"]["odds"] > 0:
        odds_map["X"] = real_odds["X"]["odds"]
    if real_odds["2"]["odds"] > 0:
        odds_map["2"] = real_odds["2"]["odds"]
        odds_map["2_bookmaker"] = real_odds["2"]["bookmaker"]
    if real_odds["Over_2.5"]["odds"] > 0:
        odds_map["Over 2.5"] = real_odds["Over_2.5"]["odds"]
    if real_odds["Under_2.5"]["odds"] > 0:
        odds_map["Under 2.5"] = real_odds["Under_2.5"]["odds"]
    if odds_map.get("1") and odds_map.get("X"):
        odds_map["1X"] = round((odds_map["1"] + odds_map["X"]) / 2 * 0.85, 2)
    if odds_map.get("2") and odds_map.get("X"):
        odds_map["X2"] = round((odds_map["2"] + odds_map["X"]) / 2 * 0.85, 2)

    return {
        **match,
        "home_stats": home_form,
        "away_stats": away_form,
        "h2h": h2h,
        "home_injuries": [],
        "away_injuries": [],
        "odds": odds_map,
        "has_real_odds": len(odds_map) > 0,
    }


async def fetch_todays_data_with_odds():
    # Cache global de 30 minutes pour toute la page
    cache_key = f"today_full_{date.today()}"
    cached = _get_cache(cache_key)
    if cached is not None:
        return cached

    logger.info("📡 Récupération des données du jour...")
    matches, all_odds = await asyncio.gather(get_matches_today(), get_all_real_odds())

    # Limiter à 5 matchs max pour économiser les requêtes API
    # (100 req/jour gratuit = ~5 matchs avec H2H + forme)
    popular = [m for m in matches if m.get("is_popular")][:4]
    other = [m for m in matches if not m.get("is_popular")][:1]
    to_enrich = popular + other

    enriched = []
    for match in to_enrich:
        try:
            full = await get_full_match_data(match, all_odds)
            enriched.append(full)
            await asyncio.sleep(0.3)
        except Exception as e:
            logger.error(f"Error enriching {match['match_id']}: {e}")
            enriched.append({**match, "odds": {}, "has_real_odds": False,
                             "home_stats": {}, "away_stats": {}, "h2h": [],
                             "home_injuries": [], "away_injuries": []})

    logger.info(f"✅ {len(enriched)} matchs prêts")
    result = {"matches": enriched, "all_odds": all_odds,
              "fetched_at": datetime.now().isoformat()}
    _set_cache(f"today_full_{date.today()}", result)
    return result


def calculate_implied_probability(odds):
    return round((1 / odds) * 100, 2) if odds > 1 else 0.0


def calculate_value_bet(our_prob, bookmaker_odds):
    if bookmaker_odds <= 1:
        return -1.0
    return round((our_prob / 100) * bookmaker_odds - 1, 3)


def format_kickoff(kickoff_str):
    try:
        dt = datetime.fromisoformat(kickoff_str.replace("Z", "+00:00"))
        return dt.strftime("%d/%m à %Hh%M")
    except Exception:
        return "Aujourd'hui"
