import json
import logging
import math
import os
import time
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

import requests
from requests.adapters import HTTPAdapter
from sqlalchemy import text
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# Série 433: IPCA - Variação mensal (%) do SGS / Banco Central do Brasil
BCB_SERIE_IPCA_MENSAL = 433
BCB_BASE_URL = (
    f"https://api.bcb.gov.br/dados/serie/"
    f"bcdata.sgs.{BCB_SERIE_IPCA_MENSAL}/dados"
)

BCB_TIMEOUT = (3, 6)
QTD_MESES_HISTORICO = 12

# Cache de 24 horas (memória e banco)
CACHE_TTL_SEGUNDOS = int(
    os.getenv("IPCA_CACHE_TTL", str(24 * 3600))
)

# O cache persistente fica no Neon (Postgres), na tabela ipca_cache:
#
#   CREATE TABLE IF NOT EXISTS ipca_cache (
#       id              SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
#       timestamp_epoch DOUBLE PRECISION NOT NULL,
#       atualizado_em   TEXT,
#       historico       JSONB NOT NULL
#   );

MODALIDADES_VALIDAS = (
    "ultimo_mes",
    "12_meses",
    "ano_atual",
    "personalizado",
)

# Limites para o índice digitado manualmente (em %)
TAXA_MANUAL_MIN = -50.0
TAXA_MANUAL_MAX = 100.0

NOMES_MESES = {
    1: "Janeiro",
    2: "Fevereiro",
    3: "Março",
    4: "Abril",
    5: "Maio",
    6: "Junho",
    7: "Julho",
    8: "Agosto",
    9: "Setembro",
    10: "Outubro",
    11: "Novembro",
    12: "Dezembro",
}

# ATENÇÃO: base de contingência usada somente se a API e o cache falharem.
# Os valores precisam ser conferidos contra a série oficial.
# Por padrão, o reajuste não utiliza esta base.
FALLBACK_HISTORICO = [
    {"data": "01/10/2025", "valor": 0.24, "rotulo": "Out/2025", "mes_nome": "Outubro", "ano": 2025, "mes": 10},
    {"data": "01/11/2025", "valor": 0.28, "rotulo": "Nov/2025", "mes_nome": "Novembro", "ano": 2025, "mes": 11},
    {"data": "01/12/2025", "valor": 0.56, "rotulo": "Dez/2025", "mes_nome": "Dezembro", "ano": 2025, "mes": 12},
    {"data": "01/01/2026", "valor": 0.42, "rotulo": "Jan/2026", "mes_nome": "Janeiro", "ano": 2026, "mes": 1},
    {"data": "01/02/2026", "valor": 0.83, "rotulo": "Fev/2026", "mes_nome": "Fevereiro", "ano": 2026, "mes": 2},
    {"data": "01/03/2026", "valor": 0.16, "rotulo": "Mar/2026", "mes_nome": "Março", "ano": 2026, "mes": 3},
    {"data": "01/04/2026", "valor": 0.38, "rotulo": "Abr/2026", "mes_nome": "Abril", "ano": 2026, "mes": 4},
    {"data": "01/05/2026", "valor": 0.46, "rotulo": "Mai/2026", "mes_nome": "Maio", "ano": 2026, "mes": 5},
    {"data": "01/06/2026", "valor": 0.21, "rotulo": "Jun/2026", "mes_nome": "Junho", "ano": 2026, "mes": 6},
    {"data": "01/07/2026", "valor": 0.07, "rotulo": "Jul/2026", "mes_nome": "Julho", "ano": 2026, "mes": 7},
    {"data": "01/08/2026", "valor": -0.32, "rotulo": "Ago/2026", "mes_nome": "Agosto", "ano": 2026, "mes": 8},
    {"data": "01/09/2026", "valor": 0.82, "rotulo": "Set/2026", "mes_nome": "Setembro", "ano": 2026, "mes": 9},
]

_CHAVES_ITEM = (
    "data",
    "ano",
    "mes",
    "mes_nome",
    "rotulo",
    "valor",
)


