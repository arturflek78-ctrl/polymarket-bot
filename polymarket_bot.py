"""
Polymarket HFT Bot — Paper Mode
Стратегия: Low-Prob (массовые ставки на маловероятные события)

PAPER MODE: реальных денег не тратится, всё симулируется.
"""

import requests
import time
import json
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional

# ─────────────────────────────────────────────
# НАСТРОЙКИ
# ─────────────────────────────────────────────

STARTING_BALANCE = 1000.0       # стартовый баланс в симуляции ($)
BET_SIZE = 10.0                 # размер одной ставки ($)
MAX_PROB_THRESHOLD = 0.03       # ставим только если вероятность YES < 3%
MIN_VOLUME = 1000.0             # минимальный объём рынка ($)
SCAN_INTERVAL_SEC = 30          # пауза между сканированиями (сек)
MAX_BETS_PER_SCAN = 5           # максимум ставок за один скан
LOG_FILE = "bot_log.json"


# ─────────────────────────────────────────────
# СТРУКТУРЫ ДАННЫХ
# ─────────────────────────────────────────────

@dataclass
class PaperBet:
    market_id: str
    question: str
    outcome: str          # YES или NO
    price: float          # цена в момент ставки (0.0–1.0)
    amount: float         # размер ставки ($)
    shares: float         # количество акций
    timestamp: str
    resolved: bool = False
    won: Optional[bool] = None
    payout: float = 0.0


@dataclass
class PaperWallet:
    balance: float = STARTING_BALANCE
    bets: list = field(default_factory=list)
    total_bet: float = 0.0
    total_won: float = 0.0
    total_lost: float = 0.0

    @property
    def profit(self):
        return self.total_won - self.total_lost

    @property
    def win_rate(self):
        resolved = [b for b in self.bets if b.resolved]
        if not resolved:
            return 0.0
        return sum(1 for b in resolved if b.won) / len(resolved) * 100


# ─────────────────────────────────────────────
# POLYMARKET API
# ─────────────────────────────────────────────

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API  = "https://clob.polymarket.com"

