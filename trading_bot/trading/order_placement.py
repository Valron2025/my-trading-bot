"""Размещение ордеров"""

from ..logger import info, error, warning


def _get_tbank():
    from trading_bot.api.tbank_client import tbank
    return tbank


class OrderPlacement:
    """Размещение различных типов ордеров"""

    def __init__(self, bot):
        self.bot = bot

    def place_market_order(self, figi: str, quantity: int, side: str) -> bool:
        """Размещение рыночного ордера"""
        try:
            if side.upper() == "BUY":
                success_flag = _get_tbank().buy(figi, quantity)
            else:
                success_flag = _get_tbank().sell(figi, quantity)

            if success_flag:
                info(f"✅ Рыночный ордер {side} {quantity} шт")
                return True
            else:
                error(f"❌ Не удалось разместить рыночный ордер")
                return False
        except Exception as e:
            error(f"Ошибка размещения ордера: {e}")
            return False

    def place_limit_order(self, figi: str, quantity: int, side: str, price: float) -> bool:
        """Размещение лимитного ордера"""
        try:
            success_flag = _get_tbank().place_pending_order(figi, quantity, side, price)
            if success_flag:
                info(f"✅ Лимитный ордер {side} {quantity} шт по {price:.2f}₽")
                return True
            else:
                error(f"❌ Не удалось разместить лимитный ордер")
                return False
        except Exception as e:
            error(f"Ошибка размещения лимитного ордера: {e}")
            return False

    def cancel_all_orders(self) -> dict:
        """Отмена всех активных заявок"""
        try:
            if hasattr(_get_tbank(), 'cancel_all_orders'):
                return _get_tbank().cancel_all_orders()

            # ✅ ИСПРАВЛЕНО: импортируем Client только при необходимости
            from t_tech.invest import Client
            from ..config import config

            with Client(config.tbank_token) as client:
                orders = client.orders.get_orders(account_id=_get_tbank().account_id)
                cancelled = 0
                for order in orders.orders:
                    try:
                        client.orders.cancel_order(
                            account_id=_get_tbank().account_id,
                            order_id=order.order_id
                        )
                        cancelled += 1
                    except Exception as e:
                        warning(f"Не удалось отменить заявку: {e}")
                return {"success": True, "cancelled": cancelled}
        except Exception as e:
            error(f"Ошибка отмены заявок: {e}")
            return {"success": False, "error": str(e)}