import logging
import os
import threading
import webbrowser

from app import create_app, db
from app.models.categoria import Categoria


app = create_app()
logger = logging.getLogger(__name__)


def registrar_versoes_ocr():
    """Registra versões sem carregar o mecanismo/modelo OCR na memória."""
    import importlib.metadata
    import platform

    logger.info("========== DIAGNOSTICO OCR ==========")
    logger.info("Python: %s", platform.python_version())

    bibliotecas = (
        "rapidocr-onnxruntime",
        "onnxruntime",
        "numpy",
        "opencv-python",
        "Pillow",
    )

    for biblioteca in bibliotecas:
        try:
            versao = importlib.metadata.version(biblioteca)
            logger.info("%s: %s", biblioteca, versao)
        except importlib.metadata.PackageNotFoundError:
            logger.warning("%s: NAO INSTALADO", biblioteca)

    logger.info("=====================================")


def seed_categorias():
    """Insere categorias padrão se o banco estiver vazio."""
    try:
        if Categoria.query.count() == 0:
            categorias_padrao = [
                "Ingredientes",
                "Embalagens",
                "Transporte",
                "Contas Fixas",
                "Equipamentos",
                "Marketing",
                "Outros",
            ]
            for nome in categorias_padrao:
                db.session.add(Categoria(nome_categoria=nome))
            db.session.commit()
            logger.info("Categorias padrão criadas.")
    finally:
        db.session.remove()


def abrir_navegador():
    """Abre o navegador apenas no desenvolvimento local."""
    try:
        webbrowser.open("http://127.0.0.1:5000")
    except Exception:
        logger.debug("Não foi possível abrir o navegador automaticamente.", exc_info=True)


registrar_versoes_ocr()

with app.app_context():
    try:
        seed_categorias()
    except Exception:
        logger.exception("Seed de categorias não concluído.")

if __name__ == "__main__":
    # O Render usa Gunicorn; este bloco serve apenas para execução local.
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        threading.Timer(1.5, abrir_navegador).start()
    app.run(host="127.0.0.1", port=5000, debug=False)
