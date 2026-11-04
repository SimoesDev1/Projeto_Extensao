import logging
import os
import uuid
from datetime import datetime, date
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, session, send_file, abort)
from app.services.ai_fiscal_service import AiFiscalService
from app.services.categoria_service import CategoriaService
from app.services.gasto_service import GastoService
from app.services.receita_service import ReceitaService
from app.utils import login_required

logger = logging.getLogger(__name__)
ia_fiscal_bp = Blueprint('ia_fiscal', __name__, url_prefix='/ia')

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'pdf'}
FORMAS_PAGAMENTO = ['Pix', 'Dinheiro', 'Cartão de Débito', 'Cartão de Crédito', 'Transferência']
TIPOS_RECEITA = ['Venda', 'Encomenda', 'Serviço', 'Delivery', 'Feira/Evento', 'Outros']

# Arquivos temporários da prévia da IA (fora do cookie de sessão)
# Caminho calculado dinamicamente a partir deste arquivo, sem caminhos absolutos fixos.
TMP_IA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'tmp_ia')
os.makedirs(TMP_IA_DIR, exist_ok=True)  # Cria na importação do módulo


def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _ensure_tmp_dir() -> None:
    """Garante que a pasta temporária exista (chamada antes de cada gravação)."""
    os.makedirs(TMP_IA_DIR, exist_ok=True)


def _preview_path(token: str, ext: str) -> str:
    safe_token = ''.join(c for c in token if c.isalnum() or c == '-')
    safe_ext = ''.join(c for c in ext if c.isalnum())
    return os.path.join(TMP_IA_DIR, f'{safe_token}.{safe_ext}')


def _limpar_preview_temporario(ia_session: dict | None = None) -> None:
    """Remove arquivo temporário associado à prévia da IA."""
    data = ia_session if ia_session is not None else session.get('ia_preview')
    if not data:
        return
    token = data.get('file_token')
    ext = data.get('ext')
    if token and ext:
        path = _preview_path(token, ext)
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError as e:
            logger.warning(f'Não foi possível remover prévia temporária: {e}')


@ia_fiscal_bp.route('/')
@login_required
def upload():
    """Tela de upload de notas fiscais e comprovantes."""
    tem_chave_api = AiFiscalService.is_configurado()
    return render_template('ia/upload.html', tem_chave_api=tem_chave_api)


@ia_fiscal_bp.route('/processar', methods=['POST'])
@login_required
def processar():
    """Recebe o arquivo e processa a extração com a IA."""
    if 'documento' not in request.files:
        flash('Nenhum arquivo enviado.', 'danger')
        return redirect(url_for('ia_fiscal.upload'))

    file = request.files['documento']
    if file.filename == '':
        flash('Selecione um arquivo de imagem ou PDF.', 'danger')
        return redirect(url_for('ia_fiscal.upload'))

    if not allowed_file(file.filename):
        flash('Formato não suportado. Envie arquivos PNG, JPG, JPEG, WEBP ou PDF.', 'danger')
        return redirect(url_for('ia_fiscal.upload'))

    file_bytes = file.read()

    # Registra informações do upload sem expor o conteúdo do documento
    logger.info(
        "UPLOAD | MIME original: %s | Extensão: %s | Bytes recebidos: %d",
        file.mimetype,
        file.filename.rsplit('.', 1)[1].lower(),
        len(file_bytes)
    )

    # Impede o processamento de arquivos vazios
    if not file_bytes:
        logger.warning("UPLOAD | Arquivo recebido sem conteúdo.")
        flash('O arquivo enviado está vazio. Selecione novamente a imagem ou PDF.', 'danger')
        return redirect(url_for('ia_fiscal.upload'))

    if len(file_bytes) > 10 * 1024 * 1024:
        logger.warning(
            "UPLOAD | Arquivo excedeu o limite. Tamanho: %d bytes",
            len(file_bytes)
        )
        flash('Arquivo muito grande (máximo 10 MB).', 'danger')
        return redirect(url_for('ia_fiscal.upload'))

    mimetype = file.mimetype or 'image/jpeg'
    ext = file.filename.rsplit('.', 1)[1].lower()
    if ext == 'pdf':
        mimetype = 'application/pdf'
    elif ext in ['jpg', 'jpeg']:
        mimetype = 'image/jpeg'
    elif ext == 'png':
        mimetype = 'image/png'
    elif ext == 'webp':
        mimetype = 'image/webp'


    # Busca categorias do usuário
    categorias = CategoriaService.listar_todas()
    cat_nomes = [c.nome_categoria for c in categorias]

    # Chama o serviço de IA
    resultado = AiFiscalService.interpretar_documento(file_bytes, mimetype, cat_nomes)

    # Descarta prévia anterior e salva o arquivo em disco (evita estourar o cookie de sessão)
    _limpar_preview_temporario()
    _ensure_tmp_dir()
    token = str(uuid.uuid4())
    path = _preview_path(token, ext)
    with open(path, 'wb') as f:
        f.write(file_bytes)

    session['ia_preview'] = {
        'dados': resultado,
        'file_token': token,
        'ext': ext,
        'mimetype': mimetype,
        'filename': file.filename,
        'origem': request.form.get('origem', 'despesa')
    }

    if resultado.get('modo_demo'):
        flash('Modo demonstração ativo: configure sua GEMINI_API_KEY no arquivo .env para análise ao vivo.', 'warning')
    elif resultado.get('ia_sucesso'):
        flash('Nota fiscal interpretada com sucesso! Revise os dados abaixo antes de confirmar.', 'success')
    else:
        # Exibe a mensagem específica retornada pelo serviço (ex.: erro do extrator ou Ollama indisponível)
        erro_msg = resultado.get(
            'erro_msg',
            'Não foi possível extrair os dados automaticamente. Por favor, preencha os campos manualmente.'
        )
        flash(f'⚠️ {erro_msg}', 'warning')

    return redirect(url_for('ia_fiscal.revisar'))


@ia_fiscal_bp.route('/preview')
@login_required
def preview():
    """Serve o arquivo temporário da prévia sem gravá-lo no cookie de sessão."""
    from io import BytesIO

    ia_session = session.get('ia_preview')
    if not ia_session:
        abort(404)

    token = ia_session.get('file_token')
    ext = ia_session.get('ext')
    mimetype = ia_session.get('mimetype', 'application/octet-stream')
    filename = ia_session.get('filename') or f'preview.{ext or "bin"}'
    if not token or not ext:
        abort(404)

    path = _preview_path(token, ext)
    if not os.path.isfile(path):
        abort(404)

    # Lê para memória para liberar o handle no Windows e permitir exclusão depois
    with open(path, 'rb') as f:
        data = BytesIO(f.read())
    data.seek(0)
    return send_file(data, mimetype=mimetype, download_name=filename)


@ia_fiscal_bp.route('/revisar', methods=['GET'])
@login_required
def revisar():
    """Tela de conferência e edição dos dados extraídos antes de salvar."""
    ia_session = session.get('ia_preview')
    if not ia_session:
        flash('Nenhum documento aguardando revisão.', 'info')
        return redirect(url_for('ia_fiscal.upload'))

    categorias = CategoriaService.listar_todas()
    dados = ia_session.get('dados', {})
    preview_uri = url_for('ia_fiscal.preview') if ia_session.get('file_token') else None

    return render_template(
        'ia/revisar.html',
        dados=dados,
        preview_uri=preview_uri,
        is_pdf=ia_session.get('mimetype') == 'application/pdf',
        categorias=categorias,
        formas_pagamento=FORMAS_PAGAMENTO,
        tipos_receita=TIPOS_RECEITA
    )


