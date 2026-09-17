"""
Google Gemini AI Client with native Pydantic Structured Outputs.
Integrates via the official google-genai SDK for deterministic entity extraction and editorial synthesis.
"""

import logging
from typing import List, Optional, Type, TypeVar
from pydantic import BaseModel, Field

from core.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


# ==============================================================================
# 1. Models for Lead Security Qualification
# ==============================================================================
class LeadAuditResult(BaseModel):
    aprovado: bool = Field(
        description="true SOMENTE se o status for REAL e todas as validações forem positivas"
    )
    status: str = Field(
        description="REAL | GOLPE | FAKE"
    )
    score_confianca: float = Field(
        description="Score entre 0.0 e 1.0"
    )
    acao_sugerida: str = Field(
        description="avancar | descartar | bloquear | revisao_manual"
    )
    motivo: str = Field(
        description="Resumo claro da justificativa em 1 frase"
    )


# ==============================================================================
# 2. Models for Daily Editorial Topics (Pautas Jurídicas)
# ==============================================================================
class FonteNoticia(BaseModel):
    id_noticia: int
    veiculo: str
    data: str
    titulo_original: str


class PautaEditorial(BaseModel):
    id: int = Field(description="ID numérico da pauta")
    titulo: str = Field(description="Título jornalístico/editorial impactante e claro")
    angulo_editorial: str = Field(description="Tese ou gancho central que o artigo deve defender")
    fontes_relacionadas: List[FonteNoticia] = Field(description="Notícias que embasaram a pauta")
    sintese_fiel_das_materias: str = Field(description="Resumo analítico estrito dos fatos e tribunais citados")
    topicos_para_redacao: List[str] = Field(description="Pontos estruturados para o redator seguir")


class CuradoriaPautasResult(BaseModel):
    pautas: List[PautaEditorial] = Field(description="Exatamente 2 pautas editoriais consolidadas")


# ==============================================================================
# Gemini Wrapper
# ==============================================================================
class GeminiClient:
    """
    Wrapper for Google GenAI SDK with structured output enforcement.
    """

    def __init__(self, api_key: Optional[str] = None):
        from google import genai
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.client = genai.Client(api_key=self.api_key)

    def generate_structured(
        self,
        prompt: str,
        system_instruction: str,
        response_model: Type[T],
        model_name: str = "gemini-2.5-flash",
    ) -> T:
        """
        Generates content from Gemini guaranteed to adhere to the given Pydantic schema.
        """
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            response_schema=response_model,
            temperature=0.2,
        )

        response = self.client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=config,
        )

        raw_text = response.text
        if not raw_text:
            raise ValueError("Gemini returned empty response")

        return response_model.model_validate_json(raw_text)
