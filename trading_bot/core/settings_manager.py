"""Менеджер настроек бота - с сохранением в файл"""

import json
import os
import threading
from typing import Dict, Any
from pathlib import Path

from ..logger import info, success, warning, debug


class SettingsManager:
    """Управление настройками бота с сохранением в JSON"""

    DEFAULT_SETTINGS = {
        'take_profit_pct': 1.5,
        'stop_loss_pct': 1.0,
        'trailing_stop_pct': 0.5,
        'max_positions': 5,
        'min_trade_amount': 500,
        'position_size_pct': 8.0,
        'score_threshold_long': 0,
        'score_threshold_short': -10,
        'timeout_minutes': 30,
        'cycle_seconds': 5,
        'short_enabled': False
    }

    def __init__(self, config_file: str = "bot_settings.json"):
        self.config_file = Path(config_file)
        self._settings = self.DEFAULT_SETTINGS.copy()
        self._lock = threading.RLock()
        self._load()

    def _load(self):
        """Загрузка настроек из файла"""
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for key, value in data.items():
                        if key in self._settings:
                            self._settings[key] = value
                info(f"⚙️ Настройки загружены из {self.config_file}")
            except Exception as e:
                warning(f"⚠️ Ошибка загрузки настроек: {e}")

        # Применяем настройки к глобальному config
        self._apply_to_config()

    def _save(self):
        """Сохранение настроек в файл"""
        try:
            with self._lock:
                with open(self.config_file, 'w', encoding='utf-8') as f:
                    json.dump(self._settings, f, indent=2, ensure_ascii=False)
            debug(f"💾 Настройки сохранены в {self.config_file}")
        except Exception as e:
            warning(f"⚠️ Ошибка сохранения настроек: {e}")

    def _apply_to_config(self):
        """Применение настроек к глобальному config"""
        try:
            from ..config import config

            config.take_profit_pct = self._settings['take_profit_pct']
            config.stop_loss_pct = self._settings['stop_loss_pct']
            config.trailing_stop_pct = self._settings['trailing_stop_pct']
            config.max_positions = int(self._settings['max_positions'])
            config.min_trade_amount = self._settings['min_trade_amount']
            config.adaptive_position_size_pct = self._settings['position_size_pct'] / 100
            config.long_score_threshold = self._settings['score_threshold_long']
            config.short_score_threshold = self._settings['score_threshold_short']
            config.adaptive_timeout_minutes = int(self._settings['timeout_minutes'])
            config.adaptive_cycle_seconds = int(self._settings['cycle_seconds'])
            config.use_short = self._settings['short_enabled']

            debug("⚙️ Настройки применены к config")
        except Exception as e:
            warning(f"⚠️ Ошибка применения настроек: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        """Получение настройки"""
        return self._settings.get(key, default)

    def set(self, key: str, value: Any) -> bool:
        """Установка настройки"""
        if key not in self._settings:
            return False

        with self._lock:
            self._settings[key] = value
            self._save()
            self._apply_to_config()

        info(f"⚙️ Настройка {key} изменена: {value}")
        return True

    def get_all(self) -> Dict[str, Any]:
        """Получение всех настроек"""
        return self._settings.copy()

    def get_settings_text(self) -> str:
        """Форматированный текст настроек для Telegram"""
        text = f"""⚙️ <b>НАСТРОЙКИ БОТА</b>

🎯 Тейк-профит: <b>+{self._settings['take_profit_pct']:.1f}%</b>
🛑 Стоп-лосс: <b>-{self._settings['stop_loss_pct']:.1f}%</b>
🔻 Трейлинг-стоп: <b>{self._settings['trailing_stop_pct']:.2f}%</b>
📊 Макс. позиций: <b>{self._settings['max_positions']}</b>
💰 Мин. сумма сделки: <b>{self._settings['min_trade_amount']}₽</b>
📈 Размер позиции: <b>{self._settings['position_size_pct']:.0f}%</b>
🎫 Score порог LONG: <b>≥ {self._settings['score_threshold_long']}</b>
🎫 Score порог SHORT: <b>≤ {self._settings['score_threshold_short']}</b>
⏰ Таймаут позиции: <b>{self._settings['timeout_minutes']} мин</b>
🔄 Интервал цикла: <b>{self._settings['cycle_seconds']} сек</b>
🔻 SHORT торговля: <b>{'✅ ВКЛ' if self._settings['short_enabled'] else '❌ ВЫКЛ'}</b>
"""
        return text

    def reset_to_defaults(self):
        """Сброс настроек до значений по умолчанию"""
        with self._lock:
            self._settings = self.DEFAULT_SETTINGS.copy()
            self._save()
            self._apply_to_config()
        success("⚙️ Настройки сброшены до значений по умолчанию")


# Глобальный экземпляр
settings_manager = SettingsManager()