"""
Automated unit tests for Flow: Site Data Synchronizer (Google Sheets -> site-postgres).
Tests venue normalization (DDD 19), date parsing, SQL upsert generation for Show and Song tables,
and Celery task execution.
"""

from datetime import datetime, date
from unittest.mock import MagicMock, patch
import pytest

from flows.flow_site_sync import (
    get_venue_details,
    parse_date_brazilian,
    sync_agenda_to_postgres,
    sync_repertorio_to_postgres,
    task_sync_site_agenda,
    task_sync_site_repertorio,
)


def test_get_venue_details():
    # Known venues
    v1 = get_venue_details("Restaurante Zafferano", "")
    assert v1["city"] == "Mogi Mirim"
    assert v1["address"] == "Zafferano Gastronomia"

    v2 = get_venue_details("Areca Lounge", "")
    assert v2["city"] == "Mogi Mirim"
    assert v2["address"] == "Areca Bambu Lounge"

    v3 = get_venue_details("Tina", "")
    assert v3["city"] == "Mogi Mirim"
    assert v3["address"] == "Bar do Tina"

    v4 = get_venue_details("Holambra Garden", "Obs: show na Carol Conceito Studio")
    assert v4["city"] == "Holambra"
    assert v4["address"] == "Conceito e Beleza Studio Hair"

    v5 = get_venue_details("Campinas Chopp Bar", "")
    assert v5["city"] == "Campinas"
    assert v5["address"] is None

    # Fallback
    v_unknown = get_venue_details("Bar Desconhecido", "")
    assert v_unknown["city"] == "São Paulo (Região)"
    assert v_unknown["state"] == "SP"


def test_parse_date_brazilian():
    dt = parse_date_brazilian("20/11/2026")
    assert dt is not None
    assert dt.day == 20
    assert dt.month == 11
    assert dt.year == 2026

    assert parse_date_brazilian("") is None
    assert parse_date_brazilian(None) is None
    assert parse_date_brazilian("data_invalida") is None


def test_sync_agenda_to_postgres():
    mock_hub = MagicMock()
    mock_records = [
        {
            "Data": "25/12/2026",
            "Local": "Zafferano",
            "Início": "20:00",
            "Término": "23:00",
            "Observações": "Palco interno",
            "Cache": "600",
        },
        {
            "Data": "10/01/2025",  # Past show
            "Local": "Areca",
            "Início": "19:00",
            "Término": "22:00",
            "Observações": "",
            "Cache": "500",
        }
    ]
    mock_hub.sheets.read_records.return_value = mock_records

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    # Mock cursor responses: first show exists (row returned), second does not (None), then returns new id
    mock_cur.fetchone.side_effect = [("existing-uuid-1",), None, ("new-uuid-2",)]

    with patch("psycopg2.connect", return_value=mock_conn):
        result = sync_agenda_to_postgres(
            spreadsheet_id="dummy_sheet",
            worksheet_name="Agenda",
            postgres_url="postgresql://user:pass@localhost:5432/testdb",
            hub=mock_hub,
        )

        assert result["status"] == "SUCCESS"
        assert result["total_shows"] == 2
        assert result["upcoming_count"] == 1
        assert mock_cur.execute.call_count >= 4
        mock_conn.commit.assert_called_once()


def test_sync_repertorio_to_postgres():
    mock_hub = MagicMock()
    mock_records = [
        {
            "Musica": "Tempo Perdido",
            "Artista": "Legião Urbana",
            "Genero": "Rock Nacional",
            "Ativa": "SIM",
        },
        {
            "Musica": "Como Nossos Pais",
            "Artista": "Elis Regina",
            "Genero": "MPB",
            "Ativa": "NAO",
        },
    ]
    mock_hub.sheets.read_records.return_value = mock_records

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    # Existing song check: first found, second not found
    mock_cur.fetchone.side_effect = [("song-uuid-1",), None]

    with patch("psycopg2.connect", return_value=mock_conn):
        result = sync_repertorio_to_postgres(
            spreadsheet_id="dummy_repertorio",
            worksheet_name="Lista de Musicas",
            postgres_url="postgresql://user:pass@localhost:5432/testdb",
            hub=mock_hub,
        )

        assert result["status"] == "SUCCESS"
        assert result["total_songs"] == 2
        assert result["updated"] == 1
        assert result["inserted"] == 1
        mock_conn.commit.assert_called_once()


def test_task_sync_site_agenda():
    with patch("flows.flow_site_sync.sync_agenda_to_postgres") as mock_sync, \
         patch("flows.flow_site_sync.site_publisher.trigger_static_rebuild", return_value={"status": "SUCCESS"}), \
         patch("storage.repository.repo.log_flow_start"), \
         patch("storage.repository.repo.log_flow_complete"):

        mock_sync.return_value = {"status": "SUCCESS", "total_shows": 10}
        res = task_sync_site_agenda()
        assert res["status"] == "SUCCESS"
        assert res["total_shows"] == 10
        mock_sync.assert_called_once()


def test_task_sync_site_repertorio():
    with patch("flows.flow_site_sync.sync_repertorio_to_postgres") as mock_sync, \
         patch("flows.flow_site_sync.site_publisher.trigger_static_rebuild", return_value={"status": "SUCCESS"}), \
         patch("storage.repository.repo.log_flow_start"), \
         patch("storage.repository.repo.log_flow_complete"):

        mock_sync.return_value = {"status": "SUCCESS", "total_songs": 150}
        res = task_sync_site_repertorio()
        assert res["status"] == "SUCCESS"
        assert res["total_songs"] == 150
        mock_sync.assert_called_once()
