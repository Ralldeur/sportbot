"""
Moteur de prédiction sportive
Logique statistique + scoring multi-facteurs
"""
import math
import random
from typing import Optional
from dataclasses import dataclass
from data_fetcher import calculate_implied_probability, calculate_value_bet
import logging

logger = logging.getLogger(__name__)


@dataclass
class Prediction:
    match_id: str
    sport: str
    home_team: str
    away_team: str
    home_win_prob: float      # % probabilité victoire domicile
    draw_prob: float          # % nul (football)
    away_win_prob: float      # % probabilité victoire extérieur
    predicted_score: str      # ex: "2-1"
    best_selection: str       # Meilleure sélection recommandée
    best_odds: float
    value_bet: float          # >0 = value bet
    confidence: float         # 0-100
    risk_level: str           # faible / moyen / élevé / tres_eleve
    justification: list       # Liste de raisons
    stake_pct: float          # % bankroll conseillé


# ══════════════════════════════════════════
#  MODÈLE STATISTIQUE PRINCIPAL
# ══════════════════════════════════════════

class PredictionEngine:

    def __init__(self):
        self.model_version = "1.2.0"

    def predict_football(self, match_data: dict) -> Prediction:
        """
        Prédit le résultat d'un match de football.
        
        Facteurs pris en compte:
        - Forme récente (5 derniers matchs)
        - Avantage domicile (historiquement +5-10%)
        - Blessures et suspensions
        - Historique H2H
        - Position au classement
        - Force offensive / défensive
        """
        home = match_data.get("home_stats", {})
        away = match_data.get("away_stats", {})
        h2h = match_data.get("h2h", [])
        home_injuries = match_data.get("home_injuries", [])
        away_injuries = match_data.get("away_injuries", [])

        # ── 1. SCORE DE FORME ──────────────────────
        home_form_score = self._form_score(home.get("form", ""))
        away_form_score = self._form_score(away.get("form", ""))

        # ── 2. AVANTAGE DOMICILE ──────────────────
        home_advantage = 8.0  # points de bonus domicile (%)

        # ── 3. FORCE OFFENSIVE / DÉFENSIVE ──────────
        home_attack = home.get("goals_for_avg", 1.3)
        home_defense = home.get("goals_against_avg", 1.2)
        away_attack = away.get("goals_for_avg", 1.1)
        away_defense = away.get("goals_against_avg", 1.4)

        # Modèle de Poisson pour les buts attendus
        home_xg = (home_attack + away_defense) / 2
        away_xg = (away_attack + home_defense) / 2

        # ── 4. PÉNALITÉS BLESSURES ───────────────
        key_players_home = sum(1 for p in home_injuries if p.get("is_key_player"))
        key_players_away = sum(1 for p in away_injuries if p.get("is_key_player"))
        home_injury_penalty = key_players_home * 2.5
        away_injury_penalty = key_players_away * 2.5

        # ── 5. HISTORIQUE H2H ─────────────────────
        h2h_bonus = self._h2h_analysis(h2h, match_data["home_team"])

        # ── 6. CALCUL FINAL DES PROBABILITÉS ──────
        home_raw = (home_form_score * 0.30 +
                    home_advantage +
                    (home_xg / (home_xg + away_xg)) * 40 +
                    h2h_bonus -
                    home_injury_penalty)

        away_raw = (away_form_score * 0.30 +
                    (away_xg / (home_xg + away_xg)) * 40 -
                    away_injury_penalty)

        draw_raw = max(15.0, 35.0 - abs(home_raw - away_raw))

        # Normalisation à 100%
        total = home_raw + away_raw + draw_raw
        home_prob = round(max(5, (home_raw / total) * 100), 1)
        away_prob = round(max(5, (away_raw / total) * 100), 1)
        draw_prob = round(max(5, 100 - home_prob - away_prob), 1)

        # ── 7. SCORE PRÉDIT (Poisson) ─────────────
        home_goals = round(home_xg)
        away_goals = round(away_xg)
        predicted_score = f"{home_goals}-{away_goals}"

        # ── 8. SÉLECTION RECOMMANDÉE ──────────────
        probs = {
            "1": home_prob,
            "X": draw_prob,
            "2": away_prob,
            "1X": home_prob + draw_prob,
            "X2": draw_prob + away_prob,
        }
        best_selection = max(probs, key=probs.get)
        best_prob = probs[best_selection]

        # ── 9. COTES ET VALUE BET ─────────────────
        odds_map = match_data.get("odds", {})
        best_odds = odds_map.get(best_selection, 1.5)
        value = calculate_value_bet(best_prob, best_odds)

        # ── 10. NIVEAU DE CONFIANCE ───────────────
        confidence = self._compute_confidence(best_prob, home_form_score,
                                               away_form_score, h2h)

        # ── 11. NIVEAU DE RISQUE ──────────────────
        risk_level = self._risk_level(best_odds, confidence)
        stake_pct = self._kelly_stake(best_prob / 100, best_odds)

        # ── 12. JUSTIFICATION ─────────────────────
        justification = self._build_justification(
            home=match_data["home_team"],
            away=match_data["away_team"],
            home_form=home.get("form", "N/A"),
            away_form=away.get("form", "N/A"),
            home_xg=home_xg,
            away_xg=away_xg,
            h2h_bonus=h2h_bonus,
            home_injuries=len(home_injuries),
            away_injuries=len(away_injuries),
            value=value
        )

        return Prediction(
            match_id=match_data["match_id"],
            sport="football",
            home_team=match_data["home_team"],
            away_team=match_data["away_team"],
            home_win_prob=home_prob,
            draw_prob=draw_prob,
            away_win_prob=away_prob,
            predicted_score=predicted_score,
            best_selection=best_selection,
            best_odds=best_odds,
            value_bet=value,
            confidence=confidence,
            risk_level=risk_level,
            justification=justification,
            stake_pct=stake_pct
        )

    def predict_basketball(self, match_data: dict) -> Prediction:
        """Prédiction basketball - basé sur points marqués/encaissés."""
        home_ppg = match_data.get("home_ppg", 105)   # Points par match
        away_ppg = match_data.get("away_ppg", 102)
        home_form = self._form_score(match_data.get("home_form", ""))
        away_form = self._form_score(match_data.get("away_form", ""))

        home_prob = 50 + (home_ppg - away_ppg) * 0.8 + (home_form - away_form) * 0.5 + 5
        home_prob = max(20, min(80, home_prob))
        away_prob = 100 - home_prob

        total_points = home_ppg + away_ppg
        over_under_line = match_data.get("ou_line", total_points)
        ou_selection = "Over" if total_points > over_under_line else "Under"
        best_prob = home_prob if home_prob > away_prob else away_prob
        best_selection = "1" if home_prob > away_prob else "2"
        best_odds = match_data.get("odds", {}).get(best_selection, 1.80)
        value = calculate_value_bet(best_prob, best_odds)

        return Prediction(
            match_id=match_data["match_id"],
            sport="basketball",
            home_team=match_data["home_team"],
            away_team=match_data["away_team"],
            home_win_prob=home_prob,
            draw_prob=0,
            away_win_prob=away_prob,
            predicted_score=f"{round(home_ppg)}-{round(away_ppg)}",
            best_selection=best_selection,
            best_odds=best_odds,
            value_bet=value,
            confidence=self._compute_confidence(best_prob, home_form, away_form, []),
            risk_level=self._risk_level(best_odds, best_prob),
            justification=[
                f"📊 Moy. points {match_data['home_team']}: {home_ppg:.1f}/match",
                f"📊 Moy. points {match_data['away_team']}: {away_ppg:.1f}/match",
                f"🏠 Avantage domicile estimé"
            ],
            stake_pct=self._kelly_stake(best_prob / 100, best_odds)
        )

    def predict_mma(self, match_data: dict) -> Prediction:
        """Prédiction MMA/UFC - basé sur stats de combat."""
        fighter1 = match_data.get("fighter1", {})
        fighter2 = match_data.get("fighter2", {})

        f1_wins = fighter1.get("wins", 10)
        f1_losses = fighter1.get("losses", 2)
        f2_wins = fighter2.get("wins", 8)
        f2_losses = fighter2.get("losses", 3)

        f1_win_rate = f1_wins / max(1, f1_wins + f1_losses) * 100
        f2_win_rate = f2_wins / max(1, f2_wins + f2_losses) * 100

        # Facteurs MMA: striking accuracy, grappling, reach, récence victoires
        f1_striking = fighter1.get("striking_accuracy", 45)
        f2_striking = fighter2.get("striking_accuracy", 43)

        f1_prob = (f1_win_rate * 0.5 + f1_striking * 0.3 + 20) / 1.2
        f1_prob = max(20, min(80, f1_prob))
        f2_prob = 100 - f1_prob

        best_selection = "1" if f1_prob > f2_prob else "2"
        best_prob = max(f1_prob, f2_prob)
        best_odds = match_data.get("odds", {}).get(best_selection, 1.90)

        return Prediction(
            match_id=match_data["match_id"],
            sport="mma",
            home_team=fighter1.get("name", "Fighter 1"),
            away_team=fighter2.get("name", "Fighter 2"),
            home_win_prob=f1_prob,
            draw_prob=0,
            away_win_prob=f2_prob,
            predicted_score="",
            best_selection=best_selection,
            best_odds=best_odds,
            value_bet=calculate_value_bet(best_prob, best_odds),
            confidence=min(70, best_prob * 0.85),
            risk_level=self._risk_level(best_odds, best_prob),
            justification=[
                f"🥊 {fighter1.get('name')}: {f1_wins}V/{f1_losses}D ({f1_win_rate:.0f}%)",
                f"🥊 {fighter2.get('name')}: {f2_wins}V/{f2_losses}D ({f2_win_rate:.0f}%)",
                f"📊 Striking accuracy: {f1_striking}% vs {f2_striking}%"
            ],
            stake_pct=self._kelly_stake(best_prob / 100, best_odds)
        )

    # ══════════════════════════════════════════
    #  CUSTOM ODDS - Générateur de combinés
    # ══════════════════════════════════════════

    def build_combo(self, target_odds: float, available_matches: list,
                    mode: str = "balanced") -> dict:
        """
        Construit un combiné pour atteindre une cote cible.
        
        mode: 'safe' | 'balanced' | 'aggressive'
        """
        # Déterminer le nombre de sélections selon la cote cible
        n_selections = self._estimate_selections_count(target_odds)

        # Filtrer et scorer les matchs disponibles
        scored = self._score_matches_for_combo(available_matches, mode)

        if len(scored) < n_selections:
            n_selections = len(scored)

        # Construire le combiné optimal
        selected = []
        combo_odds = 1.0
        attempts = 0

        for match in scored:
            if len(selected) >= n_selections:
                break
            if combo_odds * match["odds"] > target_odds * 1.3:
                continue  # Évite de trop dépasser la cote cible

            selected.append(match)
            combo_odds *= match["odds"]
            attempts += 1

        # Ajustement si cote insuffisante
        if combo_odds < target_odds * 0.7 and len(scored) > len(selected):
            remaining = [m for m in scored if m not in selected]
            for m in remaining:
                if combo_odds * m["odds"] <= target_odds * 1.2:
                    selected.append(m)
                    combo_odds *= m["odds"]
                    break

        # Calcul probabilité globale
        combo_prob = 1.0
        for s in selected:
            combo_prob *= (s["probability"] / 100)
        combo_prob *= 100

        risk_level = self._combo_risk_level(combo_odds, len(selected))
        stake_advice = self._combo_stake_advice(risk_level)

        return {
            "selections": selected,
            "total_odds": round(combo_odds, 2),
            "target_odds": target_odds,
            "probability": round(combo_prob, 2),
            "risk_level": risk_level,
            "stake_advice": stake_advice,
            "mode": mode,
            "n_matches": len(selected)
        }

    def _estimate_selections_count(self, target_odds: float) -> int:
        if target_odds <= 5:
            return 2
        elif target_odds <= 10:
            return 3
        elif target_odds <= 25:
            return 4
        elif target_odds <= 50:
            return 5
        elif target_odds <= 100:
            return 7
        else:
            return 10

    def _score_matches_for_combo(self, matches: list, mode: str) -> list:
        """Score et trie les matchs pour la construction du combiné."""
        scored = []
        for m in matches:
            prob = m.get("probability", 50)
            odds = m.get("odds", 1.5)

            if mode == "safe" and odds > 2.5:
                continue  # En mode safe, évite les grosses cotes
            if mode == "aggressive" and odds < 1.5:
                continue  # En mode agressif, évite les trop faibles cotes

            score = prob * 0.7 + (1 / odds) * 30
            scored.append({**m, "score": score})

        return sorted(scored, key=lambda x: x["score"], reverse=True)

    # ══════════════════════════════════════════
    #  MÉTHODES UTILITAIRES
    # ══════════════════════════════════════════

    def _form_score(self, form_str: str) -> float:
        """Convertit une chaîne de forme en score (0-100)."""
        if not form_str:
            return 50.0
        score = 0
        weights = [1.0, 0.9, 0.8, 0.7, 0.6]  # Matchs récents plus importants
        for i, result in enumerate(reversed(form_str[-5:])):
            w = weights[i] if i < len(weights) else 0.5
            if result == "V":
                score += 20 * w
            elif result == "N":
                score += 10 * w
        return min(100, score)

    def _h2h_analysis(self, h2h: list, home_team: str) -> float:
        """Analyse l'historique des confrontations directes."""
        if not h2h:
            return 0.0
        home_wins = sum(1 for m in h2h if m.get("winner") == home_team)
        return (home_wins / len(h2h)) * 15 - 5  # -5 à +10

    def _compute_confidence(self, prob: float, home_form: float,
                             away_form: float, h2h: list) -> float:
        """Calcule un niveau de confiance global (0-100)."""
        base = prob * 0.6
        form_factor = abs(home_form - away_form) * 0.3
        h2h_factor = len(h2h) * 0.5  # Plus de données H2H = plus de confiance
        confidence = base + form_factor + min(10, h2h_factor)
        return round(min(95, max(10, confidence)), 1)

    def _risk_level(self, odds: float, confidence: float) -> str:
        if odds <= 1.80 and confidence >= 65:
            return "faible"
        elif odds <= 2.50 and confidence >= 55:
            return "moyen"
        elif odds <= 5.00:
            return "élevé"
        else:
            return "tres_eleve"

    def _combo_risk_level(self, total_odds: float, n: int) -> str:
        if total_odds <= 5 and n <= 3:
            return "faible"
        elif total_odds <= 20 and n <= 5:
            return "moyen"
        elif total_odds <= 100:
            return "élevé"
        else:
            return "tres_eleve"

    def _kelly_stake(self, prob: float, odds: float, fraction: float = 0.25) -> float:
        """
        Critère de Kelly fractionné pour conseiller la mise.
        Fraction = 25% du Kelly complet (conservateur).
        """
        if odds <= 1:
            return 1.0
        kelly = (prob * odds - 1) / (odds - 1)
        stake = max(0, kelly * fraction * 100)
        return round(min(5, stake), 1)  # Max 5% de la bankroll

    def _combo_stake_advice(self, risk_level: str) -> float:
        stakes = {
            "faible": 3.0,
            "moyen": 2.0,
            "élevé": 1.0,
            "tres_eleve": 0.5
        }
        return stakes.get(risk_level, 1.0)

    def _build_justification(self, **kwargs) -> list:
        reasons = []
        if kwargs.get("home_form"):
            reasons.append(f"📈 Forme {kwargs.get('home', '')}: {kwargs['home_form']}")
        if kwargs.get("away_form"):
            reasons.append(f"📉 Forme {kwargs.get('away', '')}: {kwargs['away_form']}")
        if kwargs.get("home_xg") and kwargs.get("away_xg"):
            reasons.append(
                f"⚡ Buts attendus: {kwargs['home_xg']:.1f} vs {kwargs['away_xg']:.1f}")
        if kwargs.get("h2h_bonus", 0) > 2:
            reasons.append(f"🔄 Historique favorable en H2H (+{kwargs['h2h_bonus']:.0f}pts)")
        if kwargs.get("home_injuries", 0) > 0:
            reasons.append(f"🏥 {kwargs['home_injuries']} blessé(s) domicile")
        if kwargs.get("away_injuries", 0) > 0:
            reasons.append(f"🏥 {kwargs['away_injuries']} blessé(s) extérieur")
        if kwargs.get("value", 0) > 0.05:
            reasons.append(f"💰 VALUE BET détecté (+{kwargs['value']*100:.1f}%)")
        return reasons


# Instance singleton
engine = PredictionEngine()
