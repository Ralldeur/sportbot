"""
Base de données - Modèles et initialisation
"""
import sqlite3
from datetime import datetime
import json

DB_PATH = "sportbot.db"


def init_db():
    """Initialise toutes les tables de la base de données."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Table utilisateurs
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        joined_at TEXT,
        is_premium INTEGER DEFAULT 0,
        notifications_enabled INTEGER DEFAULT 1,
        language TEXT DEFAULT 'fr',
        bankroll REAL DEFAULT 0,
        total_bets INTEGER DEFAULT 0,
        won_bets INTEGER DEFAULT 0
    )''')

    # Table des paris proposés
    c.execute('''CREATE TABLE IF NOT EXISTS bets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        created_at TEXT,
        sport TEXT,
        matches TEXT,         -- JSON array des matchs
        selections TEXT,      -- JSON array des sélections
        total_odds REAL,
        risk_level TEXT,
        probability REAL,
        stake_advice REAL,
        status TEXT DEFAULT 'pending',  -- pending, won, lost, void
        result_checked_at TEXT,
        notes TEXT
    )''')

    # Table des matchs suivis
    c.execute('''CREATE TABLE IF NOT EXISTS tracked_matches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bet_id INTEGER,
        match_id TEXT,
        sport TEXT,
        home_team TEXT,
        away_team TEXT,
        selection TEXT,
        odds REAL,
        kickoff TEXT,
        status TEXT DEFAULT 'pending',  -- pending, won, lost, void
        final_score TEXT,
        FOREIGN KEY (bet_id) REFERENCES bets(id)
    )''')

    # Table des notifications programmées
    c.execute('''CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        match_id TEXT,
        notify_at TEXT,
        message TEXT,
        sent INTEGER DEFAULT 0
    )''')

    # Table des statistiques globales du bot
    c.execute('''CREATE TABLE IF NOT EXISTS bot_stats (
        date TEXT PRIMARY KEY,
        total_bets_sent INTEGER DEFAULT 0,
        won_bets INTEGER DEFAULT 0,
        lost_bets INTEGER DEFAULT 0,
        avg_odds REAL DEFAULT 0
    )''')

    conn.commit()
    conn.close()
    print("✅ Base de données initialisée")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def register_user(user_id: int, username: str, first_name: str):
    conn = get_connection()
    c = conn.cursor()
    c.execute('''INSERT OR IGNORE INTO users 
                 (user_id, username, first_name, joined_at) 
                 VALUES (?, ?, ?, ?)''',
              (user_id, username, first_name, datetime.now().isoformat()))
    conn.commit()
    conn.close()


def save_bet(user_id: int, bet_data: dict) -> int:
    """Sauvegarde un coupon de pari et retourne son ID."""
    conn = get_connection()
    c = conn.cursor()
    c.execute('''INSERT INTO bets 
                 (user_id, created_at, sport, matches, selections, 
                  total_odds, risk_level, probability, stake_advice)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
              (user_id,
               datetime.now().isoformat(),
               bet_data['sport'],
               json.dumps(bet_data['matches']),
               json.dumps(bet_data['selections']),
               bet_data['total_odds'],
               bet_data['risk_level'],
               bet_data['probability'],
               bet_data.get('stake_advice', 2.0)))
    bet_id = c.lastrowid

    # Sauvegarder les matchs individuels
    for match in bet_data['selections']:
        c.execute('''INSERT INTO tracked_matches
                     (bet_id, match_id, sport, home_team, away_team,
                      selection, odds, kickoff)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                  (bet_id, match['match_id'], match['sport'],
                   match['home_team'], match['away_team'],
                   match['selection'], match['odds'], match['kickoff']))

    conn.commit()
    conn.close()
    return bet_id


def get_user_bets(user_id: int, limit: int = 10) -> list:
    conn = get_connection()
    c = conn.cursor()
    c.execute('''SELECT * FROM bets WHERE user_id = ? 
                 ORDER BY created_at DESC LIMIT ?''', (user_id, limit))
    bets = [dict(row) for row in c.fetchall()]
    conn.close()
    return bets


def get_user_stats(user_id: int) -> dict:
    conn = get_connection()
    c = conn.cursor()
    c.execute('''SELECT 
                   COUNT(*) as total,
                   SUM(CASE WHEN status='won' THEN 1 ELSE 0 END) as won,
                   SUM(CASE WHEN status='lost' THEN 1 ELSE 0 END) as lost,
                   AVG(total_odds) as avg_odds,
                   AVG(probability) as avg_prob
                 FROM bets WHERE user_id = ? AND status != 'pending' ''',
              (user_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else {}


def update_bet_status(bet_id: int, status: str, notes: str = ""):
    conn = get_connection()
    c = conn.cursor()
    c.execute('''UPDATE bets SET status=?, result_checked_at=?, notes=?
                 WHERE id=?''',
              (status, datetime.now().isoformat(), notes, bet_id))
    conn.commit()
    conn.close()


def get_pending_bets() -> list:
    """Retourne tous les paris en attente pour vérification."""
    conn = get_connection()
    c = conn.cursor()
    c.execute('''SELECT b.*, GROUP_CONCAT(tm.match_id) as match_ids
                 FROM bets b
                 JOIN tracked_matches tm ON b.id = tm.bet_id
                 WHERE b.status = 'pending'
                 GROUP BY b.id''')
    bets = [dict(row) for row in c.fetchall()]
    conn.close()
    return bets