def fetch_markets(limit=100, offset=0) -> list[dict]:
    """Получить список активных рынков."""
    try:
        resp = requests.get(
            f"{GAMMA_API}/markets",
            params={
                "active": "true",
                "closed": "false",
                "limit": limit,
                "offset": offset,
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"  [!] Ошибка получения рынков: {e}")
        return []


def fetch_orderbook(token_id: str) -> Optional[dict]:
    """Получить ордербук для конкретного токена."""
    try:
        resp = requests.get(
            f"{CLOB_API}/book",
            params={"token_id": token_id},
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def get_best_ask(orderbook: dict) -> Optional[float]:
    """Лучшая цена ask (минимальная цена продавца)."""
    asks = orderbook.get("asks", [])
    if not asks:
        return None
    try:
        return min(float(a["price"]) for a in asks)
    except Exception:
        return None


# ─────────────────────────────────────────────
# ЛОГИКА СТРАТЕГИИ
# ─────────────────────────────────────────────

def find_low_prob_markets(markets: list[dict]) -> list[dict]:
    """
    Найти рынки с маловероятными событиями (YES < MAX_PROB_THRESHOLD).
    Это рынки, где можно купить YES дёшево.
    """
    candidates = []

    for market in markets:
        try:
            volume = float(market.get("volume", 0) or 0)
            if volume < MIN_VOLUME:
                continue

            tokens = market.get("tokens", [])
            if len(tokens) < 2:
                continue

            # Ищем токен YES
            yes_token = next((t for t in tokens if t.get("outcome", "").upper() == "YES"), None)
            if not yes_token:
                continue

            yes_price = float(yes_token.get("price", 1.0) or 1.0)

            if yes_price <= MAX_PROB_THRESHOLD:
                candidates.append({
                    "market_id": market.get("id", ""),
                    "question": market.get("question", ""),
                    "yes_price": yes_price,
                    "volume": volume,
                    "yes_token_id": yes_token.get("token_id", ""),
                    "end_date": market.get("endDate", ""),
                })

        except Exception:
            continue

    # Сортируем по объёму (сначала самые популярные)
    candidates.sort(key=lambda x: x["volume"], reverse=True)
    return candidates


# ─────────────────────────────────────────────
# PAPER TRADING
# ─────────────────────────────────────────────

def place_paper_bet(wallet: PaperWallet, market: dict) -> Optional[PaperBet]:
    """Симулировать ставку в paper mode."""
    if wallet.balance < BET_SIZE:
        print(f"  [!] Недостаточно средств: ${wallet.balance:.2f}")
        return None

    price = market["yes_price"]
    shares = BET_SIZE / price  # сколько акций купим за BET_SIZE $

    bet = PaperBet(
        market_id=market["market_id"],
        question=market["question"],
        outcome="YES",
        price=price,
        amount=BET_SIZE,
        shares=shares,
        timestamp=datetime.now().isoformat(),
    )

    wallet.balance -= BET_SIZE
    wallet.total_bet += BET_SIZE
    wallet.bets.append(bet)
    return bet


def check_resolved_markets(wallet: PaperWallet):
    """
    Проверяем завершённые рынки и обновляем баланс.
    В paper mode запрашиваем реальный результат через API.
    """
    unresolved = [b for b in wallet.bets if not b.resolved]
    if not unresolved:
        return

    for bet in unresolved:
        try:
            resp = requests.get(
                f"{GAMMA_API}/markets/{bet.market_id}",
                timeout=5
            )
            if resp.status_code != 200:
                continue

            data = resp.json()
            resolved = data.get("resolved", False)
            if not resolved:
                continue

            # Определяем победителя
            tokens = data.get("tokens", [])
            yes_token = next((t for t in tokens if t.get("outcome", "").upper() == "YES"), None)
            if not yes_token:
                continue

            winner = yes_token.get("winner", False)
            bet.resolved = True
            bet.won = bool(winner)

            if bet.won:
                payout = bet.shares * 1.0  # каждая акция = $1
                bet.payout = payout
                wallet.balance += payout
                wallet.total_won += payout
                print(f"  🎉 ВЫИГРЫШ! {bet.question[:60]}...")
                print(f"     Поставили ${bet.amount:.2f} → Получили ${payout:.2f} (+{((payout/bet.amount)-1)*100:.0f}%)")
            else:
                wallet.total_lost += bet.amount
                print(f"  ❌ Проигрыш: {bet.question[:60]}...")

        except Exception:
            continue


# ─────────────────────────────────────────────
# ЛОГИРОВАНИЕ
# ─────────────────────────────────────────────

def save_log(wallet: PaperWallet):
    data = {
        "timestamp": datetime.now().isoformat(),
        "balance": wallet.balance,
        "profit": wallet.profit,
        "total_bet": wallet.total_bet,
        "total_won": wallet.total_won,
        "total_lost": wallet.total_lost,
        "win_rate": wallet.win_rate,
        "bets_count": len(wallet.bets),
        "bets": [
            {
                "market_id": b.market_id,
                "question": b.question,
                "price": b.price,
                "amount": b.amount,
                "shares": round(b.shares, 2),
                "timestamp": b.timestamp,
                "resolved": b.resolved,
                "won": b.won,
                "payout": b.payout,
            }
            for b in wallet.bets
        ],
    }
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def print_status(wallet: PaperWallet, scan_num: int):
    resolved = [b for b in wallet.bets if b.resolved]
    active   = [b for b in wallet.bets if not b.resolved]

    print(f"\n{'─'*55}")
    print(f"  📊 СТАТУС — Скан #{scan_num} | {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'─'*55}")
    print(f"  💰 Баланс:       ${wallet.balance:>10.2f}")
    print(f"  📈 Прибыль:      ${wallet.profit:>+10.2f}")
    print(f"  🎯 Ставок всего: {len(wallet.bets)}")
    print(f"  ✅ Активных:     {len(active)}")
    print(f"  🏁 Завершено:    {len(resolved)}")
    if resolved:
        won = sum(1 for b in resolved if b.won)
        print(f"  🏆 Побед:        {won}/{len(resolved)} ({wallet.win_rate:.1f}%)")
    print(f"{'─'*55}")


# ─────────────────────────────────────────────
# ГЛАВНЫЙ ЦИКЛ
# ─────────────────────────────────────────────

def main():
    print("=" * 55)
    print("  🤖 POLYMARKET BOT — PAPER MODE")
    print(f"  Стратегия: Low-Prob YES (вероятность < {MAX_PROB_THRESHOLD*100:.0f}%)")
    print(f"  Стартовый баланс: ${STARTING_BALANCE:.2f}")
    print(f"  Размер ставки: ${BET_SIZE:.2f}")
    print(f"  Интервал сканирования: {SCAN_INTERVAL_SEC}с")
    print("=" * 55)
    print("  ⚠️  PAPER MODE: реальных денег не тратится!")
    print("=" * 55)

    wallet = PaperWallet()
    scan_num = 0
    seen_markets = set()  # чтобы не ставить дважды на один рынок

    while True:
        scan_num += 1
        print(f"\n🔍 Скан #{scan_num} — ищем рынки...")

        # 1. Проверяем завершённые ставки
        check_resolved_markets(wallet)

        # 2. Получаем список рынков
        markets = fetch_markets(limit=200)
        if not markets:
            print("  [!] Рынки не получены, пробуем снова...")
            time.sleep(SCAN_INTERVAL_SEC)
            continue

        print(f"  Получено {len(markets)} рынков")

        # 3. Фильтруем по стратегии
        candidates = find_low_prob_markets(markets)
        new_candidates = [c for c in candidates if c["market_id"] not in seen_markets]
        print(f"  Найдено кандидатов: {len(candidates)} (новых: {len(new_candidates)})")

        # 4. Делаем ставки
        bets_made = 0
        for market in new_candidates[:MAX_BETS_PER_SCAN]:
            if bets_made >= MAX_BETS_PER_SCAN:
                break

            print(f"\n  📌 Ставка: {market['question'][:65]}...")
            print(f"     YES цена: {market['yes_price']*100:.2f}% | Объём: ${market['volume']:.0f}")

            bet = place_paper_bet(wallet, market)
            if bet:
                seen_markets.add(market["market_id"])
                shares_display = f"{bet.shares:.0f}"
                potential = bet.shares * 1.0
                print(f"     💵 Поставили ${BET_SIZE:.2f} → {shares_display} акций")
                print(f"     🎯 Потенциальный выигрыш: ${potential:.2f} (+{((potential/BET_SIZE)-1)*100:.0f}%)")
                bets_made += 1

        if bets_made == 0:
            print("  — Новых подходящих рынков не найдено")

        # 5. Статус и лог
        print_status(wallet, scan_num)
        save_log(wallet)
        print(f"\n  ⏱ Следующий скан через {SCAN_INTERVAL_SEC}с... (Ctrl+C для остановки)")
        time.sleep(SCAN_INTERVAL_SEC)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  🛑 Бот остановлен.")
        print(f"  Лог сохранён в {LOG_FILE}")
