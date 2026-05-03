"""
Collecte de données sportives
Football-Data.org (gratuit, fiable) + The Odds API (cotes 1xBet/Melbet)
"""
import httpx
import asyncio
from datetime import datetime, date, timedelta
from config import ODDS_API_KEY
import os
import logging

logger = logging.getLogger(__name__)

FOOTBALL_DATA_KEY = os.getenv("FOOTBALL_DATA_KEY", "")
FOOTBALL_DATA_BASE = "https://api.football-data.org/v4"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# Compétitions disponibles sur le plan gratuit Football-Data.org
COMPETITIONS = {
    "PL":  "Premier League",
    "PD":  "La Liga",
    "BL1": "Bundesliga",
    "SA":  "Serie A",
    "FL1": "Ligue 1",
    "CL":  "Champions League",
    "EC":  "Euro",
    "WC":  "Coupe du Monde",
}

ODDS_SPORT_KEYS = [
    "soccer_epl",
    "soccer_france_ligue_one",
    "soccer_uefa_champs_league",
]

# ══════════════════════════════════════════
#  CACHE SYSTÈME
# ══════════════════════════════════════════
_cache = {}
CACHE_DURATION_MINUTES = 30


def _get_cache(key: str, max_minutes: int = None):
    if max_minutes is None:
        max_minutes = CACHE_DURATION_MINUTES
    if key in _cache:
        cached_at, value = _cache[key]
        age_minutes = (datetime.now() - cached_at).total_seconds() / 60
        if age_minutes < max_minutes:
            logger.info(f"📦 Cache hit: {key} ({age_minutes:.0f}min)")
            return value
    return None


def _set_cache(key: str, value):
    _cache[key] = (datetime.now(), value)
    logger.info(f"💾 Cache set: {key}")


async def fetch(url, headers=None, params=None):
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            resp = await client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            return {}


# ══════════════════════════════════════════
#  MATCHS - Football-Data.org
# ══════════════════════════════════════════

async def get_matches_for_date(target_date: str) -> list:
    """Récupère les matchs pour une date donnée via Football-Data.org"""
    cache_key = f"matches_{target_date}"
    # Cache 15 minutes pour les matchs du jour
    cached = _get_cache(cache_key, max_minutes=15)
    if cached is not None:
        return cached

    headers = {"X-Auth-Token": FOOTBALL_DATA_KEY}
    matches = []

    for comp_code, comp_name in COMPETITIONS.items():
        try:
            data = await fetch(
                f"{FOOTBALL_DATA_BASE}/competitions/{comp_code}/matches",
                headers=headers,
                params={"dateFrom": target_date, "dateTo": target_date}
            )
            for m in data.get("matches", []):
                home = m.get("homeTeam", {})
                away = m.get("awayTeam", {})
                score = m.get("score", {})
                full_time = score.get("fullTime", {})
                status = m.get("status", "SCHEDULED")

                matches.append({
                    "match_id": str(m.get("id", "")),
                    "sport": "football",
                    "home_team": home.get("name", ""),
                    "away_team": away.get("name", ""),
                    "home_team_id": home.get("id", 0),
                    "away_team_id": away.get("id", 0),
                    "league": comp_name,
                    "league_code": comp_code,
                    "country": m.get("area", {}).get("name", ""),
                    "kickoff": m.get("utcDate", ""),
                    "status": status,
                    "home_score": full_time.get("home"),
                    "away_score": full_time.get("away"),
                    "is_popular": True,
                })
            await asyncio.sleep(0.5)  # Respecter rate limit
        except Exception as e:
            logger.error(f"Error fetching {comp_code}: {e}")

    logger.info(f"✅ {len(matches)} matchs récupérés pour {target_date}")
    _set_cache(cache_key, matches)
    return matches


async def get_team_recent_form(team_id: int, comp_code: str = "PL") -> dict:
    """Récupère la forme récente d'une équipe."""
    cache_key = f"form_{team_id}"
    cached = _get_cache(cache_key)
    if cached is not None:
        return cached

    headers = {"X-Auth-Token": FOOTBALL_DATA_KEY}
    data = await fetch(
        f"{FOOTBALL_DATA_BASE}/teams/{team_id}/matches",
        headers=headers,
        params={"status": "FINISHED", "limit": 5}
    )

    form = ""
    goals_for, goals_against = [], []

    for m in data.get("matches", []):
        home = m.get("homeTeam", {})
        away = m.get("awayTeam", {})
        score = m.get("score", {}).get("fullTime", {})
        is_home = home.get("id") == team_id

        if is_home:
            scored = score.get("home", 0) or 0
            conceded = score.get("away", 0) or 0
            winner = m.get("score", {}).get("winner", "")
            won = winner == "HOME_TEAM"
            draw = winner == "DRAW"
        else:
            scored = score.get("away", 0) or 0
            conceded = score.get("home", 0) or 0
            winner = m.get("score", {}).get("winner", "")
            won = winner == "AWAY_TEAM"
            draw = winner == "DRAW"

        goals_for.append(scored)
        goals_against.append(conceded)
        form += "V" if won else ("N" if draw else "D")

    result = {
        "form": form,
        "goals_for_avg": round(sum(goals_for)/len(goals_for), 2) if goals_for else 1.2,
        "goals_against_avg": round(sum(goals_against)/len(goals_against), 2) if goals_against else 1.2,
    }
    _set_cache(f"form_{team_id}", result)
    return result


