import json
import logging
import math
import os
import tempfile
import time
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# Série 433: IPCA - Variação mensal (%) do SGS / Banco Central do Brasil
BCB_SERIE_IPCA_MENSAL = 433
BCB_API_URL = (
    f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{BCB_SERIE_IPCA_MENSAL}"
    f"/dados/ultimos/12?formato=json"
)
BCB_TIMEOUT = (3, 6)  # (conexão, leitura) em segundos
QTD_MESES_HISTORICO = 12

# O IPCA é mensal, então um cache local recente de 24 horas sem consultar o BACEN é seguro e suficiente.
CACHE_TTL_SEGUNDOS = int(os.getenv("IPCA_CACHE_TTL", str(24 * 3600)))

# Caminho do cache em disco
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
CACHE_FILE = os.path.join(BASE_DIR, 'db', 'ipca_cache.json')

MODALIDADES_VALIDAS = ('ultimo_mes', '12_meses', 'ano_atual', 'personalizado')

# Limites para o índice digitado manualmente (em %)
TAXA_MANUAL_MIN = -50.0
TAXA_MANUAL_MAX = 100.0

NOMES_MESES = {
    1: 'Janeiro', 2: 'Fevereiro', 3: 'Março', 4: 'Abril',
    5: 'Maio', 6: 'Junho', 7: 'Julho', 8: 'Agosto',
    9: 'Setembro', 10: 'Outubro', 11: 'Novembro', 12: 'Dezembro'
}

# ATENÇÃO: base de contingência usada só se a API e o cache falharem.
# Os valores abaixo precisam ser CONFERIDOS contra a série oficial (a composição
# deles não bate com o acumulado que a API retorna hoje). Por isso, por padrão,
# o reajuste NÃO usa esta base (veja `permitir_referencia`).
FALLBACK_HISTORICO = [
    {'data': '01/10/2025', 'valor': 0.24, 'rotulo': 'Out/2025', 'mes_nome': 'Outubro', 'ano': 2025, 'mes': 10},
    {'data': '01/11/2025', 'valor': 0.28, 'rotulo': 'Nov/2025', 'mes_nome': 'Novembro', 'ano': 2025, 'mes': 11},
    {'data': '01/12/2025', 'valor': 0.56, 'rotulo': 'Dez/2025', 'mes_nome': 'Dezembro', 'ano': 2025, 'mes': 12},
    {'data': '01/01/2026', 'valor': 0.42, 'rotulo': 'Jan/2026', 'mes_nome': 'Janeiro', 'ano': 2026, 'mes': 1},
    {'data': '01/02/2026', 'valor': 0.83, 'rotulo': 'Fev/2026', 'mes_nome': 'Fevereiro', 'ano': 2026, 'mes': 2},
    {'data': '01/03/2026', 'valor': 0.16, 'rotulo': 'Mar/2026', 'mes_nome': 'Março', 'ano': 2026, 'mes': 3},
    {'data': '01/04/2026', 'valor': 0.38, 'rotulo': 'Abr/2026', 'mes_nome': 'Abril', 'ano': 2026, 'mes': 4},
    {'data': '01/05/2026', 'valor': 0.46, 'rotulo': 'Mai/2026', 'mes_nome': 'Maio', 'ano': 2026, 'mes': 5},
    {'data': '01/06/2026', 'valor': 0.21, 'rotulo': 'Jun/2026', 'mes_nome': 'Junho', 'ano': 2026, 'mes': 6},
    {'data': '01/07/2026', 'valor': 0.07, 'rotulo': 'Jul/2026', 'mes_nome': 'Julho', 'ano': 2026, 'mes': 7},
    {'data': '01/08/2026', 'valor': -0.32, 'rotulo': 'Ago/2026', 'mes_nome': 'Agosto', 'ano': 2026, 'mes': 8},
    {'data': '01/09/2026', 'valor': 0.82, 'rotulo': 'Set/2026', 'mes_nome': 'Setembro', 'ano': 2026, 'mes': 9},
]

_CHAVES_ITEM = ('data', 'ano', 'mes', 'mes_nome', 'rotulo', 'valor')


class IndiceIndisponivelError(ValueError):
    """Não foi possível obter um IPCA confiável para aplicar o reajuste.

    Herda de ValueError para que handlers existentes (`except ValueError`)
    continuem exibindo a mensagem ao usuário.
    """


def _arredondar(valor: Decimal, casas: int) -> Decimal:
    """Arredonda 'meio para cima' (padrão comercial), não 'bancário'."""
    return valor.quantize(Decimal(1).scaleb(-casas), rounding=ROUND_HALF_UP)


