from functools import wraps
from flask import session, redirect, url_for


def login_required(f):
    """Decorator que protege rotas, exigindo que o usuário esteja autenticado."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'id_usuario' not in session:
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated
