# local_fix.py - добавьте в начало tbank_client.py
import os
import grpc

# Отключаем проверку SSL для локального запуска
os.environ['GRPC_SSL_VERIFY'] = '0'

# Патчим secure_channel на insecure_channel
_original_secure_channel = grpc.secure_channel

def _patched_secure_channel(*args, **kwargs):
    target = args[0] if args else kwargs.get('target', 'invest-public-api.tbank.ru:443')
    print(f"🔓 [LOCAL] Using insecure channel to {target}")
    return grpc.insecure_channel(target)

grpc.secure_channel = _patched_secure_channel
print("✅ Local SSL fix applied")