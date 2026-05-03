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
            await msg.reply_text(
                "😕 Aucun match trouvé pour aujourd'hui.\n\n"
                "💡 _Les matchs sont mis à jour chaque matin. "
                "Reviens demain ou utilise /bestbets pour les prochains jours._",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        predictions_text = "📅 *MATCHS DU JOUR*\n_Cotes réelles 1xBet & Melbet_\n\n"
        # Trier par kickoff
        import re
        def sort_key(m):
            return m.get("kickoff", "")
        matches_sorted = sorted(matches, key=sort_key)

        shown = 0
        for match in matches_sorted:
            if shown >= 8:
                break
            status = match.get("status", "NS")
            if status in ["FT", "AET", "PEN", "AWD", "WO", "CANC", "ABD", "INT"]:
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
            status = match.get("status", "NS")
            # On prend seulement les matchs pas encore commencés pour les paris
            if status in ["FT", "AET", "PEN", "AWD", "WO", "CANC", "ABD", "INT", "1H", "HT", "2H", "ET", "P"]:
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
            bm_name = match.get("odds", {}).get("1_bookmaker", "1xBet")
            selections_for_bet.append({
                "match_id": pred.match_id,
                "sport": "football",
                "home_team": pred.home_team,
                "away_team": pred.away_team,
                "selection": pred.best_selection,
                "odds": pred.best_odds,
                "kickoff": match.get("kickoff", ""),
                "probability": max(pred.home_win_prob, pred.draw_prob, pred.away_win_prob),
                "bookmaker": bm_name,
                "league": match.get("league", ""),
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
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    await msg.reply_text("⏳ Recherche des paris sûrs du jour...")

    try:
        data = await fetch_todays_data_with_odds()
        matches = data["matches"]

        FINISHED = ["FT", "AET", "PEN", "AWD", "WO", "CANC", "ABD", "INT"]
        safe_preds = []
        for match in matches:
            if match.get("status", "NS") in FINISHED:
                continue
            pred = engine.predict_football(match)
            if pred.best_odds <= 1.80 and pred.confidence >= 60:
                safe_preds.append((pred, match))

        safe_preds.sort(key=lambda x: x[0].confidence, reverse=True)

        text = (
            "🛡️ *PARIS SÛRS DU JOUR*\n"
            "_Cotes réelles 1xBet & Melbet_\n"
            "_Cote ≤ 1.80 | Confiance ≥ 60%_\n\n"
        )

        if not safe_preds:
            text += (
                "😕 Pas de paris sûrs disponibles maintenant.\n\n"
                "💡 _Réessaie demain matin quand les nouvelles_\n"
                "_cotes sont disponibles._"
            )
        else:
            text += "🟢 *Paris recommandés:*\n\n"
            for pred, match in safe_preds[:5]:
                bm = match.get("odds", {}).get("1_bookmaker", "1xBet")
                kickoff = format_kickoff(match.get("kickoff", ""))
                sport_emoji = "⚽" if match["sport"] == "football" else "🏀"
                text += (
                    f"{sport_emoji} *{pred.home_team} vs {pred.away_team}*\n"
                    f"📅 {kickoff} | 🏆 {match.get('league', '')}\n"
                    f"🎯 {_format_selection(pred.best_selection, pred.home_team, pred.away_team)}\n"
                    f"💰 Cote: {pred.best_odds} sur *{bm}*\n"
                    f"🔮 Confiance: {pred.confidence:.0f}% | Mise: {pred.stake_pct}% bankroll\n\n"
                )

        text += "━━━━━━━━━━━━━━━━━━━━━━\n"
        text += "💡 _Petites cotes = haut taux de réussite._\n"
        text += "⚠️ _Aucun pari n\'est garanti à 100%_"

        keyboard = [
            [InlineKeyboardButton("🏆 Voir meilleurs paris", callback_data="bestbets")],
            [InlineKeyboardButton("🎯 Combiné personnalisé", callback_data="customodds")],
        ]
        await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN,
                              reply_markup=InlineKeyboardMarkup(keyboard))

    except Exception as e:
        logger.error(f"Error in safe_handler: {e}")
        await msg.reply_text("❌ Erreur. Réessaie dans quelques minutes.")


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

    await msg.reply_text(f"⏳ Construction d'un combiné à cote ~{target_odds}...\n_Récupération des vrais matchs 1xBet/Melbet_")

    # Récupérer les vrais matchs avec leurs vraies cotes
    data = await fetch_todays_data_with_odds()
    real_matches = data["matches"]
    all_odds = data["all_odds"]

    # Construire le pool depuis les vrais matchs + cotes
    match_pool = []
    for m in real_matches:
        if m["status"] not in ["NS", "TBD"]:
            continue
        odds = m.get("odds", {})
        # Ajouter chaque sélection disponible comme entrée du pool
        for sel, sel_label in [("1", "Victoire " + m["home_team"]),
                                ("X2", m["away_team"] + " ou Nul"),
                                ("1X", m["home_team"] + " ou Nul"),
                                ("Over 2.5", "Over 2.5 buts")]:
            odds_val = odds.get(sel, 0)
            if odds_val and odds_val > 1.1:
                # Prédiction pour cette sélection
                pred = engine.predict_football(m)
                prob = (pred.home_win_prob if sel == "1"
                        else pred.away_win_prob if sel == "2"
                        else pred.home_win_prob + pred.draw_prob if sel == "1X"
                        else pred.away_win_prob + pred.draw_prob if sel == "X2"
                        else 55.0)
                bm = odds.get("1_bookmaker", "1xBet")
                match_pool.append({
                    "match_id": m["match_id"],
                    "sport": "football",
                    "home_team": m["home_team"],
                    "away_team": m["away_team"],
                    "league": m.get("league", ""),
                    "selection": sel,
                    "odds": odds_val,
                    "probability": round(prob, 1),
                    "kickoff": m.get("kickoff", ""),
                    "bookmaker": bm,
                    "reason": f"Analyse statistique - {m.get('league', '')}",
                })

    # Si pas assez de vrais matchs, compléter avec des matchs futurs des cotes API
    for event in all_odds:
        home = event.get("home_team", "")
        away = event.get("away_team", "")
        sport = event.get("sport", "football")
        kickoff = event.get("kickoff", "")
        for bm_name, bm_data in event.get("bookmakers", {}).items():
            for team, odds_val in bm_data.get("h2h", {}).items():
                if odds_val and odds_val > 1.1:
                    is_home = team.lower() in home.lower() or home.lower() in team.lower()
                    is_away = team.lower() in away.lower() or away.lower() in team.lower()
                    sel = "1" if is_home else ("2" if is_away else None)
                    if not sel:
                        continue
                    prob = round(100 / odds_val * 0.92, 1)
                    sport_clean = ("football" if "soccer" in sport
                                   else "basketball" if "basketball" in sport
                                   else "tennis" if "tennis" in sport
                                   else "mma" if "mma" in sport else "football")
                    match_pool.append({
                        "match_id": f"odds_{home}_{away}",
                        "sport": sport_clean,
                        "home_team": home,
                        "away_team": away,
                        "selection": sel,
                        "odds": odds_val,
                        "probability": prob,
                        "kickoff": kickoff,
                        "bookmaker": bm_name,
                        "reason": f"Cote disponible sur {bm_name}",
                    })

    # Dédupliquer
    seen = set()
    unique_pool = []
    for m in match_pool:
        key = f"{m['home_team']}_{m['away_team']}_{m['selection']}"
        if key not in seen:
            seen.add(key)
            unique_pool.append(m)

    if not unique_pool:
        await msg.reply_text("😕 Pas assez de matchs disponibles sur 1xBet/Melbet pour l'instant. Réessaie plus tard.")
        return

    combo = engine.build_combo(target_odds, unique_pool, mode="balanced")
    await _send_combo_result(msg, combo, target_odds)

    # Sauvegarder pour usage ultérieur
    context.user_data["last_target_odds"] = target_odds
    context.user_data["last_match_pool"] = unique_pool
    context.user_data["last_combo"] = combo
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
    pool = context.user_data.get("last_match_pool", [])
    if not pool:
        data = await fetch_todays_data_with_odds()
        pool = [{"match_id": m["match_id"], "sport": "football",
                 "home_team": m["home_team"], "away_team": m["away_team"],
                 "selection": "1X", "odds": m.get("odds", {}).get("1X", 1.3),
                 "probability": 70, "kickoff": m.get("kickoff", ""),
                 "bookmaker": "1xBet", "reason": "Double chance sécurisée"}
                for m in data["matches"] if m.get("has_real_odds")]
    combo = engine.build_combo(target_odds, pool, mode="safe")
    context.user_data["last_combo"] = combo
    await _send_combo_result(query.message, combo, target_odds, mode="SAFE")


async def customodds_aggressive(query, context: ContextTypes.DEFAULT_TYPE):
    target_odds = context.user_data.get("last_target_odds", 10)
    pool = context.user_data.get("last_match_pool", [])
    if not pool:
        data = await fetch_todays_data_with_odds()
        pool = [{"match_id": m["match_id"], "sport": "football",
                 "home_team": m["home_team"], "away_team": m["away_team"],
                 "selection": "1", "odds": m.get("odds", {}).get("1", 2.0),
                 "probability": 55, "kickoff": m.get("kickoff", ""),
                 "bookmaker": "1xBet", "reason": "Pari offensif"}
                for m in data["matches"] if m.get("has_real_odds")]
    combo = engine.build_combo(target_odds, pool, mode="aggressive")
    context.user_data["last_combo"] = combo
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
            f"   💰 Cote: {sel.get('odds', 0):.2f} sur *{sel.get('bookmaker', '1xBet/Melbet')}*\n"
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
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    text = (
        "❓ *COMMENT UTILISER SPORTBOT ?*\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📱 *GUIDE D\'UTILISATION*\n\n"
        "1️⃣ *⚽ Paris du jour*\n"
        "   → Voir tous les matchs analysés aujourd\'hui\n"
        "   → Probabilités et cotes réelles 1xBet/Melbet\n\n"
        "2️⃣ *🏆 Meilleurs paris*\n"
        "   → Top 5 sélections filtrées automatiquement\n"
        "   → Appuie \'Suivre\' pour sauvegarder le coupon\n\n"
        "3️⃣ *🛡️ Paris sûrs*\n"
        "   → Cote ≤ 1.80 | Risque minimum\n"
        "   → Idéal pour les débutants\n\n"
        "4️⃣ *🎯 Cote personnalisée*\n"
        "   → Tape: /customodds 10\n"
        "   → Le bot construit un combiné sur mesure\n"
        "   → Exemples: /customodds 5, 20, 50, 100\n"
        "   → Choisis SAFE ou AGRESSIF\n"
        "   → Sauvegarde pour suivi automatique\n\n"
        "5️⃣ *📋 Mon historique*\n"
        "   → Voir tous tes coupons sauvegardés\n"
        "   → ⏳ En attente | ✅ Gagné | ❌ Perdu\n"
        "   → Mis à jour auto après chaque match\n\n"
        "6️⃣ *📊 /stats*\n"
        "   → Ton taux de réussite personnel\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🧠 *COMMENT ÇA ANALYSE ?*\n\n"
        "Le bot combine 7 facteurs:\n"
        "📈 Forme récente (5 derniers matchs)\n"
        "🏠 Avantage domicile (+8%)\n"
        "⚡ Buts attendus (modèle de Poisson)\n"
        "🔄 Historique confrontations directes\n"
        "💰 Vraies cotes 1xBet et Melbet\n"
        "🏥 Blessures et suspensions\n"
        "📐 Critère de Kelly (mise optimale)\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔔 *SUIVI AUTOMATIQUE*\n"
        "Sauvegarde un coupon → le bot vérifie\n"
        "les résultats toutes les 30min et\n"
        "t\'envoie: ✅ GAGNÉ ou ❌ PERDU\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ _Les paris comportent des risques._\n"
        "_Ne mise jamais plus que ce que tu peux perdre._"
    )
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
    status_map = {
        "won":     "✅ GAGNÉ",
        "lost":    "❌ PERDU",
        "pending": "⏳ En attente des résultats",
        "void":    "🔄 Annulé",
    }
    for bet in bets:
        status_label = status_map.get(bet["status"], "⏳ En attente")
        emoji = status_label.split()[0]
        text += (
            f"{emoji} *Coupon #{bet['id']}*\n"
            f"📅 {bet['created_at'][:10]} | 🏅 {bet['sport'].capitalize()}\n"
            f"💰 Cote: *{bet['total_odds']}* | Prob: {bet['probability']}%\n"
            f"📌 {status_label}\n"
            f"{'─'*22}\n\n"
        )

    text += "_Les coupons ⏳ seront mis à jour automatiquement après les matchs._"
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
    """Sauvegarde le dernier combiné généré en base de données."""
    user_id = query.from_user.id
    last_combo = context.user_data.get("last_combo")

    if not last_combo:
        await query.message.reply_text(
            "❌ Aucun combiné à sauvegarder. Génère d'abord un combiné avec /customodds",
            parse_mode="Markdown"
        )
        return

    try:
        bet_id = save_bet(user_id, {
            "sport": "multi-sports",
            "matches": [s.get("match_id", "") for s in last_combo["selections"]],
            "selections": last_combo["selections"],
            "total_odds": last_combo["total_odds"],
            "risk_level": last_combo["risk_level"],
            "probability": last_combo["probability"],
            "stake_advice": last_combo.get("stake_advice", 1.0),
        })
        await query.message.reply_text(
            f"✅ *Coupon #{bet_id} sauvegardé !*\n\n"
            f"📊 Cote totale: *{last_combo['total_odds']}*\n"
            f"🎲 Probabilité: *{last_combo['probability']}%*\n"
            f"🔢 {last_combo['n_matches']} sélections\n\n"
            f"Je vérifierai les résultats automatiquement après les matchs "
            f"et t'enverrai une notification.\n\n"
            f"📋 Retrouve-le dans /historique",
            parse_mode="Markdown"
        )
    except Exception as e:
        await query.message.reply_text(
            "❌ Erreur lors de la sauvegarde. Réessaie.",
            parse_mode="Markdown"
        )
