"""
SSL Fix module for Render deployment
Применяет все необходимые исправления для SSL/gRPC
"""

import os
import sys


def apply_ssl_fix():
    """Применяет все SSL фиксы перед импортом других модулей"""

    # Переменные окружения
    env_vars = {
        'GRPC_DNS_RESOLVER': 'native',
        'GRPC_SSL_CIPHER_SUITES': 'HIGH+ECDSA+HIGH',
        'GRPC_VERBOSITY': 'ERROR',
        'GRPC_TRACE': 'none',
    }

    for key, value in env_vars.items():
        os.environ.setdefault(key, value)

    # Настройка сертификатов
    try:
        import certifi
        cert_path = certifi.where()
        os.environ['SSL_CERT_FILE'] = cert_path
        os.environ['REQUESTS_CA_BUNDLE'] = cert_path
        print(f"✅ SSL fix applied: certifi at {cert_path}")
        return True
    except ImportError:
        print("⚠️ certifi not installed, checking system certificates...")

        # Проверяем системные сертификаты
        cert_paths = [
            '/etc/ssl/certs/ca-certificates.crt',
            '/etc/pki/tls/certs/ca-bundle.crt',
            '/etc/ssl/cert.pem',
        ]

        for cert_path in cert_paths:
            if os.path.exists(cert_path):
                os.environ['SSL_CERT_FILE'] = cert_path
                os.environ['REQUESTS_CA_BUNDLE'] = cert_path
                print(f"✅ Using system certificates: {cert_path}")
                return True

        print("❌ No certificates found! SSL will likely fail.")
        return False


# Автоматическое применение при импорте
apply_ssl_fix()