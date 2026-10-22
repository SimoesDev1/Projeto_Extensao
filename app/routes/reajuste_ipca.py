import io
import os
import uuid
import time
import logging
from datetime import datetime
from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, session, send_file, jsonify, abort
)
from app.services.bacen_service import BacenService
from app.services.planilha_produto_service import PlanilhaProdutoService
from app.utils import login_required

logger = logging.getLogger(__name__)

reajuste_ipca_bp = Blueprint('reajuste_ipca', __name__, url_prefix='/reajuste-ipca')

ALLOWED_EXTENSIONS = {'pdf', 'xlsx', 'xls', 'csv', 'png', 'jpg', 'jpeg', 'webp'}
TMP_REAJUSTE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'tmp_reajuste')


def _garantir_diretorio_temp():
    os.makedirs(TMP_REAJUSTE_DIR, exist_ok=True)


def _limpar_arquivos_antigos():
    """Remove arquivos temporários com mais de 2 horas."""
    try:
        _garantir_diretorio_temp()
        agora = time.time()
        for f in os.listdir(TMP_REAJUSTE_DIR):
            caminho = os.path.join(TMP_REAJUSTE_DIR, f)
            if os.path.isfile(caminho):
                if agora - os.path.getmtime(caminho) > 7200:
                    os.remove(caminho)
    except Exception as e:
        logger.warning(f"Erro ao limpar temporários de reajuste: {e}")


def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@reajuste_ipca_bp.route('/', methods=['GET'])
@login_required
def index():
    """Exibe a tela de reajuste com indicadores do IPCA e área de upload."""
    try:
        resumo_ipca = BacenService.obter_resumo_ipca()
    except Exception as e:
        logger.error(f"Erro ao obter resumo IPCA: {e}")
        resumo_ipca = {
            'ultimo_mes': {'rotulo': 'Ref/Recente', 'valor': 0.82},
            'acumulado_12m': 4.58,
            'acumulado_ano': 3.95,
            'fonte': 'referencia',
            'atualizado_em': datetime.now().strftime("%d/%m/%Y às %H:%M")
        }

    resultado_previo = session.pop('reajuste_resultado', None)

    return render_template(
        'reajuste/index.html',
        resumo_ipca=resumo_ipca,
        resultado=resultado_previo
    )


@reajuste_ipca_bp.route('/processar', methods=['POST'])
@login_required
def processar():
    """Processa a planilha ou documento PDF enviado e aplica o reajuste pelo IPCA."""
    _garantir_diretorio_temp()
    _limpar_arquivos_antigos()

    if 'planilha' not in request.files:
        flash('Nenhum arquivo enviado. Selecione uma Imagem/Print (.png, .jpg), PDF (.pdf), Excel (.xlsx) ou CSV.', 'warning')
        return redirect(url_for('reajuste_ipca.index'))

    file = request.files['planilha']
    if file.filename == '':
        flash('Nenhum arquivo selecionado.', 'warning')
        return redirect(url_for('reajuste_ipca.index'))

    if not allowed_file(file.filename):
        flash('Formato inválido. Por favor envie uma Imagem/Print (.png, .jpg, .jpeg, .webp), PDF (.pdf), Excel (.xlsx, .xls) ou CSV (.csv).', 'danger')
        return redirect(url_for('reajuste_ipca.index'))

    modalidade = request.form.get('modalidade', '12_meses')
    regra_arredondamento = request.form.get('regra_arredondamento', 'padrao')
    coluna_preco_selecionada = request.form.get('coluna_preco', '').strip() or None
    mes_vigencia = request.form.get('mes_vigencia', '').strip() or None

    try:
        margem_str = request.form.get('margem_adicional', '0').replace(',', '.').strip()
        margem_adicional = float(margem_str) if margem_str else 0.0
    except ValueError:
        margem_adicional = 0.0

    taxa_manual = None
    if modalidade == 'personalizado':
        try:
            taxa_str = request.form.get('taxa_manual', '').replace(',', '.').strip()
            taxa_manual = float(taxa_str) if taxa_str else 0.0
        except ValueError:
            taxa_manual = 0.0

    try:
        resultado = PlanilhaProdutoService.processar_reajuste(
            arquivo_stream=file.stream,
            nome_arquivo=file.filename,
            modalidade=modalidade,
            margem_adicional=margem_adicional,
            regra_arredondamento=regra_arredondamento,
            coluna_preco_selecionada=coluna_preco_selecionada,
            taxa_manual=taxa_manual,
            mes_vigencia=mes_vigencia
        )

        token = uuid.uuid4().hex

        # Salva o PDF gerado
        caminho_pdf = os.path.join(TMP_REAJUSTE_DIR, f"{token}.pdf")
        with open(caminho_pdf, 'wb') as f:
            f.write(resultado['pdf_bytes'])

        # Salva também a versão Excel
        caminho_excel = os.path.join(TMP_REAJUSTE_DIR, f"{token}.xlsx")
        with open(caminho_excel, 'wb') as f:
            f.write(resultado['excel_bytes'])

        resultado_sessao = {
            'token': token,
            'nome_arquivo_pdf': resultado['nome_arquivo_pdf'],
            'nome_arquivo_excel': resultado['nome_arquivo_excel'],
            'nome_arquivo_origem': file.filename,
            'kpis': resultado['kpis'],
            'linhas_previa': resultado['linhas_previa']
        }

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            return jsonify({
                'sucesso': True,
                'token': token,
                'download_pdf_url': url_for('reajuste_ipca.download_pdf', token=token),
                'download_excel_url': url_for('reajuste_ipca.download_excel', token=token),
                'kpis': resultado['kpis'],
                'linhas_previa': resultado['linhas_previa']
            })

        session['reajuste_resultado'] = resultado_sessao
        flash('Produtos reajustados com sucesso de acordo com o IPCA oficial do Banco Central!', 'success')
        return redirect(url_for('reajuste_ipca.index'))

    except Exception as e:
        logger.exception("Erro ao processar reajuste de documento/planilha")
        msg_erro = str(e)
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            return jsonify({'sucesso': False, 'erro': msg_erro}), 400

        flash(f'Erro ao processar documento: {msg_erro}', 'danger')
        return redirect(url_for('reajuste_ipca.index'))


@reajuste_ipca_bp.route('/download/<token>', methods=['GET'])
@reajuste_ipca_bp.route('/download-pdf/<token>', methods=['GET'])
@login_required
def download_pdf(token: str):
    """Download do catálogo/tabela reajustada em formato PDF."""
    safe_token = ''.join(c for c in token if c.isalnum())
    caminho_arquivo = os.path.join(TMP_REAJUSTE_DIR, f"{safe_token}.pdf")

    if not os.path.isfile(caminho_arquivo):
        flash('O arquivo solicitado expirou ou não foi encontrado. Por favor processe novamente.', 'warning')
        return redirect(url_for('reajuste_ipca.index'))

    nome_download = f"tabela_produtos_reajustada_ipca_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
    return send_file(
        caminho_arquivo,
        as_attachment=True,
        download_name=nome_download,
        mimetype='application/pdf'
    )


@reajuste_ipca_bp.route('/download-excel/<token>', methods=['GET'])
@login_required
def download_excel(token: str):
    """Download alternativo da planilha reajustada em Excel (.xlsx)."""
    safe_token = ''.join(c for c in token if c.isalnum())
    caminho_arquivo = os.path.join(TMP_REAJUSTE_DIR, f"{safe_token}.xlsx")

    if not os.path.isfile(caminho_arquivo):
        flash('O arquivo solicitado expirou ou não foi encontrado. Por favor processe novamente.', 'warning')
        return redirect(url_for('reajuste_ipca.index'))

    nome_download = f"tabela_produtos_reajustada_ipca_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return send_file(
        caminho_arquivo,
        as_attachment=True,
        download_name=nome_download,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


@reajuste_ipca_bp.route('/modelo', methods=['GET'])
@reajuste_ipca_bp.route('/modelo-pdf', methods=['GET'])
@login_required
def baixar_modelo_pdf():
    """Gera e envia o modelo de tabela de produtos em PDF."""
    try:
        pdf_bytes = PlanilhaProdutoService.gerar_pdf_modelo()
        return send_file(
            io.BytesIO(pdf_bytes),
            as_attachment=True,
            download_name='modelo_tabela_produtos.pdf',
            mimetype='application/pdf'
        )
    except Exception as e:
        logger.error(f"Erro ao gerar modelo em PDF: {e}")
        flash('Não foi possível gerar o modelo em PDF no momento.', 'danger')
        return redirect(url_for('reajuste_ipca.index'))


@reajuste_ipca_bp.route('/modelo-excel', methods=['GET'])
@login_required
def baixar_modelo_excel():
    """Gera e envia a planilha modelo de produtos em Excel (.xlsx)."""
    try:
        excel_bytes = PlanilhaProdutoService.gerar_planilha_modelo()
        return send_file(
            io.BytesIO(excel_bytes),
            as_attachment=True,
            download_name='modelo_tabela_produtos.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        logger.error(f"Erro ao gerar modelo em Excel: {e}")
        flash('Não foi possível gerar a planilha modelo no momento.', 'danger')
        return redirect(url_for('reajuste_ipca.index'))


@reajuste_ipca_bp.route('/api/resumo', methods=['GET'])
@login_required
def api_resumo():
    """Retorna os dados oficiais mais recentes do IPCA via JSON."""
    forcar = request.args.get('forcar', '0') == '1'
    try:
        dados = BacenService.obter_resumo_ipca(forcar_atualizacao=forcar)
        return jsonify({'sucesso': True, 'dados': dados})
    except Exception as e:
        return jsonify({'sucesso': False, 'erro': str(e)}), 500