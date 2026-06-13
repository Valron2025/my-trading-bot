#!/usr/bin/env python3
"""Минимальный web сервер для Render - с поддержкой Telegram webhook"""

import os
import logging
from flask import Flask, jsonify, request
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Глобальная ссылка на Telegram бота (устанавливается из worker.py)
_telegram_bot = None


def set_telegram_bot(bot):
    """Установка глобальной ссылки на Telegram бота"""
    global _telegram_bot
    _telegram_bot = bot


@app.route('/')
@app.route('/health')
def health_check():
    return jsonify({
        "status": "running",
        "timestamp": datetime.now().isoformat(),
        "service": "web",
        "telegram_bot": _telegram_bot is not None
    }), 200


@app.route(f'/webhook/{os.getenv("TELEGRAM_TOKEN", "")}', methods=['POST'])
def telegram_webhook():
    """Webhook для Telegram - принимает входящие сообщения"""
    try:
        data = request.get_json()
        if not data or 'message' not in data:
            return "ok", 200

        msg = data['message']
        text = msg.get('text', '')
        chat_id = msg.get('chat', {}).get('id')

        logger.info(f"📩 Telegram: {text} from {chat_id}")

        if _telegram_bot and hasattr(_telegram_bot, 'process_command'):
            # Обрабатываем команду в отдельном потоке
            import threading
            thread = threading.Thread(
                target=_telegram_bot.process_command,
                args=(text, chat_id)
            )
            thread.daemon = True
            thread.start()

        return "ok", 200
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return "ok", 200


@app.route('/status')
def status():
    return jsonify({
        "service": "web",
        "telegram_bot": _telegram_bot is not None,
        "timestamp": datetime.now().isoformat()
    }), 200


if __name__ == "__main__":
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=False)