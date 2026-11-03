from app import create_app, db
from app.models.categoria import Categoria


app = create_app()

def seed_categorias():
    #Insere categorias padrão se o banco estiver vazio
    if Categoria.query.count() == 0:
        categorias_padrao = [
            'Ingredientes',
            'Embalagens',
            'Transporte',
            'Contas Fixas',
            'Equipamentos',
            'Marketing',
            'Outros'
        ]
        for nome in categorias_padrao:
            db.session.add(Categoria(nome_categoria=nome))
        db.session.commit()
        print('Categorias padrao criadas.')
    # Fecha a sessão após o seed para liberar a conexão com o banco
    db.session.remove()


import os
import threading
import webbrowser


def abrir_navegador():
    try:
        webbrowser.open('http://127.0.0.1:5000')
    except Exception:
        pass


with app.app_context():
    try:
        seed_categorias()
    except Exception as e:
        print(f"Seed categorias aviso: {e}")

def _aquecer_ocr():
    try:
        from app.services import planilha_produto_service as svc
        svc._obter_ocr_engine()
    except Exception as e:
        print(f"Aquecimento OCR aviso: {e}")


if os.environ.get('OCR_AQUECER') == '1':
    threading.Thread(target=_aquecer_ocr, daemon=True).start()


if __name__ == '__main__':
    # Abre o navegador automaticamente
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        threading.Timer(1.5, abrir_navegador).start()
    app.run(host='127.0.0.1', port=5000, debug=False)
