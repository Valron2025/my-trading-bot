# ============================================
# PROCFILE FOR RENDER
# ============================================
# Файл называется app.py (с маленькой буквы)
# ============================================

web: gunicorn app:app --bind 0.0.0.0:${PORT:-5000} --workers 1 --threads 2 --timeout 120 --access-logfile - --error-logfile -