class IndiceIndisponivelError(ValueError):
    """Não foi possível obter um IPCA confiável para aplicar o reajuste."""


def _arredondar(valor: Decimal, casas: int) -> Decimal:
    """Arredonda meio para cima (padrão comercial)."""
    return valor.quantize(
        Decimal(1).scaleb(-casas),
        rounding=ROUND_HALF_UP,
    )


def _engine():
    """Engine do banco (Neon). Import tardio evita import circular.

    AJUSTE o import abaixo conforme o seu projeto.
    """
    from app import db

    return db.engine


def _criar_sessao() -> requests.Session:
    sessao = requests.Session()

    retry = Retry(
        total=1,
        connect=1,
        read=1,
        status=1,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        respect_retry_after_header=True,
    )

    sessao.mount(
        "https://",
        HTTPAdapter(max_retries=retry),
    )

    sessao.headers.update({
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Cache-Control": "no-cache",
    })

    return sessao


def _variacoes_consulta() -> list[tuple[str, dict]]:
    """Três formas diferentes de pedir o mesmo dado ao BACEN."""
    hoje = datetime.now()
    inicio = hoje - timedelta(days=550)  # folga p/ garantir 12 meses

    intervalo = {
        "formato": "json",
        "dataInicial": inicio.strftime("%d/%m/%Y"),
        "dataFinal": hoje.strftime("%d/%m/%Y"),
    }

    return [
        # 1. URL canônica, sem parâmetros extras
        (
            f"{BCB_BASE_URL}/ultimos/{QTD_MESES_HISTORICO}",
            {"formato": "json"},
        ),
        # 2. Endpoint por intervalo de datas
        (BCB_BASE_URL, intervalo),
        # 3. Intervalo + cache-buster
        (BCB_BASE_URL, {**intervalo, "_": str(time.time_ns())}),
    ]


