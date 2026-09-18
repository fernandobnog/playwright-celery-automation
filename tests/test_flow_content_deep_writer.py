"""
Automated unit tests for Flow: Deep Content Writer (Multichannel Editorial Engine).
Tests Pydantic schemas, EditorialResearchAgent iterative loop, Gemini structured generation,
Google Docs formatting, WhatsApp/Email delivery across all 4 channels, and Celery task execution.
"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from flows.flow_content_deep_writer import (
    ArtigoBlog,
    ArtigoLinkedIn,
    BlogMetadata,
    CenaReels,
    ComprehensionAssessment,
    ContentGenerationResult,
    EditorialResearchAgent,
    FonteCitada,
    MaterialInstagram,
    PostLinkedIn,
    PromptsImagem,
    SearchQueryPlan,
    generate_deep_content_and_deliver,
    task_deep_content_generation,
)


def test_content_generation_result_schema():
    data = {
        "tema_selecionado": "Cavalo de Troia em PDF: Os Riscos do Prompt Injection Indireto",
        "categoria": "Tecnologia da Informação (TI)",
        "dossie_pesquisa": "Pesquisa realizada em 3 fontes sobre incidentes de prompt injection e decisões regulatórias.",
        "total_fontes_analisadas": 3,
        "fontes_pesquisadas": [
            {
                "titulo": "Tribunal detecta tentativa de manipulação por texto oculto em petição",
                "url": "https://conjur.com.br/decisao-ia-peticao",
                "veiculo": "conjur.com.br",
                "contribuicao": "Uso de tinta branca invisível em petição para desviar parecer de assistente IA.",
            }
        ],
        "blog": {
            "titulo": "O Fim da Era do PDF Inofensivo: Como o Prompt Injection Indireto Desafiou os Tribunais",
            "subtitulo": "Quando uma petição deixa de ser documento e vira código malicioso executável",
            "tempo_leitura_minutos": 7,
            "corpo_markdown": (
                "## 1. O Choque de Realidade: A Trapaça da Tinta Branca\n"
                "Você já assistiu a *De Volta para o Futuro*? Imagine enviar um carro voador...\n\n"
                "## 2. Casos Reais: O Que Aconteceu nos Tribunais\n"
                "Em caso emblemático analisado pelo [ConJur](https://conjur.com.br/decisao-ia-peticao), um advogado tentou...\n\n"
                "## 3. Conceituação sem Fricção: O Cão Farejador e o Estagiário sem CPF\n"
                "Modelos de linguagem são como um estagiário ultraveloz que não tem CPF nem OAB...\n\n"
                "## 4. Framework de Sobrevivência: 4 Pilares Inegociáveis\n"
                "| Nível | Pilar | Ação Prática |\n"
                "|---|---|---|\n"
                "| 1 | Higienização | Sanitizar arquivos PDF |\n"
                "| 2 | O Advogado Maestro | Supervisão humana inegociável |\n\n"
                "## 5. Conclusão: O Humano no Volante\n"
                "A tecnologia não substitui a prudência.\n\n"
                "## Referências e Fontes Consultadas\n"
                "- [ConJur](https://conjur.com.br/decisao-ia-peticao)"
            ),
            "seo": {
                "veiculo": "fernandonogueira.dev.br",
                "slug_sugerido": "prompt-injection-indireto-seguranca-ia",
                "meta_title": "Prompt Injection Indireto: O Risco Oculto dos PDFs em 2026",
                "meta_description": "Entenda o perigo do prompt injection indireto em arquivos PDF e como proteger sistemas corporativos com governança humana.",
                "palavra_chave_foco": "prompt injection indireto",
                "palavras_chave_secundarias": ["segurança de IA", "cibersegurança corporativa", "human-in-the-loop"],
                "tempo_leitura_minutos": 7,
            },
            "fontes_citadas": [
                {
                    "titulo": "Decisão sobre IA em Petição",
                    "url": "https://conjur.com.br/decisao-ia-peticao",
                    "veiculo": "ConJur",
                    "contribuicao": "Tentativa de manipulação por texto oculto.",
                }
            ],
        },
        "linkedin": {
            "titulo_artigo": "O Fim do PDF Inofensivo: Por que Sua Empresa Precisa de um 'Advogado Maestro'",
            "subtitulo": "A fronteira entre documento e código malicioso desapareceu com os agentes de IA",
            "tempo_leitura_minutos": 4,
            "corpo_artigo_markdown": "## O Choque de Realidade\nTratar arquivos como inocentes é um erro bilionário...",
            "texto_post_divulgacao": (
                "Você ainda confia cegamente em arquivos PDF recebidos por e-mail?\n"
                "O jogo mudou drasticamente em 2026. 👇\n\n"
                "❌ O erro: achar que texto é inofensivo.\n"
                "🔹 O fato: hackers e litigantes estão usando tinta branca invisível para hipnotizar robôs de triagem.\n"
                "1️⃣ O remédio: governança estrita e humanos no volante.\n\n"
                "Como seu departamento jurídico está se preparando? Leia o artigo completo!\n\n"
                "#DireitoDigital #InteligenciaArtificial #Ciberseguranca #GovernancaCorporativa #LegalTech #Inovacao"
            ),
            "gancho_inicial": "Você ainda confia cegamente em arquivos PDF? O jogo mudou em 2026. 👇",
            "chamada_acao": "Como seu departamento jurídico e de TI está se preparando? Deixe nos comentários!",
            "hashtags": ["#DireitoDigital", "#InteligenciaArtificial", "#Ciberseguranca", "#GovernancaCorporativa", "#LegalTech", "#Inovacao"],
            "fontes_mencionadas": ["ConJur"],
            "sugestao_imagem_capa": "Documento em vidro com circuito digital carmim oculto sob a luz negra",
        },
        "instagram": {
            "legenda": (
                "🚨 SEU PDF AGORA PODE HIPNOTIZAR A IA DA SUA EMPRESA!\n\n"
                "👉 Você já ouviu falar da trapaça da tinta branca?\n"
                "🔍 Advogados e criminosos estão escondendo comandos invisíveis em arquivos comuns.\n"
                "🛡️ O resultado? Robôs de triagem enganados em segundos.\n\n"
                "Arraste para o lado e veja como se proteger! Salve este post para consultar depois. 📲"
            ),
            "hashtags": [
                "#segurancadainformacao", "#inteligenciaartificial", "#direitodigital",
                "#ciberseguranca", "#inovacaotecnologica", "#advocacia40", "#tecnologia"
            ],
            "texto_reels": (
                "| Tempo | O que aparece na tela (Cena) | O que você fala (Áudio) |\n"
                "|---|---|---|\n"
                "| 00-05s | Close no rosto com expressão chocada, tela dividida com alerta vermelho | Você sabia que um simples PDF pode sequestrar a IA da sua empresa? |\n"
                "| 06-25s | Mostra tela com texto invisível sendo revelado sob luz negra | É o chamado Cavalo de Troia em PDF. Eles usam tinta branca para dar ordens secretas ao robô! |\n"
                "| 26-45s | B-roll de tribunal e servidores com efeito de sirene e zoom | Não caia nessa armadilha. A regra de ouro é: humano no volante sempre! |"
            ),
            "cenas_reels": [
                {
                    "tempo": "00-05s",
                    "cena": "Close no rosto com expressão chocada",
                    "audio": "Você sabia que um simples PDF pode sequestrar a IA da sua empresa?",
                }
            ],
        },
        "prompts_imagem": {
            "imagem_feed_instagram_1x1": (
                "A futuristic crystalline PDF document with hidden glowing crimson (#ba2649) laser circuits "
                "under ultraviolet light, dark obsidian graphite background (#191b27), deep burgundy (#922824) shadows, "
                "pure optical white typography, volumetric studio lighting, Pixar-meets-Cyberpunk clean aesthetic, 8k render --ar 1:1"
            ),
            "imagem_reels_9x16": (
                "Vertical dynamic perspective, cascading binary code beams in vibrant carmine (#ba2649) falling past "
                "a floating metallic security seal, midnight navy dark backdrop (#191b27), sleek tech editorial --ar 9:16"
            ),
            "imagem_wordpress_16x9": (
                "Cinematic wide panoramic tech editorial cover: modern executive boardroom overlooking a holographic "
                "data courtroom, split contrast between vintage typewriter and quantum AI servers, color palette: #ba2649, #922824, #191b27 --ar 16:9"
            ),
            "paleta_hex": ["#ba2649", "#922824", "#a79f97", "#191b27", "#ffffff"],
        },
    }

    res = ContentGenerationResult.model_validate(data)
    assert res.tema_selecionado.startswith("Cavalo de Troia em PDF")
    assert res.categoria == "Tecnologia da Informação (TI)"
    assert res.total_fontes_analisadas == 3
    assert res.blog.seo.veiculo == "fernandonogueira.dev.br"
    assert res.blog.seo.palavra_chave_foco == "prompt injection indireto"
    assert "De Volta para o Futuro" in res.blog.corpo_markdown
    assert res.linkedin.titulo_artigo.startswith("O Fim do PDF Inofensivo")
    assert "👇" in res.linkedin.texto_post_divulgacao
    assert res.linkedin.texto_post == res.linkedin.texto_post_divulgacao
    assert "🚨 SEU PDF AGORA" in res.instagram.legenda
    assert "Tempo" in res.instagram.texto_reels
    assert "#ba2649" in res.prompts_imagem.imagem_feed_instagram_1x1
    assert "--ar 16:9" in res.prompts_imagem.imagem_wordpress_16x9


def test_editorial_research_agent_plan_queries():
    mock_gemini = MagicMock()
    mock_gemini.generate_structured.return_value = SearchQueryPlan(
        queries=[
            "prompt injection indireto pdf tribunal cnj",
            "seguranca ia generativa proseg ia incidentes",
        ],
        intencao_investigativa="Descobrir incidentes reais e decisões de tribunais",
    )

    agent = EditorialResearchAgent(gemini_client=mock_gemini)
    queries = agent.plan_initial_queries(
        tema="Cavalo de Troia em PDF",
        categoria="Tecnologia da Informação (TI)",
    )

    assert len(queries) == 2
    assert "prompt injection indireto pdf tribunal cnj" in queries
    mock_gemini.generate_structured.assert_called_once()


def test_editorial_research_agent_conduct_deep_research():
    mock_gemini = MagicMock()
    mock_scraper = MagicMock()
    mock_extractor = MagicMock()

    mock_gemini.generate_structured.side_effect = [
        SearchQueryPlan(
            queries=["musica ao vivo bares 2026 faturamento"],
            intencao_investigativa="Investigar faturamento de bares com música ao vivo",
        ),
        ComprehensionAssessment(
            tema_compreendido=True,
            grau_compreensao_score=0.92,
            fatos_e_dados_chave=[
                "Bares com música ao vivo faturam até 35% mais",
                "Arrecadação do ECAD cresceu em estabelecimentos comerciais",
            ],
            lacunas_identificadas=[],
        ),
    ]

    mock_scraper.search.return_value = {
        "organic_results": [
            {
                "title": "Música ao Vivo Alavanca Faturamento de Bares",
                "url": "https://abrasel.com.br/noticias/musica-bares",
                "snippet": "Pesquisa revela aumento de 35% no consumo médio...",
            }
        ]
    }

    mock_extractor.extract.return_value = {
        "status": "success",
        "metadata": {
            "title": "Música ao Vivo Alavanca Faturamento de Bares",
            "published_time": "2026-02-10",
        },
        "content": "Pesquisa da Abrasel aponta que casas com som ao vivo retêm clientes por 45 minutos a mais...",
    }

    agent = EditorialResearchAgent(
        gemini_client=mock_gemini,
        google_scraper=mock_scraper,
        content_extractor=mock_extractor,
        max_iterations=1,
    )

    result = agent.conduct_deep_research(
        tema="Música ao Vivo em Bares",
        categoria="Música & Mercado Musical",
    )

    assert result["total_analisadas"] == 1
    assert len(result["fontes"]) == 1
    assert result["fontes"][0].url == "https://abrasel.com.br/noticias/musica-bares"
    assert "abrasel.com.br" in result["fontes"][0].veiculo
    assert "PESQUISA INVESTIGATIVA REALIZADA EM 1 FONTES" in result["dossie_pesquisa"]


def test_generate_deep_content_and_deliver_multichannel():
    mock_hub = MagicMock()
    mock_evo = MagicMock()
    mock_gemini = MagicMock()
    mock_research_agent = MagicMock()

    mock_hub.docs.create_document.return_value = {"documentId": "doc-test-deep-123"}
    mock_evo.send_text_message = AsyncMock(return_value={"status": "SENT"})

    mock_research_agent.conduct_deep_research.return_value = {
        "dossie_pesquisa": "Pesquisa: Bares faturam 35% mais com música ao vivo segundo a Abrasel.",
        "fontes": [
            FonteCitada(
                titulo="Pesquisa Abrasel 2026",
                url="https://abrasel.com.br/musica-faturamento",
                veiculo="abrasel.com.br",
                contribuicao="Aumento de 35% no consumo de mesas com som acústico.",
            )
        ],
        "queries_executadas": ["musica ao vivo bares faturamento"],
        "total_analisadas": 1,
        "grau_compreensao": 0.95,
    }

    mock_content = ContentGenerationResult(
        tema_selecionado="Música ao Vivo: O Motor Invisível da Noite",
        categoria="Música & Mercado Musical",
        dossie_pesquisa="Pesquisa: Bares faturam 35% mais com música ao vivo segundo a Abrasel.",
        total_fontes_analisadas=1,
        fontes_pesquisadas=[
            FonteCitada(
                titulo="Pesquisa Abrasel 2026",
                url="https://abrasel.com.br/musica-faturamento",
                veiculo="abrasel.com.br",
                contribuicao="Aumento de 35% no consumo de mesas.",
            )
        ],
        blog=ArtigoBlog(
            titulo="O Som do Lucro: Por que Bares com Música ao Vivo Faturam até 35% Mais",
            subtitulo="A matemática dos bastidores entre o cachê, o ECAD e a mesa cheia",
            tempo_leitura_minutos=5,
            corpo_markdown=(
                "## 1. O Choque da Mesa Vazia\n"
                "De acordo com levantamento da [Abrasel](https://abrasel.com.br/musica-faturamento), o cliente permanece...\n\n"
                "## 2. Casos Reais: O Dono de Bar que Quase Faliu\n"
                "No litoral paulista, uma casa de shows trocou o som mecânico...\n\n"
                "## 3. Conceituação sem Fricção: O Maestro do Bar\n"
                "O músico da noite é como um maestro que calibra a temperatura do ambiente...\n\n"
                "## 4. Framework de Gestão Musical (Níveis 1 a 4)\n"
                "| Nível | Estágio | Ação |\n"
                "|---|---|---|\n"
                "| 1 | Básico | Voz e violão acústico |\n\n"
                "## 5. Conclusão: O Acústico Insubstituível\n"
                "Playlists não criam memórias.\n\n"
                "## Referências e Fontes Consultadas\n"
                "- [Abrasel](https://abrasel.com.br/musica-faturamento)"
            ),
            seo=BlogMetadata(
                veiculo="fernandonogueira.dev.br",
                slug_sugerido="musica-ao-vivo-faturamento-bares",
                meta_title="Música ao Vivo em Bares: Como Aumentar o Lucro em 35%",
                meta_description="Descubra a estratégia dos bares que faturam 35% mais com música ao vivo e fidelizam clientes.",
                palavra_chave_foco="música ao vivo em bares",
                palavras_chave_secundarias=["show business", "bares e restaurantes", "lucratividade"],
                tempo_leitura_minutos=5,
            ),
            fontes_citadas=[
                FonteCitada(
                    titulo="Pesquisa Abrasel 2026",
                    url="https://abrasel.com.br/musica-faturamento",
                    veiculo="Abrasel",
                    contribuicao="Aumento de faturamento comprovado.",
                )
            ],
        ),
        linkedin=ArtigoLinkedIn(
            titulo_artigo="Música ao Vivo Não É Custo: É a Alavanca Invisível da Noite",
            subtitulo="Por que estabelecimentos que investem em artistas locais faturam muito mais",
            tempo_leitura_minutos=4,
            corpo_artigo_markdown="## A Experiência que Nenhuma Playlist Substitui\nEm plena era digital...",
            texto_post_divulgacao=(
                "Música ao vivo no seu estabelecimento é custo ou motor de receita?\n"
                "Os dados da última pesquisa da Abrasel surpreendem. 👇\n\n"
                "❌ O erro: cortar o cachê para economizar.\n"
                "🔹 O fato: a permanência do cliente sobe 45 minutos com som ao vivo de qualidade.\n"
                "1️⃣ O resultado: faturamento até 35% superior.\n\n"
                "Confira o artigo completo e veja como estruturar sua gestão musical.\n\n"
                "#ShowBusiness #Empreendedorismo #Gastronomia #MusicaAoVivo #Gestao"
            ),
            gancho_inicial="Música ao vivo é custo ou motor de receita? Os dados surpreendem. 👇",
            chamada_acao="Como você planeja a atração musical no seu espaço? Comente abaixo!",
            hashtags=["#ShowBusiness", "#Empreendedorismo", "#Gastronomia", "#MusicaAoVivo", "#Gestao"],
            fontes_mencionadas=["Abrasel"],
            sugestao_imagem_capa="Músico de costas para o público iluminado em tons de carmim e grafite",
        ),
        instagram=MaterialInstagram(
            legenda=(
                "🚨 SEU BAR ESTÁ PERDENDO 35% DE FATURAMENTO SEM SABER?\n\n"
                "👉 Pesquisa recente comprovou: clientes gastam muito mais com som ao vivo.\n"
                "🔍 O segredo está no tempo de permanência na mesa.\n"
                "🎶 Menos som de rádio, mais experiência humana.\n\n"
                "Salve este post e envie para aquele amigo dono de restaurante! 📲"
            ),
            hashtags=["#musicaaovivo", "#gastronomia", "#bares", "#restaurantes", "#gestaodenegocios"],
            texto_reels=(
                "| Tempo | O que aparece na tela (Cena) | O que você fala (Áudio) |\n"
                "|---|---|---|\n"
                "| 00-05s | Bar vazio em contraste com bar lotado | Por que esse bar aqui fatura 35% mais que o vizinho? |\n"
                "| 06-25s | Close no violão e na conta da mesa subindo | A resposta é o maestro da noite: a música ao vivo acústica! |"
            ),
        ),
        prompts_imagem=PromptsImagem(
            imagem_feed_instagram_1x1=(
                "A vintage acoustic guitar made of smoked dark glass with glowing carmine (#ba2649) strings, "
                "resting on a midnight charcoal marble pedestal (#191b27), deep burgundy highlights (#922824), 8k --ar 1:1"
            ),
            imagem_reels_9x16="Vertical neon soundwaves glowing in carmine (#ba2649) and optical white (#ffffff) --ar 9:16",
            imagem_wordpress_16x9="Panoramic editorial photo of an intimate acoustic lounge, warm moody lighting, palette #ba2649 and #191b27 --ar 16:9",
            paleta_hex=["#ba2649", "#922824", "#a79f97", "#191b27", "#ffffff"],
        ),
    )
    mock_gemini.generate_structured.return_value = mock_content

    result = generate_deep_content_and_deliver(
        pauta_titulo="Música ao Vivo em Bares",
        categoria="Música & Mercado Musical",
        target_format="both",
        hub=mock_hub,
        evolution=mock_evo,
        gemini_client=mock_gemini,
        research_agent=mock_research_agent,
        execute_research=True,
    )

    assert result["status"] == "SUCCESS"
    assert result["doc_id"] == "doc-test-deep-123"
    assert result["total_fontes_analisadas"] == 1
    assert "instagram" in result
    assert "prompts_imagem" in result
    assert result["delivery"]["whatsapp"] == "SENT"
    assert result["delivery"]["email"] == "SENT"

    # Verify Google Docs includes all 4 channels
    appended_text = mock_hub.docs.append_text.call_args[0][1]
    assert "CANAL 1: ARTIGO COMPLETO PARA O LINKEDIN & POST DO FEED" in appended_text
    assert "CANAL 2: ARTIGO PARA BLOG WORDPRESS (com SEO & Framework)" in appended_text
    assert "CANAL 3: MATERIAL DE APOIO PARA INSTAGRAM" in appended_text
    assert "CANAL 4: PROMPTS DETALHADOS DE GERAÇÃO DE IMAGEM" in appended_text
    assert "CANAL 5: DOSSIÊ DE PESQUISA & FONTES WEB CONSULTADAS" in appended_text
    assert "#ba2649" in appended_text

    # Verify WhatsApp notification mentions the 4 channels
    wpp_text = mock_evo.send_text_message.call_args[0][1]
    assert "Pacote Completo Gerado (4 Canais)" in wpp_text
    assert "Blog WordPress" in wpp_text
    assert "Instagram" in wpp_text
    assert "Prompts de Imagem" in wpp_text

    # Verify Email includes the 4 channels
    email_html = mock_hub.gmail.send_email.call_args[1]["html_body"]
    assert "CANAL 1 • LinkedIn Pulse & Feed" in email_html
    assert "CANAL 2 • Artigo Blog WordPress" in email_html
    assert "CANAL 3 • Instagram (Carrossel & Reels)" in email_html
    assert "CANAL 4 • Prompts IA (Midjourney / FLUX)" in email_html
    assert "#ba2649" in email_html


def test_task_deep_content_generation():
    token_payload = {
        "pauta_id": 1,
        "pauta_titulo": "Arquiteturas Escaláveis em Cloud",
        "categoria": "Tecnologia da Informação (TI)",
        "target_format": "both",
        "angulo_editorial": "Foco em redução de custos e resiliência multi-região",
    }

    with patch("flows.flow_content_deep_writer.generate_deep_content_and_deliver") as mock_gen, \
         patch("storage.repository.repo.log_flow_start"), \
         patch("storage.repository.repo.record_editorial_publication") as mock_record, \
         patch("storage.repository.repo.log_flow_complete"):

        mock_gen.return_value = {
            "status": "SUCCESS",
            "doc_url": "https://docs.google.com/test",
            "total_fontes_analisadas": 2,
        }
        res = task_deep_content_generation(token_payload)

        assert res["status"] == "SUCCESS"
        mock_record.assert_called_once()
        mock_gen.assert_called_once_with(
            pauta_titulo="Arquiteturas Escaláveis em Cloud",
            categoria="Tecnologia da Informação (TI)",
            target_format="both",
            contexto_adicional="Foco em redução de custos e resiliência multi-região",
        )


def test_editorial_research_agent_iterative_gap_filling():
    mock_gemini = MagicMock()
    mock_scraper = MagicMock()
    mock_extractor = MagicMock()

    mock_gemini.generate_structured.side_effect = [
        SearchQueryPlan(
            queries=["ia generativa ciberseguranca 2026"],
            intencao_investigativa="Investigar riscos de IA",
        ),
        ComprehensionAssessment(
            tema_compreendido=False,
            grau_compreensao_score=0.4,
            fatos_e_dados_chave=["Ataques cibernéticos usando IA cresceram."],
            lacunas_identificadas=["Falta dado sobre defesas automatizadas e custos corporativos."],
            proxima_query="ia defesa cibernetica custos mitigacao",
        ),
        ComprehensionAssessment(
            tema_compreendido=True,
            grau_compreensao_score=0.95,
            fatos_e_dados_chave=[
                "Ataques cibernéticos usando IA cresceram 40%.",
                "Defesas automatizadas reduzem tempo de resposta em 70%.",
            ],
            lacunas_identificadas=[],
        ),
    ]

    mock_scraper.search.side_effect = [
        {"organic_results": [{"title": "Artigo 1", "url": "https://sec.example.com/1"}]},
        {"organic_results": [{"title": "Artigo 2", "url": "https://sec.example.com/2"}]},
    ]

    mock_extractor.extract.side_effect = [
        {
            "status": "success",
            "metadata": {"title": "Artigo 1"},
            "content": "Ataques cibernéticos usando IA cresceram muito em 2025 e 2026...",
        },
        {
            "status": "success",
            "metadata": {"title": "Artigo 2"},
            "content": "Defesas automatizadas reduzem tempo de contenção de incidentes em 70%...",
        },
    ]

    agent = EditorialResearchAgent(
        gemini_client=mock_gemini,
        google_scraper=mock_scraper,
        content_extractor=mock_extractor,
        max_iterations=2,
    )

    result = agent.conduct_deep_research(
        tema="Cibersegurança e IA",
        categoria="Tecnologia da Informação (TI)",
    )

    assert result["total_analisadas"] == 2
    assert len(result["fontes"]) == 2
    assert "ia defesa cibernetica custos mitigacao" in result["queries_executadas"]


def test_editorial_research_agent_google_rss_fallback():
    mock_gemini = MagicMock()
    mock_scraper = MagicMock()
    mock_extractor = MagicMock()

    mock_gemini.generate_structured.side_effect = [
        SearchQueryPlan(
            queries=["mercado musical streaming"],
            intencao_investigativa="Investigar streaming",
        ),
        ComprehensionAssessment(
            tema_compreendido=True,
            grau_compreensao_score=0.88,
            fatos_e_dados_chave=["Streaming representa 85% da receita da música no Brasil"],
            lacunas_identificadas=[],
        ),
    ]

    mock_scraper.search.side_effect = RuntimeError("Playwright connection lost")

    mock_extractor.extract.return_value = {
        "status": "success",
        "metadata": {"title": "Streaming domina faturamento"},
        "content": "Receita de streaming cresce 12% impulsionada por assinaturas pagas...",
    }

    agent = EditorialResearchAgent(
        gemini_client=mock_gemini,
        google_scraper=mock_scraper,
        content_extractor=mock_extractor,
        max_iterations=1,
    )

    with patch("feedparser.parse") as mock_rss:
        mock_entry = MagicMock()
        mock_entry.get.side_effect = lambda k, default="": {
            "title": "Notícia RSS Streaming",
            "link": "https://g1.globo.com/musica/streaming-recorde",
            "summary": "Recorde de streaming no Brasil",
            "published": "15/09/2026",
        }.get(k, default)

        mock_feed = MagicMock()
        mock_feed.entries = [mock_entry]
        mock_rss.return_value = mock_feed

        result = agent.conduct_deep_research(
            tema="O Streaming no Brasil",
            categoria="Música & Mercado Musical",
        )

        assert result["total_analisadas"] == 1
        assert result["fontes"][0].url == "https://g1.globo.com/musica/streaming-recorde"


def test_editorial_research_agent_default_limits():
    """Validates that EditorialResearchAgent defaults to 5 iterations and 6 max pages to scrape."""
    mock_gemini = MagicMock()
    agent = EditorialResearchAgent(gemini_client=mock_gemini)
    assert agent.max_iterations == 5
    assert agent.max_pages_to_scrape == 6