async def get_head_to_head(team1_id: int, team2_id: int) -> list:
    """Récupère l'historique H2H."""
    cache_key = f"h2h_{team1_id}_{team2_id}"
    cached = _get_cache(cache_key, max_minutes=1440)  # Cache 24h
    if cached is not None:
        return cached

    headers = {"X-Auth-Token": FOOTBALL_DATA_KEY}
    data = await fetch(
        f"{FOOTBALL_DATA_BASE}/teams/{team1_id}/matches",
        headers=headers,
        params={"status": "FINISHED", "limit": 10}
    )

    h2h = []
    for m in data.get("matches", []):
        home_id = m.get("homeTeam", {}).get("id")
        away_id = m.get("awayTeam", {}).get("id")
        if team2_id in [home_id, away_id]:
            winner = m.get("score", {}).get("winner", "")
            h2h.append({
                "home_team_id": home_id,
                "home_winner": winner == "HOME_TEAM",
            })

    _set_cache(cache_key, h2h)
    return h2h


async def get_fixture_result(fixture_id: str) -> dict:
    """Récupère le résultat d'un match terminé."""
    headers = {"X-Auth-Token": FOOTBALL_DATA_KEY}
    data = await fetch(f"{FOOTBALL_DATA_BASE}/matches/{fixture_id}", headers=headers)

    if data:
        score = data.get("score", {})
        full_time = score.get("fullTime", {})
        winner = score.get("winner", "")
        return {
            "status": data.get("status", ""),
            "home_score": full_time.get("home", 0),
            "away_score": full_time.get("away", 0),
            "home_winner": winner == "HOME_TEAM",
        }
    return {}


# ══════════════════════════════════════════
#  COTES - The Odds API (1xBet/Melbet)
# ══════════════════════════════════════════

async def get_all_real_odds() -> list:
    cache_key = "all_odds"
    cached = _get_cache(cache_key, max_minutes=120)
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
            for team, odds_val in bm_data.get("h2h", {}).items():
                tl = team.lower()
                if tl in ev_home or ev_home in tl:
                    if odds_val > best["1"]["odds"]:
                        best["1"] = {"odds": odds_val, "bookmaker": bm_name}
                elif "draw" in tl:
                    if odds_val > best["X"]["odds"]:
                        best["X"] = {"odds": odds_val, "bookmaker": bm_name}
                elif tl in ev_away or ev_away in tl:
                    if odds_val > best["2"]["odds"]:
                        best["2"] = {"odds": odds_val, "bookmaker": bm_name}
            for key, odds_val in bm_data.get("totals", {}).items():
                if "Over" in key and "2.5" in key:
                    if odds_val > best["Over_2.5"]["odds"]:
                        best["Over_2.5"] = {"odds": odds_val, "bookmaker": bm_name}
                elif "Under" in key and "2.5" in key:
                    if odds_val > best["Under_2.5"]["odds"]:
                        best["Under_2.5"] = {"odds": odds_val, "bookmaker": bm_name}
    return best


async def get_full_match_data(match, all_odds):
    home_id = match.get("home_team_id")
    away_id = match.get("away_team_id")

    # Séquentiel pour respecter le rate limit (10 req/min)
    home_form = await get_team_recent_form(home_id)
    await asyncio.sleep(1)
    away_form = await get_team_recent_form(away_id)
    await asyncio.sleep(1)
    h2h = await get_head_to_head(home_id, away_id)
    await asyncio.sleep(1)

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


async def get_matches_today():
    tomorrow = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")

    today_matches = await get_matches_for_date(today)

    FINISHED = ["FINISHED", "AWARDED", "CANCELLED", "POSTPONED",
                "IN_PLAY", "PAUSED", "HALFTIME"]

    # Filtrer les matchs terminés ET les matchs dont l'heure est passée
    from datetime import datetime as dt, timezone
    now_utc = dt.now(timezone.utc)

    upcoming = []
    for m in today_matches:
        if m["status"] in FINISHED:
            continue
        try:
            kickoff = dt.fromisoformat(m["kickoff"].replace("Z", "+00:00"))
            if kickoff > now_utc:  # Seulement les matchs futurs
                upcoming.append(m)
        except Exception:
            if m["status"] not in FINISHED:
                upcoming.append(m)

    if len(upcoming) < 3:
        logger.info("⚠️ Peu de matchs à venir aujourd'hui, ajout de demain...")
        tomorrow_matches = await get_matches_for_date(tomorrow)
        tomorrow_upcoming = []
        for m in tomorrow_matches:
            if m["status"] not in FINISHED:
                tomorrow_upcoming.append(m)
        return upcoming + tomorrow_upcoming

    return upcoming


async def fetch_todays_data_with_odds():
    # Cache basé sur l'heure (se renouvelle toutes les 15 minutes)
    from datetime import datetime as _dt
    slot = _dt.now().minute // 15
    cache_key = f"today_full_{date.today()}_{_dt.now().hour}_{slot}"
    cached = _get_cache(cache_key, max_minutes=15)
    if cached is not None:
        return cached

    logger.info("📡 Récupération des données du jour...")
    matches, all_odds = await asyncio.gather(get_matches_today(), get_all_real_odds())

    to_enrich = matches[:3]  # Max 3 pour respecter rate limit
    enriched = []
    for match in to_enrich:
        try:
            full = await get_full_match_data(match, all_odds)
            enriched.append(full)
            await asyncio.sleep(6)  # 10 req/min = 1 req/6sec
        except Exception as e:
            logger.error(f"Error enriching {match['match_id']}: {e}")
            enriched.append({**match, "odds": {}, "has_real_odds": False,
                             "home_stats": {}, "away_stats": {}, "h2h": [],
                             "home_injuries": [], "away_injuries": []})

    logger.info(f"✅ {len(enriched)} matchs prêts")
    result = {"matches": enriched, "all_odds": all_odds,
              "fetched_at": datetime.now().isoformat()}
    _set_cache(cache_key, result)
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
