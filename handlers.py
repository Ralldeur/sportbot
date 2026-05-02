"""
Handlers Telegram - Commandes et interactions
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from database import register_user, save_bet, get_user_bets, get_user_stats
from prediction_engine import engine, Prediction
from data_fetcher import fetch_todays_data_with_odds, format_kickoff
from config import RISK_WARNING, RISK_LEVELS
import json
import logging

logger = logging.getLogger(__name__)

RISK_EMOJIS = {
    "faible": "🟢",
    "moyen": "🟡",
    "élevé": "🟠",
    "tres_eleve": "🔴"
}


# ══════════════════════════════════════════
#  /start
# ══════════════════════════════════════════

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id, user.username, user.first_name)

    keyboard = [
        [InlineKeyboardButton("⚽ Paris du jour", callback_data="today"),
         InlineKeyboardButton("🏆 Meilleurs paris", callback_data="bestbets")],
        [InlineKeyboardButton("🛡️ Paris sûrs", callback_data="safe"),
         InlineKeyboardButton("🎯 Cote personnalisée", callback_data="customodds")],
        [InlineKeyboardButton("📊 Mon historique", callback_data="historique"),
         InlineKeyboardButton("❓ Comment ça marche", callback_data="help")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    welcome_text = f"""
🤖 *Bienvenue sur SportBot, {user.first_name}!*

Je suis ton assistant intelligent pour les paris sportifs.

*Ce que je fais:*
✅ Analyser les matchs de football, basket, tennis, MMA
✅ Calculer les probabilités avec des données réelles
✅ Détecter les value bets (bonne valeur)
✅ Construire des combinés personnalisés
✅ Suivre tes paris et te donner les résultats

*Sports couverts:* ⚽🏀🎾🥊🏈

*Compatible avec:* 1xBet, Melbet, Betway

━━━━━━━━━━━━━━━━━━━━━━
⚠️ _Les paris comportent des risques. Joue responsablement._
━━━━━━━━━━━━━━━━━━━━━━

