
import logging
import os
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from app.services.ai_fiscal_service import AiFiscalService
from app.services.categoria_service import CategoriaService
from app.services.comprovante_service import promover_temporario_para_comprovante
from app.services.gasto_service import GastoService
from app.services.receita_service import ReceitaService
from app.utils import login_required

logger = logging.getLogger(__name__)

ia_fiscal_bp = Blueprint("ia_fiscal", __name__, url_prefix="/ia")

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "pdf"}
FORMAS_PAGAMENTO = [
    "Pix",
    "Dinheiro",
    "Cartão de Débito",
    "Cartão de Crédito",
    "Transferência",
]
TIPOS_RECEITA = [
    "Venda",
    "Encomenda",
    "Serviço",
    "Delivery",
    "Feira/Evento",
    "Outros",
]

TMP_IA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "tmp_ia"
)


def _ensure_tmp_dir() -> None:
    os.makedirs(TMP_IA_DIR, exist_ok=True)


def allowed_file(filename: str) -> bool:
    return (
        bool(filename)
        and "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


def _preview_path(token: str, ext: str) -> str:
    safe_token = "".join(
        c for c in token if c.isalnum() or c == "-"
    )
    safe_ext = "".join(c for c in ext if c.isalnum())

    if not safe_token or safe_ext not in ALLOWED_EXTENSIONS:
        raise ValueError("Identificador de prévia inválido.")

    return os.path.join(TMP_IA_DIR, f"{safe_token}.{safe_ext}")


def _limpar_preview_temporario(ia_session: dict | None = None) -> None:
    data = ia_session if ia_session is not None else session.get("ia_preview")

    if not data:
        return

    token = data.get("file_token")
    ext = data.get("ext")

    if not token or not ext:
        return

    try:
        path = _preview_path(token, ext)

        if os.path.isfile(path):
            os.remove(path)

    except (OSError, ValueError):
        logger.exception("Não foi possível remover a prévia temporária.")


def _converter_valor(valor) -> Decimal | None:
    """Converte valor monetário brasileiro ou decimal para centavos."""
    if valor is None:
        return None

    texto = str(valor).strip().replace("R$", "").replace(" ", "")

    if not texto:
        return None

    if "," in texto and "." in texto:
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        texto = texto.replace(".", "").replace(",", ".")

    try:
        resultado = Decimal(texto).quantize(Decimal("0.01"))
        return resultado if resultado.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _obter_data_formulario() -> date | None:
    data_str = request.form.get("data", "").strip()

    try:
        return datetime.strptime(data_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


@ia_fiscal_bp.route("/")
@login_required
def upload():
    tem_chave_api = AiFiscalService.is_configurado()

    return render_template(
        "ia/upload.html",
        tem_chave_api=tem_chave_api
    )


@ia_fiscal_bp.route("/processar", methods=["POST"])
@login_required
def processar():
    if "documento" not in request.files:
        flash("Nenhum arquivo enviado.", "danger")
        return redirect(url_for("ia_fiscal.upload"))

    file = request.files["documento"]

    if not file.filename:
        flash("Selecione um arquivo de imagem ou PDF.", "danger")
        return redirect(url_for("ia_fiscal.upload"))

    if not allowed_file(file.filename):
        flash(
            "Formato não suportado. Envie PNG, JPG, JPEG, WEBP ou PDF.",
            "danger"
        )
        return redirect(url_for("ia_fiscal.upload"))

    ext = file.filename.rsplit(".", 1)[1].lower()
    file_bytes = file.read()

    logger.info(
        "UPLOAD | MIME: %s | Extensão: %s | Bytes: %d",
        file.mimetype,
        ext,
        len(file_bytes)
    )

    if not file_bytes:
        flash("O arquivo enviado está vazio.", "danger")
        return redirect(url_for("ia_fiscal.upload"))

    if len(file_bytes) > 10 * 1024 * 1024:
        flash("Arquivo muito grande (máximo 10 MB).", "danger")
        return redirect(url_for("ia_fiscal.upload"))

    mimetypes = {
        "pdf": "application/pdf",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
    }
    mimetype = mimetypes[ext]

    try:
        categorias = CategoriaService.listar_todas()
        cat_nomes = [c.nome_categoria for c in categorias]

        resultado = AiFiscalService.interpretar_documento(
            file_bytes,
            mimetype,
            cat_nomes
        )

        if not isinstance(resultado, dict):
            raise ValueError("O serviço de leitura retornou dados inválidos.")

        _ensure_tmp_dir()

        token = str(uuid.uuid4())
        path = _preview_path(token, ext)

        with open(path, "wb") as arquivo:
            arquivo.write(file_bytes)

        # Só substitui a prévia anterior depois que a nova foi criada.
        _limpar_preview_temporario()

        session["ia_preview"] = {
            "dados": resultado,
            "file_token": token,
            "ext": ext,
            "mimetype": mimetype,
            "filename": os.path.basename(file.filename),
            "origem": request.form.get("origem", "despesa"),
        }

        if resultado.get("ia_sucesso"):
            flash(
                "Documento interpretado! Revise os dados antes de confirmar.",
                "success"
            )
        else:
            erro_msg = resultado.get(
                "erro_msg",
                "Não foi possível extrair os dados automaticamente."
            )
            flash(f"⚠️ {erro_msg}", "warning")

        return redirect(url_for("ia_fiscal.revisar"))

    except Exception:
        logger.exception("Erro ao processar documento fiscal.")
        flash("Não foi possível processar o arquivo enviado.", "danger")
        return redirect(url_for("ia_fiscal.upload"))


@ia_fiscal_bp.route("/preview")
@login_required
def preview():
    from io import BytesIO

    ia_session = session.get("ia_preview")

    if not ia_session:
        abort(404)

    token = ia_session.get("file_token")
    ext = ia_session.get("ext")

    if not token or not ext:
        abort(404)

    try:
        path = _preview_path(token, ext)
    except ValueError:
        abort(404)

    if not os.path.isfile(path):
        abort(404)

    with open(path, "rb") as arquivo:
        data = BytesIO(arquivo.read())

    data.seek(0)

    return send_file(
        data,
        mimetype=ia_session.get("mimetype", "application/octet-stream"),
        download_name=ia_session.get("filename") or f"preview.{ext}"
    )


@ia_fiscal_bp.route("/revisar", methods=["GET"])
@login_required
def revisar():
    ia_session = session.get("ia_preview")

    if not ia_session:
        flash("Nenhum documento aguardando revisão.", "info")
        return redirect(url_for("ia_fiscal.upload"))

    categorias = CategoriaService.listar_todas()
    dados = ia_session.get("dados", {})

    return render_template(
        "ia/revisar.html",
        dados=dados,
        preview_uri=(
            url_for("ia_fiscal.preview")
            if ia_session.get("file_token")
            else None
        ),
        is_pdf=ia_session.get("mimetype") == "application/pdf",
        categorias=categorias,
        formas_pagamento=FORMAS_PAGAMENTO,
        tipos_receita=TIPOS_RECEITA
    )


@ia_fiscal_bp.route("/confirmar", methods=["POST"])
@login_required
def confirmar():
    ia_session = session.get("ia_preview")

    if not ia_session:
        flash("A prévia expirou. Envie o documento novamente.", "danger")
        return redirect(url_for("ia_fiscal.upload"))

    id_usuario = session.get("id_usuario")

    if not id_usuario:
        flash("Sessão inválida. Faça login novamente.", "danger")
        return redirect(url_for("ia_fiscal.upload"))

    tipo_transacao = request.form.get("tipo_transacao", "despesa")
    forma_pagamento = request.form.get("forma_pagamento", "Pix").strip()
    data_registro = _obter_data_formulario()

    if tipo_transacao not in ("despesa", "receita"):
        flash("Tipo de transação inválido.", "danger")
        return redirect(url_for("ia_fiscal.revisar"))

    if not data_registro:
        flash("Informe uma data válida para o lançamento.", "danger")
        return redirect(url_for("ia_fiscal.revisar"))

    if forma_pagamento not in FORMAS_PAGAMENTO:
        flash("Selecione uma forma de pagamento válida.", "danger")
        return redirect(url_for("ia_fiscal.revisar"))

    # Valida os dados antes de promover o comprovante.
    itens_validos = []
    valor_receita = None

    if tipo_transacao == "despesa":
        descricoes = request.form.getlist("item_descricao[]")
        valores = request.form.getlist("item_valor[]")
        categorias_ids = request.form.getlist("item_categoria[]")

        if not (
            len(descricoes) == len(valores) == len(categorias_ids)
        ):
            flash("Os dados dos itens estão incompletos.", "danger")
            return redirect(url_for("ia_fiscal.revisar"))

        categorias_validas = {
            str(c.id_categoria)
            for c in CategoriaService.listar_todas()
        }

        for desc, val, cat_id in zip(
            descricoes,
            valores,
            categorias_ids
        ):
            desc = desc.strip()
            valor = _converter_valor(val)

            if not desc or valor is None or valor <= 0:
                continue

            if str(cat_id) not in categorias_validas:
                continue

            itens_validos.append({
                "descricao": desc,
                "valor": valor,
                "id_categoria": int(cat_id)
            })

        if not itens_validos:
            flash(
                "Nenhum item válido. Confira descrições, valores e categorias.",
                "danger"
            )
            return redirect(url_for("ia_fiscal.revisar"))

        is_parcelado = request.form.get("is_parcelado") in (
            "on", "1", "true", "True"
        )

        try:
            numero_parcelas = int(
                request.form.get("numero_parcelas", 1)
            )
        except (ValueError, TypeError):
            numero_parcelas = 1

        if not is_parcelado:
            numero_parcelas = 1

        if not 1 <= numero_parcelas <= 36:
            flash("Informe um número de parcelas entre 1 e 36.", "danger")
            return redirect(url_for("ia_fiscal.revisar"))

    else:
        valor_receita = _converter_valor(
            request.form.get("receita_valor")
        )

        if valor_receita is None or valor_receita <= 0:
            flash("Valor da receita inválido.", "danger")
            return redirect(url_for("ia_fiscal.revisar"))

        tipo_rec = request.form.get("tipo_receita", "Venda")

        if tipo_rec not in TIPOS_RECEITA:
            flash("Tipo de receita inválido.", "danger")
            return redirect(url_for("ia_fiscal.revisar"))

        descricao_receita = (
            request.form.get("receita_descricao")
            or "Venda / Comprovante Fiscal"
        ).strip()

    # Promove o arquivo somente depois da validação dos dados.
    token = ia_session.get("file_token")
    ext = ia_session.get("ext")
    comprovante_nome = None

    if token and ext:
        try:
            caminho_origem = _preview_path(token, ext)
        except ValueError:
            flash("Arquivo temporário inválido. Envie novamente.", "danger")
            return redirect(url_for("ia_fiscal.upload"))

        if not os.path.isfile(caminho_origem):
            flash("O arquivo temporário expirou. Envie novamente.", "danger")
            return redirect(url_for("ia_fiscal.upload"))

        comprovante_nome = promover_temporario_para_comprovante(
            caminho_origem,
            ext
        )

        if not comprovante_nome:
            flash(
                "Não foi possível salvar o comprovante. Tente novamente.",
                "danger"
            )
            return redirect(url_for("ia_fiscal.revisar"))

    try:
        if tipo_transacao == "despesa":
            id_parcelamento = (
                str(uuid.uuid4()) if numero_parcelas > 1 else None
            )

            for item in itens_validos:
                dados_item = {
                    "valor": item["valor"],
                    "data_gasto": data_registro,
                    "forma_pagamento": forma_pagamento,
                    "descricao": item["descricao"],
                    "id_usuario": id_usuario,
                    "id_categoria": item["id_categoria"],
                    "id_parcelamento": id_parcelamento,
                    "comprovante": comprovante_nome,
                }

                if numero_parcelas > 1:
                    GastoService.criar_gasto_parcelado(
                        dados_item,
                        numero_parcelas
                    )
                else:
                    GastoService.criar_gasto(dados_item)

            total_adicionados = len(itens_validos)

        else:
            ReceitaService.criar_receita({
                "valor": valor_receita,
                "data_receita": data_registro,
                "forma_recebimento": forma_pagamento,
                "tipo_receita": tipo_rec,
                "descricao": descricao_receita,
                "comprovante": comprovante_nome,
                "id_usuario": id_usuario
            })

    except Exception:
        logger.exception("Erro ao salvar transação confirmada pela IA.")

        # Se o arquivo foi promovido mas a transação falhou, tenta removê-lo.
        if comprovante_nome:
            from app.services.comprovante_service import (
                remover_arquivo_comprovante
            )
            remover_arquivo_comprovante(comprovante_nome)

        flash(
            "Não foi possível salvar o lançamento. Confira os dados e tente novamente.",
            "danger"
        )
        return redirect(url_for("ia_fiscal.revisar"))

    _limpar_preview_temporario()
    session.pop("ia_preview", None)

    if tipo_transacao == "despesa":
        if numero_parcelas > 1:
            flash(
                f"Compra registrada! {total_adicionados} item(ns) "
                f"distribuído(s) em {numero_parcelas} parcelas.",
                "success"
            )
        else:
            flash(
                f"{total_adicionados} despesa(s) registrada(s) com sucesso!",
                "success"
            )

        return redirect(url_for("gastos.listar"))

    flash("Receita registrada com sucesso!", "success")
    return redirect(url_for("receitas.listar"))


@ia_fiscal_bp.route("/cancelar")
@login_required
def cancelar():
    origem = session.get("ia_preview", {}).get("origem", "despesa")

    _limpar_preview_temporario()
    session.pop("ia_preview", None)

    flash("Leitura descartada.", "info")

    if origem == "receita":
        return redirect(url_for("receitas.nova"))

    return redirect(url_for("gastos.novo"))