def _criar_sessao() -> requests.Session:
    sessao = requests.Session()
    retry = Retry(
        total=1,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    sessao.mount("https://", HTTPAdapter(max_retries=retry))
    sessao.headers.update({
        "Accept": "application/json",
        "User-Agent": "reajuste-ipca/1.0",
    })
    return sessao


class BacenService:
    _MEM_CACHE: dict | None = None

    # ------------------------------------------------------------------
    # Utilitários
    # ------------------------------------------------------------------
    @staticmethod
    def _converter_data(data_str: str) -> datetime:
        return datetime.strptime(data_str, "%d/%m/%Y")

    @classmethod
    def _montar_item(cls, data_str, valor_bruto) -> dict:
        """Converte um item da API no formato interno. Lança exceção se inválido."""
        dt = cls._converter_data(data_str)
        valor = float(valor_bruto)
        if not math.isfinite(valor):
            raise ValueError(f"valor não finito: {valor_bruto!r}")
        nome_mes = NOMES_MESES.get(dt.month, str(dt.month))
        return {
            'data': data_str,
            'ano': dt.year,
            'mes': dt.month,
            'mes_nome': nome_mes,
            'rotulo': f"{nome_mes[:3]}/{dt.year}",
            'valor': valor,
        }

    @classmethod
    def _validar_historico(cls, historico) -> list[dict] | None:
        """
        Devolve o histórico ordenado por data, limitado aos últimos 12 meses,
        ou None se estiver incompleto, com meses faltando/duplicados ou corrompido.
        """
        if not isinstance(historico, list):
            return None
        try:
            for item in historico:
                if not all(chave in item for chave in _CHAVES_ITEM):
                    return None
                if not math.isfinite(float(item['valor'])):
                    return None
            ordenado = sorted(historico, key=lambda m: (int(m['ano']), int(m['mes'])))
        except (TypeError, ValueError):
            return None

        ordenado = ordenado[-QTD_MESES_HISTORICO:]
        if len(ordenado) < QTD_MESES_HISTORICO:
            return None

        for anterior, atual in zip(ordenado, ordenado[1:]):
            idx_ant = int(anterior['ano']) * 12 + int(anterior['mes'])
            idx_atual = int(atual['ano']) * 12 + int(atual['mes'])
            if idx_atual != idx_ant + 1:
                return None
        return ordenado

    # ------------------------------------------------------------------
    # Cache em disco
    # ------------------------------------------------------------------
    @classmethod
    def _carregar_cache_disco(cls) -> dict | None:
        """Lê o cache e só o devolve se o histórico estiver íntegro."""
        try:
            if not os.path.isfile(CACHE_FILE):
                return None
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                bruto = json.load(f)
            historico = cls._validar_historico(bruto.get('historico'))
            if historico is None:
                logger.warning("Cache do IPCA existe, mas está incompleto ou corrompido. Ignorando.")
                return None
            return {
                'timestamp': float(bruto.get('timestamp', 0) or 0),
                'atualizado_em': bruto.get('atualizado_em'),
                'historico': historico,
            }
        except Exception as e:
            logger.warning(f"Não foi possível ler cache do IPCA em disco: {e}")
            return None

    @classmethod
    def _salvar_cache_disco(cls, dados: dict) -> None:
        """Gravação atômica: evita arquivo pela metade com requisições concorrentes."""
        tmp_path = None
        try:
            pasta = os.path.dirname(CACHE_FILE)
            os.makedirs(pasta, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=pasta, suffix='.tmp')
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(dados, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, CACHE_FILE)
            tmp_path = None
        except Exception as e:
            logger.warning(f"Não foi possível gravar cache do IPCA em disco: {e}")
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

        # ------------------------------------------------------------------
    # Consulta à API
    # ------------------------------------------------------------------
    
    @classmethod
    def _consultar_api(cls) -> list[dict]:
        """Consulta a série 433 e devolve 12 meses consecutivos e válidos."""
        logger.info("Consultando API do Banco Central para IPCA (Série 433)...")

        with _criar_sessao() as sessao:
            resposta = sessao.get(BCB_API_URL, timeout=BCB_TIMEOUT)

        logger.info(
            "IPCA | Resposta BACEN: status=%s, content-type=%s, tamanho=%s",
            resposta.status_code,
            resposta.headers.get("Content-Type"),
            len(resposta.content),
        )

        resposta.raise_for_status()

        if not resposta.content or not resposta.text.strip():
            raise ValueError("O Banco Central retornou uma resposta vazia.")

        try:
            dados = resposta.json()
        except requests.exceptions.JSONDecodeError as e:
            trecho = resposta.text[:300].replace("\n", " ")
            logger.error(
                "IPCA | Resposta não é JSON. Conteúdo recebido: %r",
                trecho,
            )
            raise ValueError(
                "O Banco Central retornou conteúdo que não é JSON."
            ) from e

        if not isinstance(dados, list) or not dados:
            raise ValueError(
                f"Resposta vazia ou inválida da API do BCB: {str(dados)[:200]}"
            )

        itens = []

        for item in dados:
            try:
                itens.append(cls._montar_item(item['data'], item['valor']))
            except (KeyError, TypeError, ValueError) as e:
                logger.warning(
                    "Item inválido da API ignorado: %r (%s)",
                    item,
                    e,
                )

        historico = cls._validar_historico(itens)

        if historico is None:
            raise ValueError(
                "A API do BCB não retornou 12 meses consecutivos e válidos de IPCA."
            )

        return historico


   
    @classmethod
    def _consultar_api(cls) -> list[dict]:
        """Consulta a série 433 e devolve 12 meses consecutivos e válidos."""

        logger.info(
            "Consultando API do Banco Central para IPCA (Série 433)..."
        )

        with _criar_sessao() as sessao:
            # Primeira tentativa: consulta normal.
            resposta = sessao.get(
                BCB_API_URL,
                timeout=BCB_TIMEOUT
            )
            logger.info(
                "IPCA | Diagnóstico HTTP | URL=%s | "
                "Request-Headers=%s | Response-Headers=%s",
                resposta.url,
                dict(resposta.request.headers),
                dict(resposta.headers),
            )

            logger.info(
                "IPCA | Resposta BACEN: status=%s, content-type=%s, tamanho=%s",
                resposta.status_code,
                resposta.headers.get("Content-Type"),
                len(resposta.content),
            )

            # 304 significa que o conteúdo não foi modificado.
            # Como esta consulta não envia cabeçalhos condicionais,
            # tentamos novamente sem reaproveitar a resposta 304.
            if resposta.status_code == 304:
                logger.warning(
                    "IPCA | BACEN retornou 304. Repetindo consulta sem cache condicional."
                )

                resposta.close()

                resposta = sessao.get(
                    BCB_API_URL,
                    headers={
                        "Cache-Control": "no-cache",
                        "Pragma": "no-cache",
                    },
                    timeout=BCB_TIMEOUT
                )
                logger.info(
                    "IPCA | Diagnóstico HTTP | URL=%s | "
                    "Request-Headers=%s | Response-Headers=%s",
                    resposta.url,
                    dict(resposta.request.headers),
                    dict(resposta.headers),
                )

                logger.info(
                    "IPCA | Segunda resposta BACEN: status=%s, content-type=%s, tamanho=%s",
                    resposta.status_code,
                    resposta.headers.get("Content-Type"),
                    len(resposta.content),
                )

        if resposta.status_code == 304:
            raise ValueError(
                "O Banco Central continuou retornando HTTP 304."
            )

        resposta.raise_for_status()

        if not resposta.content or not resposta.text.strip():
            raise ValueError(
                "O Banco Central retornou uma resposta vazia."
            )

        try:
            dados = resposta.json()
        except requests.exceptions.JSONDecodeError as e:
            trecho = resposta.text[:300].replace("\n", " ")

            logger.error(
                "IPCA | Resposta não é JSON. Conteúdo recebido: %r",
                trecho,
            )

            raise ValueError(
                "O Banco Central retornou conteúdo que não é JSON."
            ) from e

        if not isinstance(dados, list) or not dados:
            raise ValueError(
                f"Resposta vazia ou inválida da API do BCB: {str(dados)[:200]}"
            )

        itens = []

        for item in dados:
            try:
                itens.append(
                    cls._montar_item(item["data"], item["valor"])
                )
            except (KeyError, TypeError, ValueError) as e:
                logger.warning(
                    "Item inválido da API ignorado: %r (%s)",
                    item,
                    e,
                )

        historico = cls._validar_historico(itens)

        if historico is None:
            raise ValueError(
                "A API do BCB não retornou 12 meses consecutivos e válidos de IPCA."
            )

        return historico

    @classmethod
    def _obter_historico_com_meta(
        cls,
        forcar_api: bool = False
    ) -> tuple[list[dict], str, str | None]:
        """
        Retorna (historico, fonte, atualizado_em).

        Utiliza cache recente quando disponível.
        Se a API falhar, utiliza o cache válido antes da referência.
        """

        agora = time.time()
        cache = None

        if not forcar_api:
            # 1. Cache em memória
            if (
                cls._MEM_CACHE
                and (agora - cls._MEM_CACHE.get("timestamp", 0))
                < CACHE_TTL_SEGUNDOS
            ):
                logger.info(
                    "IPCA | Usando cache em memória recente (%s).",
                    cls._MEM_CACHE.get("atualizado_em")
                )

                return (
                    cls._MEM_CACHE["historico"],
                    "cache_local",
                    cls._MEM_CACHE["atualizado_em"]
                )

            # 2. Cache em disco
            cache = cls._carregar_cache_disco()

            if (
                cache
                and (agora - cache.get("timestamp", 0))
                < CACHE_TTL_SEGUNDOS
            ):
                cls._MEM_CACHE = cache

                logger.info(
                    "IPCA | Usando cache em disco recente (%s).",
                    cache.get("atualizado_em")
                )

                return (
                    cache["historico"],
                    "cache_local",
                    cache["atualizado_em"]
                )

        else:
            cache = cls._carregar_cache_disco()

        try:
            historico = cls._consultar_api()

            atualizado_em = datetime.now().strftime(
                "%d/%m/%Y às %H:%M"
            )

            dados_salvar = {
                "timestamp": time.time(),
                "atualizado_em": atualizado_em,
                "historico": historico,
            }

            cls._salvar_cache_disco(dados_salvar)
            cls._MEM_CACHE = dados_salvar

            return historico, "api_bacen", atualizado_em

        except (requests.RequestException, ValueError) as e:
            logger.warning(
                "API do Banco Central indisponível ou inválida: %s.",
                e
            )

        # 3. Cache válido, mesmo que esteja vencido
        if cache:
            logger.warning(
                "IPCA | Utilizando cache local como contingência."
            )

            cls._MEM_CACHE = cache

            return (
                cache["historico"],
                "cache_local",
                cache["atualizado_em"]
            )

        # 4. Sem cache válido
        logger.error(
            "IPCA | Nenhum cache válido disponível. Utilizando referência."
        )

        return FALLBACK_HISTORICO, "referencia", None


    @classmethod
    def obter_historico_ipca(cls, forcar_api: bool = False) -> tuple[list[dict], str]:
        """
        Obtém o histórico do IPCA (12 meses, ordenado do mais antigo ao mais recente).
        Retorna (lista_de_itens, origem: 'api_bacen' | 'cache_local' | 'referencia').
        """
        historico, fonte, _ = cls._obter_historico_com_meta(forcar_api)
        return historico, fonte

    # ------------------------------------------------------------------
    # Cálculos
    # ------------------------------------------------------------------
    @classmethod
    def _acumulado_decimal(cls, lista_valores: list[float]) -> Decimal:
        """Inflação acumulada composta, em %, sem arredondar."""
        fator = Decimal(1)
        for taxa in lista_valores:
            fator *= Decimal(1) + Decimal(str(taxa)) / Decimal(100)
        return (fator - Decimal(1)) * Decimal(100)

    @classmethod
    def calcular_acumulado(cls, lista_valores: list[float]) -> float:
        """
        Calcula a inflação acumulada composta:
        fator = (1 + i_1/100) * (1 + i_2/100) * ... * (1 + i_n/100)
        Retorna o percentual acumulado (4 casas decimais).
        """
        return float(_arredondar(cls._acumulado_decimal(lista_valores), 4))

    @classmethod
    def obter_resumo_ipca(cls, forcar_atualizacao: bool = False) -> dict:
        """
        Retorna os principais indicadores do IPCA:
        - Último mês divulgado
        - Acumulado nos últimos 12 meses
        - Acumulado no ano corrente
        - Histórico recente (12 meses)
        - Fonte e data real da última sincronização
        """
        historico, fonte, atualizado_em = cls._obter_historico_com_meta(forcar_atualizacao)
        ultimo_mes = historico[-1]

        # Os índices são arredondados UMA vez só, a 2 casas, a partir do valor exato.
        acumulado_12m = _arredondar(
            cls._acumulado_decimal([m['valor'] for m in historico]), 2
        )

        ano_corrente = ultimo_mes['ano']
        meses_ano = [m['valor'] for m in historico if m['ano'] == ano_corrente]
        acumulado_ano = _arredondar(cls._acumulado_decimal(meses_ano), 2)

        aviso = None
        if fonte == 'referencia':
            aviso = (
                "Não foi possível consultar o Banco Central e não há cache local. "
                "Exibindo dados de referência, que podem estar desatualizados."
            )

        return {
            'ultimo_mes': {
                'data': ultimo_mes['data'],
                'rotulo': ultimo_mes['rotulo'],
                'mes_nome': ultimo_mes['mes_nome'],
                'ano': ultimo_mes['ano'],
                'valor': ultimo_mes['valor'],
            },
            'acumulado_12m': float(acumulado_12m),
            'acumulado_ano': float(acumulado_ano),
            'ano_referencia': ano_corrente,
            'historico_recente': historico,
            'fonte': fonte,
            'confiavel': fonte != 'referencia',
            'aviso': aviso,
            'atualizado_em': atualizado_em,  # data real da última consulta ao BACEN
        }

    @staticmethod
    def _validar_taxa_manual(custom_taxa) -> float:
        if custom_taxa is None or (isinstance(custom_taxa, str) and not custom_taxa.strip()):
            raise ValueError("Para o reajuste personalizado, informe o percentual.")
        if isinstance(custom_taxa, str):
            custom_taxa = custom_taxa.strip().replace('%', '').replace(',', '.')
        try:
            taxa = float(custom_taxa)
        except (TypeError, ValueError):
            raise ValueError("Percentual personalizado inválido.")
        if not math.isfinite(taxa) or not (TAXA_MANUAL_MIN <= taxa <= TAXA_MANUAL_MAX):
            raise ValueError(
                f"O percentual personalizado deve estar entre {TAXA_MANUAL_MIN:.0f}% e {TAXA_MANUAL_MAX:.0f}%."
            )
        return taxa

    @staticmethod
    def _montar_indice(taxa: float, descricao: str, periodo: str, modalidade: str,
                       fonte: str, atualizado_em: str | None, aviso: str | None = None) -> dict:
        return {
            'percentual': taxa,
            'fator': 1.0 + (taxa / 100.0),
            'descricao': descricao,
            'periodo': periodo,
            'modalidade': modalidade,
            'fonte': fonte,
            'atualizado_em': atualizado_em,
            'aviso': aviso,
        }

    @classmethod
    def calcular_indice_para_reajuste(
        cls,
        modalidade: str,
        custom_taxa: float = None,
        permitir_referencia: bool = False,
    ) -> dict:
        """
        Retorna o percentual de reajuste com base na modalidade escolhida:
        - 'ultimo_mes': taxa do último mês divulgado
        - '12_meses': acumulado dos últimos 12 meses
        - 'ano_atual': acumulado do ano corrente
        - 'personalizado': taxa fornecida manualmente pelo usuário

        Levanta IndiceIndisponivelError se só houver a base de referência
        (a menos que `permitir_referencia=True`) e ValueError para entradas inválidas.
        O dict retornado inclui 'fonte', 'atualizado_em' e 'aviso' para a interface/relatórios.
        """
        if modalidade not in MODALIDADES_VALIDAS:
            raise ValueError(
                f"Modalidade de reajuste inválida: '{modalidade}'. "
                f"Use uma de: {', '.join(MODALIDADES_VALIDAS)}."
            )

        # O índice manual não depende da API: resolve antes, sem chamada de rede.
        if modalidade == 'personalizado':
            taxa = cls._validar_taxa_manual(custom_taxa)
            return cls._montar_indice(
                taxa, f"Índice informado manualmente: {taxa:+.2f}%",
                'Personalizado', modalidade, fonte='manual', atualizado_em=None
            )

        resumo = cls.obter_resumo_ipca()

        if resumo['fonte'] == 'referencia' and not permitir_referencia:
            raise IndiceIndisponivelError(
                "Não foi possível obter o IPCA atualizado do Banco Central. "
                "Tente novamente em instantes ou informe o índice manualmente."
            )

        if modalidade == 'ultimo_mes':
            taxa = float(resumo['ultimo_mes']['valor'])
            descricao = f"IPCA do último mês ({resumo['ultimo_mes']['rotulo']}): {taxa:+.2f}%"
            periodo = resumo['ultimo_mes']['rotulo']
        elif modalidade == 'ano_atual':
            taxa = float(resumo['acumulado_ano'])
            descricao = f"IPCA acumulado em {resumo['ano_referencia']}: {taxa:+.2f}%"
            periodo = f"Ano {resumo['ano_referencia']}"
        else:  # '12_meses'
            taxa = float(resumo['acumulado_12m'])
            descricao = f"IPCA acumulado nos últimos 12 meses: {taxa:+.2f}%"
            periodo = "Últimos 12 meses"

        return cls._montar_indice(
            taxa, descricao, periodo, modalidade,
            fonte=resumo['fonte'], atualizado_em=resumo['atualizado_em'], aviso=resumo['aviso']
        )