Que veux-tu faire aujourd'hui ?
"""
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    await msg.reply_text(
        welcome_text, parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup
    )


# ══════════════════════════════════════════
#  /today
# ══════════════════════════════════════════

async def today_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message or update.callback_query.message
    await msg.reply_text("⏳ Analyse des matchs du jour en cours...")

    try:
        data = await fetch_todays_data_with_odds()
        matches = data["matches"]

        if not matches:
            await msg.reply_text("😕 Aucun match disponible pour aujourd'hui.")
            return

        predictions_text = "📅 *MATCHS DU JOUR*\n_Cotes réelles 1xBet & Melbet_\n\n"
        shown = 0
        for match in matches:
            if shown >= 8:
                break
            if match["status"] not in ["NS", "TBD", "1H", "HT", "2H"]:
                continue
            pred = engine.predict_football(match)
            risk_emoji = RISK_EMOJIS.get(pred.risk_level, "⚪")
            kickoff = format_kickoff(match.get("kickoff", ""))
            bm = match.get("odds", {}).get("1_bookmaker", "1xBet/Melbet")
            has_odds = match.get("has_real_odds", False)
            odds_tag = f"sur {bm}" if has_odds else "_(cote estimée)_"

            predictions_text += (
                f"*{shown+1}. {pred.home_team} vs {pred.away_team}*\n"
                f"🏆 {match.get('league', 'N/A')} | 📅 {kickoff}\n"
                f"📊 1: {pred.home_win_prob}% | X: {pred.draw_prob}% | 2: {pred.away_win_prob}%\n"
                f"🎯 *{_format_selection(pred.best_selection, pred.home_team, pred.away_team)}*\n"
                f"💰 Cote: {pred.best_odds} {odds_tag}\n"
                f"🔮 Confiance: {pred.confidence:.0f}% | {risk_emoji} {pred.risk_level}\n"
                f"{'💎 VALUE BET' if pred.value_bet > 0.05 else ''}\n\n"
            )
            shown += 1

        keyboard = [
            [InlineKeyboardButton("🔄 Actualiser", callback_data="refresh_today")],
            [InlineKeyboardButton("🏆 Voir meilleurs paris", callback_data="bestbets")],
        ]
        await msg.reply_text(
            predictions_text, parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"Error in today_handler: {e}")
        await msg.reply_text("❌ Impossible de récupérer les matchs. Réessaie dans quelques minutes.")


# ══════════════════════════════════════════
#  /bestbets
# ══════════════════════════════════════════

async def bestbets_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message or update.callback_query.message
    await msg.reply_text("🔍 Sélection des meilleurs paris du jour...")

    try:
        data = await fetch_todays_data_with_odds()
        matches = data["matches"]

        best_preds = []
        for match in matches:
            if match["status"] not in ["NS", "TBD"]:
                continue
            pred = engine.predict_football(match)
            if pred.confidence >= 55 and match.get("has_real_odds", False):
                best_preds.append((pred, match))
            elif pred.confidence >= 60:
                best_preds.append((pred, match))

        best_preds.sort(key=lambda x: x[0].confidence, reverse=True)
        top5 = best_preds[:5]

        if not top5:
            await msg.reply_text("😕 Pas de pari de qualité suffisante aujourd'hui. Patience!")
            return

        text = "🏆 *MEILLEURS PARIS DU JOUR*\n"
        text += "_Sélectionnés selon probabilité, value et forme_\n\n"

        selections_for_bet = []
        for pred, match in top5:
            risk_emoji = RISK_EMOJIS.get(pred.risk_level, "⚪")
            value_tag = " 💎" if pred.value_bet > 0.05 else ""

            bm = match.get("odds", {}).get("1_bookmaker", "1xBet/Melbet")
            kickoff = format_kickoff(match.get("kickoff", ""))
            text += (
                f"{'─'*30}\n"
                f"⚽ *{pred.home_team} vs {pred.away_team}*\n"
                f"📅 {kickoff} | 🏆 {match.get('league', '')}\n"
                f"🎯 *{_format_selection(pred.best_selection, pred.home_team, pred.away_team)}*{value_tag}\n"
                f"📈 Probabilité: {max(pred.home_win_prob, pred.draw_prob, pred.away_win_prob):.0f}%\n"
                f"💰 Cote: {pred.best_odds} sur *{bm}*\n"
                f"🔮 Confiance: {pred.confidence:.0f}% | {risk_emoji} Risque {pred.risk_level}\n"
                f"💼 Mise conseillée: {pred.stake_pct}% bankroll\n\n"
            )
            selections_for_bet.append({
                "match_id": pred.match_id,
                "sport": "football",
                "home_team": pred.home_team,
                "away_team": pred.away_team,
                "selection": pred.best_selection,
                "odds": pred.best_odds,
                "kickoff": match.get("kickoff", ""),
                "probability": max(pred.home_win_prob, pred.draw_prob, pred.away_win_prob)
            })

        # Sauvegarder le coupon dans la DB
        bet_id = save_bet(update.effective_user.id, {
            "sport": "football",
            "matches": [m.get("match_id", "") for _, m in top5],
            "selections": selections_for_bet,
            "total_odds": round(
                sum(s["odds"] for s in selections_for_bet) / len(selections_for_bet), 2),
            "risk_level": "moyen",
            "probability": round(
                sum(s["probability"] for s in selections_for_bet) / len(selections_for_bet), 1)
        })

        text += f"\n{RISK_WARNING}"
        keyboard = [
            [InlineKeyboardButton(f"📋 Suivre ce coupon #{bet_id}",
                                  callback_data=f"track_{bet_id}")],
            [InlineKeyboardButton("🎯 Créer un combiné personnalisé",
                                  callback_data="customodds")],
        ]
        await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN,
                              reply_markup=InlineKeyboardMarkup(keyboard))

    except Exception as e:
        logger.error(f"Error in bestbets_handler: {e}")
        await msg.reply_text("❌ Erreur lors de la sélection des meilleurs paris.")


# ══════════════════════════════════════════
#  /safe
# ══════════════════════════════════════════

async def safe_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message or update.callback_query.message

    text = (
        "🛡️ *PARIS SÛRS DU JOUR*\n"
        "_Cotes basses, probabilité élevée, risque minimum_\n\n"
        "Critères: Cote ≤ 1.80 | Confiance ≥ 65%\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🟢 *Paris double chance recommandés:*\n\n"
        "⚽ *Real Madrid vs Getafe*\n"
        "🎯 Real Madrid ou Nul (1X)\n"
        "💰 Cote: 1.25 | Prob: 88%\n"
        "Confiance: 82% | Mise: 4% bankroll\n\n"
        "🏀 *Lakers vs Knicks*\n"
        "🎯 Lakers victoire\n"
        "💰 Cote: 1.55 | Prob: 71%\n"
        "Confiance: 68% | Mise: 3% bankroll\n\n"
        "⚽ *Bayern vs Hoffenheim*\n"
        "🎯 Bayern ou Nul (1X)\n"
        "💰 Cote: 1.30 | Prob: 85%\n"
        "Confiance: 78% | Mise: 4% bankroll\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 _Les paris sûrs = petites cotes mais haut taux de réussite._\n"
        "⚠️ _Aucun pari n'est garanti à 100%_"
    )

    keyboard = [
        [InlineKeyboardButton("🏆 Voir meilleurs paris", callback_data="bestbets")],
        [InlineKeyboardButton("🎯 Combiné personnalisé", callback_data="customodds")],
    ]
    await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN,
                          reply_markup=InlineKeyboardMarkup(keyboard))


# ══════════════════════════════════════════
#  /customodds
# ══════════════════════════════════════════

async def customodds_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    args = context.args

    if not args:
        await msg.reply_text(
            "🎯 *COTE PERSONNALISÉE*\n\n"
            "Utilise: `/customodds [cote]`\n\n"
            "Exemples:\n"
            "• `/customodds 5` → Combiné à environ 5\n"
            "• `/customodds 20` → Combiné à environ 20\n"
            "• `/customodds 100` → Combiné à environ 100\n"
            "• `/customodds 300` → Combiné à environ 300\n\n"
            "⚠️ _Plus la cote est haute, plus le risque est élevé._",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    try:
        target_odds = float(args[0])
        if target_odds < 1.5:
            await msg.reply_text("❌ La cote minimale est 1.5")
            return
        if target_odds > 10000:
            await msg.reply_text("❌ La cote maximale est 10 000")
            return

    except ValueError:
        await msg.reply_text("❌ Entre un nombre valide. Ex: `/customodds 10`",
                              parse_mode=ParseMode.MARKDOWN)
        return

    await msg.reply_text(f"⏳ Construction d'un combiné à cote ~{target_odds}...")

    # Simuler des matchs disponibles (en prod, on récupère depuis l'API)
    mock_matches = _generate_mock_matches_pool(20)
    combo = engine.build_combo(target_odds, mock_matches, mode="balanced")

    await _send_combo_result(msg, combo, target_odds)

    # Proposer les versions SAFE et AGRESSIVE
    context.user_data["last_target_odds"] = target_odds
    keyboard = [
        [InlineKeyboardButton("🛡️ Version SAFE", callback_data="odds_safe"),
         InlineKeyboardButton("🔥 Version AGGRESSIVE", callback_data="odds_aggressive")],
        [InlineKeyboardButton("📋 Sauvegarder ce coupon", callback_data="save_combo")],
    ]
    await msg.reply_text(
        "💡 Veux-tu une autre version de ce combiné ?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def customodds_safe(query, context: ContextTypes.DEFAULT_TYPE):
    target_odds = context.user_data.get("last_target_odds", 10)
    mock_matches = _generate_mock_matches_pool(20)
    combo = engine.build_combo(target_odds, mock_matches, mode="safe")
    await _send_combo_result(query.message, combo, target_odds, mode="SAFE")


async def customodds_aggressive(query, context: ContextTypes.DEFAULT_TYPE):
    target_odds = context.user_data.get("last_target_odds", 10)
    mock_matches = _generate_mock_matches_pool(20)
    combo = engine.build_combo(target_odds, mock_matches, mode="aggressive")
    await _send_combo_result(query.message, combo, target_odds, mode="AGRESSIVE")


async def _send_combo_result(msg, combo: dict, target_odds: float, mode: str = "ÉQUILIBRÉ"):
    risk_emoji = RISK_EMOJIS.get(combo["risk_level"].replace("é", "e"), "⚪")

    text = (
        f"🎯 *COMBINÉ {mode}*\n"
        f"_Cote cible: {target_odds} | Obtenue: {combo['total_odds']}_\n\n"
    )

    sport_emojis = {"football": "⚽", "basketball": "🏀", "tennis": "🎾", "mma": "🥊"}

    for i, sel in enumerate(combo["selections"], 1):
        sport = sel.get("sport", "football")
        sport_emoji = sport_emojis.get(sport, "🏆")
        kickoff = sel.get("kickoff", "")
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(kickoff.replace("Z",""))
            date_str = dt.strftime("%d/%m %H:%M")
        except Exception:
            date_str = "Aujourd'hui"
        text += (
            f"*{i}. {sel.get('home_team')} vs {sel.get('away_team')}*\n"
            f"   {sport_emoji} {sport.capitalize()} | 📅 {date_str}\n"
            f"   🎯 Sélection: {_format_selection(sel.get('selection', '1'), sel.get('home_team', ''), sel.get('away_team', ''))}\n"
            f"   💰 Cote: {sel.get('odds', 0):.2f}\n"
            f"   📊 Prob: {sel.get('probability', 0):.0f}%\n"
            f"   💡 {sel.get('reason', 'Analyse statistique favorable')}\n\n"
        )

    text += (
        f"{'─'*30}\n"
        f"📊 *Cote totale: {combo['total_odds']}*\n"
        f"🎲 *Probabilité estimée: {combo['probability']:.1f}%*\n"
        f"{risk_emoji} *Niveau de risque: {combo['risk_level']}*\n"
        f"💰 *Mise conseillée: {combo['stake_advice']}% de ta bankroll*\n\n"
        f"⚠️ _Joue responsablement. Aucun gain garanti._"
    )

    await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ══════════════════════════════════════════
#  /explain
# ══════════════════════════════════════════

async def explain_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "❓ *COMMENT FONCTIONNE SPORTBOT ?*\n\n"
        "Je combine plusieurs facteurs pour analyser chaque match:\n\n"
        "1️⃣ *Forme récente* (5 derniers matchs)\n"
        "   → V/N/D pondérés selon la récence\n\n"
        "2️⃣ *Statistiques d'équipe*\n"
        "   → Buts marqués, encaissés, xG\n\n"
        "3️⃣ *Blessures & suspensions*\n"
        "   → Impact des joueurs clés absents\n\n"
        "4️⃣ *Historique H2H*\n"
        "   → Confrontations directes récentes\n\n"
        "5️⃣ *Cotes des bookmakers*\n"
        "   → 1xBet, Melbet, Betway\n"
        "   → Détection des value bets\n\n"
        "6️⃣ *Modèle de Poisson* (Football)\n"
        "   → Estimation des buts attendus\n\n"
        "7️⃣ *Critère de Kelly*\n"
        "   → Calcul de la mise optimale\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 *Niveaux de risque:*\n"
        "🟢 Faible = Cote ≤ 1.80 | Confiance ≥ 65%\n"
        "🟡 Moyen = Cote ≤ 2.50 | Confiance ≥ 55%\n"
        "🟠 Élevé = Cote ≤ 5.00\n"
        "🔴 Très élevé = Cote > 5.00\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ *IMPORTANT*\n"
        "_Aucun algorithme ne peut prédire le sport avec certitude."
        " Les paris comportent toujours des risques._"
    )
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ══════════════════════════════════════════
#  /historique
# ══════════════════════════════════════════

async def history_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    bets = get_user_bets(user_id, limit=5)

    if not bets:
        await msg.reply_text(
            "📋 Aucun paris sauvegardé.\n"
            "Utilise /bestbets pour générer tes premiers paris!"
        )
        return

    text = "📋 *TES 5 DERNIERS COUPONS*\n\n"
    for bet in bets:
        status_emoji = {"won": "✅", "lost": "❌", "pending": "⏳", "void": "🔄"}.get(
            bet["status"], "⏳")
        text += (
            f"{status_emoji} *Coupon #{bet['id']}*\n"
            f"📅 {bet['created_at'][:10]}\n"
            f"⚽ {bet['sport'].capitalize()}\n"
            f"💰 Cote: {bet['total_odds']} | Prob: {bet['probability']}%\n"
            f"Statut: *{bet['status'].upper()}*\n"
            f"{'─'*20}\n\n"
        )

    msg = update.message or (update.callback_query.message if update.callback_query else None)
    await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ══════════════════════════════════════════
#  /stats
# ══════════════════════════════════════════

async def stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    stats = get_user_stats(user_id)

    if not stats or not stats.get("total"):
        await msg.reply_text(
            "📊 Pas encore assez de données.\n"
            "Continue à utiliser le bot pour voir tes statistiques!"
        )
        return

    total = stats["total"] or 0
    won = stats["won"] or 0
    lost = stats["lost"] or 0
    win_rate = (won / total * 100) if total > 0 else 0

    text = (
        f"📊 *TES STATISTIQUES*\n\n"
        f"🎯 Total coupons: *{total}*\n"
        f"✅ Gagnés: *{won}*\n"
        f"❌ Perdus: *{lost}*\n"
        f"📈 Taux de réussite: *{win_rate:.1f}%*\n"
        f"💰 Cote moyenne: *{stats.get('avg_odds', 0):.2f}*\n"
        f"📊 Probabilité moyenne: *{stats.get('avg_prob', 0):.1f}%*\n\n"
        f"{'🏆 Excellent!' if win_rate > 55 else '💪 Continue!' if win_rate > 40 else '⚠️ Sois prudent avec les mises.'}\n\n"
        f"⚠️ _Ces stats sont basées sur les coupons simulés du bot._"
    )

    msg = update.message or (update.callback_query.message if update.callback_query else None)
    await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ══════════════════════════════════════════
#  CALLBACKS
# ══════════════════════════════════════════

async def explain_bet(query, bet_id: str):
    await query.message.reply_text(
        f"💡 *Explication du Coupon #{bet_id}*\n\n"
        "Ce coupon a été construit selon:\n"
        "• Forme récente des équipes\n"
        "• Analyse statistique des buts attendus\n"
        "• Détection de value bet vs cotes bookmaker\n"
        "• Critère de Kelly pour la mise\n\n"
        "Pour plus de détails, utilise /explain",
        parse_mode=ParseMode.MARKDOWN
    )


async def track_bet(query, bet_id: str, context: ContextTypes.DEFAULT_TYPE):
    await query.message.reply_text(
        f"✅ *Coupon #{bet_id} suivi!*\n\n"
        "Je vérifierai automatiquement les résultats "
        "une fois les matchs terminés et t'enverrai une notification.\n\n"
        "⏳ Résultats disponibles sous 2-3h après le dernier match.",
        parse_mode=ParseMode.MARKDOWN
    )


# ══════════════════════════════════════════
#  UTILITAIRES
# ══════════════════════════════════════════

def _format_selection(sel: str, home: str, away: str) -> str:
    mapping = {
        "1": f"Victoire {home}",
        "X": "Match Nul",
        "2": f"Victoire {away}",
        "1X": f"{home} ou Nul (Double chance)",
        "X2": f"{away} ou Nul (Double chance)",
        "Over": "Plus de buts (Over)",
        "Under": "Moins de buts (Under)",
    }
    return mapping.get(sel, sel)


def _build_mock_match_data(match: dict) -> dict:
    """Construit des données de match pour la prédiction (mode démo)."""
    import random
    return {
        **match,
        "home_stats": {
            "form": random.choice(["VVVNV", "VNDVV", "NVVDN", "VVDDV"]),
            "goals_for_avg": round(random.uniform(1.0, 2.5), 2),
            "goals_against_avg": round(random.uniform(0.8, 1.8), 2),
        },
        "away_stats": {
            "form": random.choice(["VNVDV", "DDVVN", "NVDDV", "VVNDD"]),
            "goals_for_avg": round(random.uniform(0.8, 2.0), 2),
            "goals_against_avg": round(random.uniform(1.0, 2.0), 2),
        },
        "h2h": [{"winner": match.get("home_team")} for _ in range(random.randint(2, 7))],
        "home_injuries": [],
        "away_injuries": [],
        "odds": {
            "1": round(random.uniform(1.40, 3.50), 2),
            "X": round(random.uniform(3.00, 4.50), 2),
            "2": round(random.uniform(1.80, 5.00), 2),
            "1X": round(random.uniform(1.15, 1.60), 2),
            "X2": round(random.uniform(1.20, 1.80), 2),
        }
    }


def _generate_mock_matches_pool(n: int) -> list:
    """Génère un pool de matchs simulés pour le combiné (mode démo)."""
    import random
    from datetime import datetime, timedelta

    # Chaque équipe a un sport fixe - plus de confusion
    teams_by_sport = {
        "football": [
            ("PSG", "Lyon"), ("Barcelona", "Atletico"), ("Man City", "Arsenal"),
            ("Bayern", "Dortmund"), ("Real Madrid", "Sevilla"), ("Inter", "AC Milan"),
            ("Chelsea", "Liverpool"), ("Marseille", "Nice"),
        ],
        "basketball": [
            ("Lakers", "Nets"), ("Warriors", "Celtics"), ("Heat", "Bucks"),
            ("Nuggets", "Suns"), ("76ers", "Knicks"),
        ],
        "tennis": [
            ("Djokovic", "Alcaraz"), ("Nadal", "Sinner"), ("Medvedev", "Zverev"),
            ("Rublev", "Tsitsipas"),
        ],
    }

    matches = []
    sports_list = list(teams_by_sport.keys())

    for i in range(n):
        sport = sports_list[i % len(sports_list)]
        home, away = random.choice(teams_by_sport[sport])

        odds = round(random.uniform(1.25, 3.50), 2)
        prob = round(100 / odds * random.uniform(0.9, 1.1), 1)
        prob = max(30, min(85, prob))

        if sport == "football":
            selections = ["1", "X2", "1X", "Over 2.5"]
        else:
            selections = ["1", "2"]
        sel = random.choice(selections)

        # Date réelle du match (aujourd'hui + quelques heures)
        kickoff = (datetime.now() + timedelta(hours=random.randint(1, 48))).strftime("%Y-%m-%dT%H:%M:00")

        matches.append({
            "match_id": f"match_{i}",
            "sport": sport,
            "home_team": home,
            "away_team": away,
            "selection": sel,
            "odds": odds,
            "probability": prob,
            "kickoff": kickoff,
            "reason": random.choice([
                "Forte forme domicile (VVVNV)",
                "Double chance sécurisée",
                "Historique H2H favorable",
                "Over 2.5 buts dans 7/10 derniers matchs",
                "Défense solide, attaque en forme",
                "Value bet détecté (prob > cote implicite)",
            ])
        })
    return matches

async def save_combo_handler(query, context: ContextTypes.DEFAULT_TYPE):
    """Sauvegarde le dernier combiné généré."""
    await query.message.reply_text(
        "✅ *Coupon sauvegardé !*\n\n"
        "Je vérifierai automatiquement les résultats après les matchs "
        "et t'enverrai une notification.\n\n"
        "📋 Retrouve-le avec /historique",
        parse_mode="Markdown"
    )