@ia_fiscal_bp.route('/confirmar', methods=['POST'])
@login_required
def confirmar():
    """Salva os lançamentos confirmados pelo usuário e persiste o comprovante original."""
    from flask import current_app
    import shutil

    id_usuario = session['id_usuario']
    tipo_transacao = request.form.get('tipo_transacao', 'despesa')
    forma_pagamento = request.form.get('forma_pagamento', 'Pix')
    data_str = request.form.get('data', date.today().strftime('%Y-%m-%d'))

    try:
        data_registro = datetime.strptime(data_str, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        data_registro = date.today()

    # Persiste permanentemente o comprovante do upload da IA de forma otimizada
    ia_session = session.get('ia_preview', {})
    token = ia_session.get('file_token')
    ext = ia_session.get('ext')
    comprovante_nome = None

    if token and ext:
        caminho_origem = _preview_path(token, ext)
        if os.path.isfile(caminho_origem):
            from app.services.comprovante_service import promover_temporario_para_comprovante
            comprovante_nome = promover_temporario_para_comprovante(caminho_origem, ext)

    if tipo_transacao == 'despesa':
        # Lança cada item como despesa (ou distribui em parcelas caso parcelado)
        descricoes = request.form.getlist('item_descricao[]')
        valores = request.form.getlist('item_valor[]')
        categorias_ids = request.form.getlist('item_categoria[]')

        is_parcelado = request.form.get('is_parcelado') in ['on', '1', 'true', 'True']
        try:
            numero_parcelas = int(request.form.get('numero_parcelas', 1))
        except (ValueError, TypeError):
            numero_parcelas = 1

        if not is_parcelado or numero_parcelas < 1:
            numero_parcelas = 1

        total_adicionados = 0
        id_parcelamento_nota = str(uuid.uuid4()) if numero_parcelas > 1 else None

        for desc, val, cat_id in zip(descricoes, valores, categorias_ids):
            desc = desc.strip()
            if not desc:
                continue
            try:
                valor_float = float(str(val).replace(',', '.'))
                id_cat = int(cat_id)
            except (ValueError, TypeError):
                continue

            if valor_float > 0 and id_cat > 0:
                dados_item = {
                    'valor': valor_float,
                    'data_gasto': data_registro,
                    'forma_pagamento': forma_pagamento,
                    'descricao': desc,
                    'id_usuario': id_usuario,
                    'id_categoria': id_cat,
                    'id_parcelamento': id_parcelamento_nota,
                    'comprovante': comprovante_nome
                }
                if numero_parcelas > 1:
                    GastoService.criar_gasto_parcelado(dados_item, numero_parcelas=numero_parcelas)
                else:
                    GastoService.criar_gasto(dados_item)

                total_adicionados += 1

        if total_adicionados == 0:
            flash('Nenhum item válido para adicionar. Verifique descrições, valores e categorias.', 'danger')
            return redirect(url_for('ia_fiscal.revisar'))

        _limpar_preview_temporario()
        session.pop('ia_preview', None)

        if numero_parcelas > 1:
            flash(
                f'Compra parcelada registrada! {total_adicionados} item(ns) com comprovante anexado distribuído(s) em {numero_parcelas} parcelas mensais '
                f'({total_adicionados * numero_parcelas} lançamentos gerados nos respectivos meses).',
                'success'
            )
        else:
            flash(f'{total_adicionados} despesa(s) categorizada(s) e adicionada(s) com comprovante anexado com sucesso!', 'success')

        return redirect(url_for('gastos.listar'))

    else:
        # Lança como receita
        descricao = request.form.get('receita_descricao', 'Venda / Comprovante Fiscal')
        tipo_rec = request.form.get('tipo_receita', 'Venda')
        try:
            valor_float = float(str(request.form.get('receita_valor', '0')).replace(',', '.'))
        except (ValueError, TypeError):
            valor_float = 0.0

        if valor_float > 0:
            ReceitaService.criar_receita({
                'valor': valor_float,
                'data_receita': data_registro,
                'forma_recebimento': forma_pagamento,
                'tipo_receita': tipo_rec,
                'descricao': descricao,
                'comprovante': comprovante_nome,
                'id_usuario': id_usuario
            })
            _limpar_preview_temporario()
            session.pop('ia_preview', None)
            flash('Receita confirmada e registrada com comprovante anexado com sucesso a partir do comprovante!', 'success')
            return redirect(url_for('receitas.listar'))
        else:
            flash('Valor da receita inválido.', 'danger')
            return redirect(url_for('ia_fiscal.revisar'))


@ia_fiscal_bp.route('/cancelar')
@login_required
def cancelar():
    """Descarta a pré-visualização atual."""
    origem = session.get('ia_preview', {}).get('origem', 'despesa')
    _limpar_preview_temporario()
    session.pop('ia_preview', None)
    flash('Leitura descartada.', 'info')
    if origem == 'receita':
        return redirect(url_for('receitas.nova'))
    return redirect(url_for('gastos.novo'))