class BacenService:
    _MEM_CACHE: dict | None = None

    # --------------------------------------------------------------
    # Utilitários
    # --------------------------------------------------------------

    @staticmethod
    def _converter_data(data_str: str) -> datetime:
        return datetime.strptime(data_str, "%d/%m/%Y")

    @classmethod
    def _montar_item(cls, data_str, valor_bruto) -> dict:
        """Converte um item da API no formato interno."""
        dt = cls._converter_data(data_str)
        valor = float(valor_bruto)

        if not math.isfinite(valor):
            raise ValueError(
                f"Valor não finito: {valor_bruto!r}"
            )

        nome_mes = NOMES_MESES.get(
            dt.month,
            str(dt.month),
        )

        return {
            "data": data_str,
            "ano": dt.year,
            "mes": dt.month,
            "mes_nome": nome_mes,
            "rotulo": f"{nome_mes[:3]}/{dt.year}",
            "valor": valor,
        }

    @classmethod
    def _validar_historico(cls, historico) -> list[dict] | None:
        """
        Valida e ordena o histórico.

        Retorna os últimos 12 meses consecutivos ou None
        se os dados estiverem incompletos, duplicados ou corrompidos.
        """

        if not isinstance(historico, list):
            return None

        try:
            for item in historico:

                if not isinstance(item, dict):
                    return None

                if not all(
                    chave in item
                    for chave in _CHAVES_ITEM
                ):
                    return None

                ano = int(item["ano"])
                mes = int(item["mes"])
                valor = float(item["valor"])

                if ano < 1900 or mes < 1 or mes > 12:
                    return None

                if not math.isfinite(valor):
                    return None

                data = datetime.strptime(
                    item["data"],
                    "%d/%m/%Y",
                )

                # Confere se data, ano e mês são compatíveis.
                if data.year != ano or data.month != mes:
                    return None

                # Confere se o nome do mês corresponde à data.
                if item["mes_nome"] != NOMES_MESES[mes]:
                    return None

            ordenado = sorted(
                historico,
                key=lambda item: (
                    int(item["ano"]),
                    int(item["mes"]),
                ),
            )

        except (TypeError, ValueError, KeyError):
            return None

        # Verifica duplicidade.
        meses = [
            (
                int(item["ano"]),
                int(item["mes"]),
            )
            for item in ordenado
        ]

        if len(meses) != len(set(meses)):
            return None

        # Mantém somente os últimos 12 meses.
        ordenado = ordenado[-QTD_MESES_HISTORICO:]

        if len(ordenado) != QTD_MESES_HISTORICO:
            return None

        # Verifica se os 12 meses são consecutivos.
        for anterior, atual in zip(
            ordenado,
            ordenado[1:],
        ):

            indice_anterior = (
                int(anterior["ano"]) * 12
                + int(anterior["mes"])
            )

            indice_atual = (
                int(atual["ano"]) * 12
                + int(atual["mes"])
            )

            if indice_atual != indice_anterior + 1:
                return None

        return ordenado

    # --------------------------------------------------------------
    # Cache persistente (Neon / Postgres)
    # --------------------------------------------------------------

    @classmethod
    def _carregar_cache_disco(cls) -> dict | None:
        """Lê o cache do Neon e só o devolve se o histórico estiver íntegro."""

        try:
            with _engine().connect() as conn:
                linha = conn.execute(
                    text(
                        "SELECT timestamp_epoch, atualizado_em, historico "
                        "FROM ipca_cache WHERE id = 1"
                    )
                ).mappings().first()

            if not linha:
                return None

            historico = cls._validar_historico(
                linha["historico"]
            )

            if historico is None:
                logger.warning(
                    "Cache do IPCA no banco incompleto ou corrompido. "
                    "Ignorando."
                )
                return None

            timestamp = float(
                linha["timestamp_epoch"] or 0
            )

            if not math.isfinite(timestamp):
                return None

            return {
                "timestamp": timestamp,
                "atualizado_em": linha["atualizado_em"],
                "historico": historico,
            }

        except Exception as erro:
            logger.warning(
                "Não foi possível ler cache do IPCA no banco: %s",
                erro,
            )
            return None

    @classmethod
    def _salvar_cache_disco(cls, dados: dict) -> None:
        """Upsert atômico da única linha de cache."""

        try:
            with _engine().begin() as conn:
                conn.execute(
                    text(
                        """
                        INSERT INTO ipca_cache
                            (id, timestamp_epoch, atualizado_em, historico)
                        VALUES
                            (1, :ts, :atualizado, CAST(:historico AS JSONB))
                        ON CONFLICT (id) DO UPDATE SET
                            timestamp_epoch = EXCLUDED.timestamp_epoch,
                            atualizado_em   = EXCLUDED.atualizado_em,
                            historico       = EXCLUDED.historico
                        """
                    ),
                    {
                        "ts": dados["timestamp"],
                        "atualizado": dados.get("atualizado_em"),
                        "historico": json.dumps(
                            dados["historico"],
                            ensure_ascii=False,
                        ),
                    },
                )

        except Exception as erro:
            logger.warning(
                "Não foi possível gravar cache do IPCA no banco: %s",
                erro,
            )

    # --------------------------------------------------------------
    # Consulta à API
    # --------------------------------------------------------------

    @classmethod
    def _consultar_api(cls) -> list[dict]:
        """Consulta a Série 433 e devolve 12 meses válidos."""

        logger.info(
            "Consultando API do Banco Central para IPCA (Série %s)...",
            BCB_SERIE_IPCA_MENSAL,
        )

        dados = None
        variacoes = _variacoes_consulta()

        for tentativa, (url, params) in enumerate(variacoes, start=1):

            # Sessão nova a cada tentativa: nada reaproveitado.
            with _criar_sessao() as sessao:
                try:
                    with sessao.get(
                        url,
                        params=params,
                        timeout=BCB_TIMEOUT,
                    ) as resposta:

                        logger.info(
                            "IPCA | HTTP | tentativa=%s | status=%s | "
                            "content_type=%s | tamanho=%s",
                            tentativa,
                            resposta.status_code,
                            resposta.headers.get("Content-Type"),
                            len(resposta.content),
                        )

                        if resposta.status_code == 304:
                            # Diagnóstico: quem respondeu e o que enviamos?
                            logger.warning(
                                "IPCA | 304 na tentativa %s | url=%s | "
                                "req_condicional=%s | server=%s | via=%s | "
                                "age=%s | etag=%s | x-cache=%s",
                                tentativa,
                                resposta.url,
                                [
                                    h
                                    for h in resposta.request.headers
                                    if h.lower().startswith("if-")
                                ],
                                resposta.headers.get("Server"),
                                resposta.headers.get("Via"),
                                resposta.headers.get("Age"),
                                resposta.headers.get("ETag"),
                                resposta.headers.get("X-Cache"),
                            )
                            continue

                        resposta.raise_for_status()

                        if not resposta.text.strip():
                            raise ValueError(
                                "O Banco Central retornou uma resposta vazia."
                            )

                        try:
                            dados = resposta.json()

                        except requests.exceptions.JSONDecodeError as erro:
                            logger.error(
                                "IPCA | Resposta não é JSON. "
                                "Conteúdo recebido: %r",
                                resposta.text[:300],
                            )

                            raise ValueError(
                                "O Banco Central retornou conteúdo "
                                "que não é JSON."
                            ) from erro

                        break

                except requests.RequestException:
                    logger.exception(
                        "IPCA | Falha na tentativa %s de consulta ao BACEN.",
                        tentativa,
                    )

                    if tentativa == len(variacoes):
                        raise

        if dados is None:
            raise ValueError(
                "O Banco Central continuou retornando HTTP 304 "
                "ou não forneceu dados."
            )

        if not isinstance(dados, list) or not dados:
            raise ValueError(
                f"Resposta vazia ou inválida da API do BCB: "
                f"{str(dados)[:200]}"
            )

        itens = []

        for item in dados:
            try:
                itens.append(
                    cls._montar_item(
                        item["data"],
                        item["valor"],
                    )
                )

            except (KeyError, TypeError, ValueError) as erro:
                logger.warning(
                    "IPCA | Item inválido da API ignorado: %r (%s)",
                    item,
                    erro,
                )

        historico = cls._validar_historico(itens)

        if historico is None:
            raise ValueError(
                "A API do BCB não retornou 12 meses "
                "consecutivos e válidos de IPCA."
            )

        logger.info(
            "IPCA | Consulta concluída | %s meses válidos | "
            "período: %s até %s",
            len(historico),
            historico[0]["rotulo"],
            historico[-1]["rotulo"],
        )

        return historico

    # --------------------------------------------------------------
    # Obtenção do histórico e controle de cache
    # --------------------------------------------------------------

    @classmethod
    def _obter_historico_com_meta(
        cls,
        forcar_api: bool = False,
    ) -> tuple[list[dict], str, str | None]:
        """
        Retorna (historico, fonte, atualizado_em).

        Utiliza cache recente quando disponível.
        Se a API falhar, utiliza o cache válido, mesmo vencido.
        """

        agora = time.time()
        cache = None

        if not forcar_api:

            # 1. Cache em memória
            if (
                cls._MEM_CACHE
                and (
                    agora
                    - cls._MEM_CACHE.get("timestamp", 0)
                ) < CACHE_TTL_SEGUNDOS
            ):
                logger.info(
                    "IPCA | Usando cache em memória recente (%s).",
                    cls._MEM_CACHE.get("atualizado_em"),
                )

                return (
                    cls._MEM_CACHE["historico"],
                    "cache_local",
                    cls._MEM_CACHE["atualizado_em"],
                )

            # 2. Cache no banco
            cache = cls._carregar_cache_disco()

            if (
                cache
                and (
                    agora
                    - cache.get("timestamp", 0)
                ) < CACHE_TTL_SEGUNDOS
            ):
                cls._MEM_CACHE = cache

                logger.info(
                    "IPCA | Usando cache do banco recente (%s).",
                    cache.get("atualizado_em"),
                )

                return (
                    cache["historico"],
                    "cache_local",
                    cache["atualizado_em"],
                )

        else:
            # Mesmo forçando a API, mantém o cache como contingência.
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

            return (
                historico,
                "api_bacen",
                atualizado_em,
            )

        except (requests.RequestException, ValueError) as erro:
            logger.warning(
                "API do Banco Central indisponível ou inválida: %s.",
                erro,
            )

        # 3. Cache válido, mesmo que esteja vencido.
        if cache:
            logger.warning(
                "IPCA | Utilizando cache local como contingência."
            )

            cls._MEM_CACHE = cache

            return (
                cache["historico"],
                "cache_local",
                cache["atualizado_em"],
            )

        # 4. Sem cache válido.
        logger.error(
            "IPCA | Nenhum cache válido disponível. "
            "Utilizando referência."
        )

        return (
            FALLBACK_HISTORICO,
            "referencia",
            None,
        )

    @classmethod
    def obter_historico_ipca(
        cls,
        forcar_api: bool = False,
    ) -> tuple[list[dict], str]:
        """
        Obtém o histórico do IPCA.

        Retorna:
        (lista_de_itens, origem)
        """

        historico, fonte, _ = cls._obter_historico_com_meta(
            forcar_api
        )

        return historico, fonte

    # --------------------------------------------------------------
    # Cálculos
    # --------------------------------------------------------------

    @classmethod
    def _acumulado_decimal(
        cls,
        lista_valores: list[float],
    ) -> Decimal:
        """Inflação acumulada composta, em %, sem arredondar."""

        fator = Decimal(1)

        for taxa in lista_valores:
            fator *= (
                Decimal(1)
                + Decimal(str(taxa)) / Decimal(100)
            )

        return (fator - Decimal(1)) * Decimal(100)

    @classmethod
    def calcular_acumulado(
        cls,
        lista_valores: list[float],
    ) -> float:
        """
        Calcula a inflação acumulada composta.
        Retorna o percentual acumulado com 4 casas decimais.
        """

        return float(
            _arredondar(
                cls._acumulado_decimal(lista_valores),
                4,
            )
        )

    @classmethod
    def obter_resumo_ipca(
        cls,
        forcar_atualizacao: bool = False,
    ) -> dict:
        """
        Retorna os principais indicadores do IPCA:
        - Último mês divulgado
        - Acumulado nos últimos 12 meses
        - Acumulado no ano corrente
        - Histórico recente
        - Fonte e data da última sincronização
        """

        historico, fonte, atualizado_em = (
            cls._obter_historico_com_meta(
                forcar_atualizacao
            )
        )

        ultimo_mes = historico[-1]

        # Arredonda uma única vez, a partir do valor exato.
        acumulado_12m = _arredondar(
            cls._acumulado_decimal(
                [mes["valor"] for mes in historico]
            ),
            2,
        )

        ano_corrente = ultimo_mes["ano"]

        meses_ano = [
            mes["valor"]
            for mes in historico
            if mes["ano"] == ano_corrente
        ]

        acumulado_ano = _arredondar(
            cls._acumulado_decimal(meses_ano),
            2,
        )

        aviso = None

        if fonte == "referencia":
            aviso = (
                "Não foi possível consultar o Banco Central e "
                "não há cache local. Exibindo dados de referência, "
                "que podem estar desatualizados."
            )

        return {
            "ultimo_mes": {
                "data": ultimo_mes["data"],
                "rotulo": ultimo_mes["rotulo"],
                "mes_nome": ultimo_mes["mes_nome"],
                "ano": ultimo_mes["ano"],
                "valor": ultimo_mes["valor"],
            },
            "acumulado_12m": float(acumulado_12m),
            "acumulado_ano": float(acumulado_ano),
            "ano_referencia": ano_corrente,
            "historico_recente": historico,
            "fonte": fonte,
            "confiavel": fonte != "referencia",
            "aviso": aviso,
            "atualizado_em": atualizado_em,
        }

    # --------------------------------------------------------------
    # Validação de taxa manual
    # --------------------------------------------------------------

    @staticmethod
    def _validar_taxa_manual(custom_taxa) -> float:

        if (
            custom_taxa is None
            or (
                isinstance(custom_taxa, str)
                and not custom_taxa.strip()
            )
        ):
            raise ValueError(
                "Para o reajuste personalizado, informe o percentual."
            )

        if isinstance(custom_taxa, str):
            custom_taxa = (
                custom_taxa.strip()
                .replace("%", "")
                .replace(",", ".")
            )

        try:
            taxa = float(custom_taxa)

        except (TypeError, ValueError):
            raise ValueError(
                "Percentual personalizado inválido."
            )

        if (
            not math.isfinite(taxa)
            or not (
                TAXA_MANUAL_MIN
                <= taxa
                <= TAXA_MANUAL_MAX
            )
        ):
            raise ValueError(
                f"O percentual personalizado deve estar entre "
                f"{TAXA_MANUAL_MIN:.0f}% e "
                f"{TAXA_MANUAL_MAX:.0f}%."
            )

        return taxa

    # --------------------------------------------------------------
    # Montagem do índice
    # --------------------------------------------------------------

    @staticmethod
    def _montar_indice(
        taxa: float,
        descricao: str,
        periodo: str,
        modalidade: str,
        fonte: str,
        atualizado_em: str | None,
        aviso: str | None = None,
    ) -> dict:

        return {
            "percentual": taxa,
            "fator": 1.0 + (taxa / 100.0),
            "descricao": descricao,
            "periodo": periodo,
            "modalidade": modalidade,
            "fonte": fonte,
            "atualizado_em": atualizado_em,
            "aviso": aviso,
        }

    # --------------------------------------------------------------
    # Cálculo do índice para reajuste
    # --------------------------------------------------------------

    @classmethod
    def calcular_indice_para_reajuste(
        cls,
        modalidade: str,
        custom_taxa: float = None,
        permitir_referencia: bool = False,
    ) -> dict:
        """
        Retorna o percentual de reajuste com base na modalidade:

        - ultimo_mes: taxa do último mês divulgado
        - 12_meses: acumulado dos últimos 12 meses
        - ano_atual: acumulado do ano corrente
        - personalizado: taxa fornecida manualmente

        Levanta IndiceIndisponivelError se só houver a base
        de referência, a menos que permitir_referencia=True.
        """

        if modalidade not in MODALIDADES_VALIDAS:
            raise ValueError(
                f"Modalidade de reajuste inválida: '{modalidade}'. "
                f"Use uma de: {', '.join(MODALIDADES_VALIDAS)}."
            )

        # O índice manual não depende da API.
        if modalidade == "personalizado":

            taxa = cls._validar_taxa_manual(
                custom_taxa
            )

            return cls._montar_indice(
                taxa,
                f"Índice informado manualmente: {taxa:+.2f}%",
                "Personalizado",
                modalidade,
                fonte="manual",
                atualizado_em=None,
            )

        resumo = cls.obter_resumo_ipca()

        if (
            resumo["fonte"] == "referencia"
            and not permitir_referencia
        ):
            raise IndiceIndisponivelError(
                "Não foi possível obter o IPCA atualizado "
                "do Banco Central. Tente novamente em instantes "
                "ou informe o índice manualmente."
            )

        if modalidade == "ultimo_mes":

            taxa = float(
                resumo["ultimo_mes"]["valor"]
            )

            descricao = (
                f"IPCA do último mês "
                f"({resumo['ultimo_mes']['rotulo']}): "
                f"{taxa:+.2f}%"
            )

            periodo = resumo["ultimo_mes"]["rotulo"]

        elif modalidade == "ano_atual":

            taxa = float(
                resumo["acumulado_ano"]
            )

            descricao = (
                f"IPCA acumulado em "
                f"{resumo['ano_referencia']}: "
                f"{taxa:+.2f}%"
            )

            periodo = (
                f"Ano {resumo['ano_referencia']}"
            )

        else:
            # 12 meses
            taxa = float(
                resumo["acumulado_12m"]
            )

            descricao = (
                "IPCA acumulado nos últimos 12 meses: "
                f"{taxa:+.2f}%"
            )

            periodo = "Últimos 12 meses"

        return cls._montar_indice(
            taxa,
            descricao,
            periodo,
            modalidade,
            fonte=resumo["fonte"],
            atualizado_em=resumo["atualizado_em"],
            aviso=resumo["aviso"],
        )