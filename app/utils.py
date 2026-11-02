from functools import wraps
from flask import session, redirect, url_for, request, flash


def login_required(f):
    """Decorator que protege rotas, exigindo que o usuário esteja autenticado."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'id_usuario' not in session:
            return redirect(url_for('auth.login', next=request.path))

        # Cookie antigo: o usuário da sessão pode não existir mais no banco
        try:
            from app import db
            from app.models.usuario import Usuario
            existe = db.session.get(Usuario, session['id_usuario']) is not None
        except Exception:
            existe = True  # se a checagem falhar, mantém o comportamento anterior

        if not existe:
            session.clear()
            flash('Sua sessão expirou. Entre novamente.', 'warning')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated