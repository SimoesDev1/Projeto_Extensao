import io

import os

import math

import re

import logging

import time

from datetime import datetime

import numpy as np

import pandas as pd

import pdfplumber

from PIL import Image, ImageOps

from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

from openpyxl.utils import get_column_letter



from reportlab.lib.pagesizes import A4, landscape

from reportlab.lib import colors

from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

from reportlab.platypus import (

    SimpleDocTemplate, Table, TableStyle, Paragraph

)

from reportlab.lib.units import cm

from reportlab.pdfgen import canvas



from app.services.bacen_service import BacenService



logger = logging.getLogger(__name__)


def _memoria_rss_mb():
    """Retorna RSS atual em MB no Linux (Render), sem dependência adicional."""
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as arquivo:
            for linha in arquivo:
                if linha.startswith("VmRSS:"):
                    return round(int(linha.split()[1]) / 1024, 1)
    except (OSError, ValueError, IndexError):
        pass
    return None


def _log_memoria(etapa: str):
    memoria = _memoria_rss_mb()
    if memoria is not None:
        logger.info("MEMORIA | %s | RSS=%.1f MB", etapa, memoria)




# Instância preguiçosa do RapidOCR para processar imagens/prints locais

import threading as _threading

import hashlib

from collections import OrderedDict



_OCR_LOCK = _threading.Lock()

_OCR_INFERENCE_LOCK = _threading.Lock()

_OCR_ENGINE = None



# Cache LRU de OCR indexado por SHA-256 do arquivo (máx. 8 entradas)

_OCR_CACHE = OrderedDict()

_OCR_CACHE_LOCK = _threading.Lock()