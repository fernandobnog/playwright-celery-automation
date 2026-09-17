"""
Automated unit tests for the Check Links NT flow.
Validates ping parsing, DB queries, state transition detection, and WhatsApp alert triggering.
"""

from unittest.mock import MagicMock, patch
import pytest

from flows.flow_link_monitor import (
    ping_ip,
    get_stored_link_statuses,
    update_link_status_postgres,
    task_check_network_links,
)


def test_ping_ip_success():
    mock_stdout = "PING 1.1.1.1 (1.1.1.1) 56(84) bytes of data.\n--- 1.1.1.1 ping statistics ---\n2 packets transmitted, 2 received, 0% packet loss\nrtt min/avg/max/mdev = 12.345/14.567/16.789/1.234 ms\n"
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=mock_stdout, stderr="")
        res = ping_ip("1.1.1.1")
        assert res["status"] == "UP"
        assert res["latency"] == "14.567 ms"
        assert res["error"] is None


def test_ping_ip_failure():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Destination Host Unreachable")
        res = ping_ip("10.255.255.1")
        assert res["status"] == "DOWN"
        assert res["latency"] is None
        assert "Unreachable" in res["error"]


def test_get_stored_link_statuses():
    with patch("psycopg2.connect") as mock_conn:
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [("VIVO", "UP"), ("NEOLINK", "DOWN")]
        mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = mock_cursor

        statuses = get_stored_link_statuses("postgresql://fake:fake@fake:5432/fake")
        assert statuses == {"VIVO": "UP", "NEOLINK": "DOWN"}


def test_update_link_status_postgres():
    with patch("psycopg2.connect") as mock_conn:
        mock_cursor = MagicMock()
        mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = mock_cursor

        ok = update_link_status_postgres("VIVO", "DOWN", "postgresql://fake:fake@fake:5432/fake")
        assert ok is True
        mock_cursor.execute.assert_called_once()


def test_task_check_network_links_alert_on_change():
    # Previous status: VIVO is UP, NEOLINK is UP
    old_statuses = {"VIVO": "UP", "NEOLINK": "UP"}

    with patch("storage.repository.repo.get_link_statuses", return_value=old_statuses), \
         patch("storage.repository.repo.update_link_status") as mock_repo_update, \
         patch("flows.flow_link_monitor.ping_ip") as mock_ping, \
         patch("flows.flow_link_monitor.update_link_status_postgres") as mock_update, \
         patch("integrations.evolution.EvolutionClient.send_text_message") as mock_send_msg:

        # VIVO is still UP, but NEOLINK went DOWN
        mock_ping.side_effect = [
            {"status": "UP", "latency": "15 ms", "error": None},     # VIVO
            {"status": "DOWN", "latency": None, "error": "Timeout"},  # NEOLINK
        ]

        result = task_check_network_links()
        assert result["status"] == "SUCCESS"
        assert result["links_checked"] == 2

        # Verify alert was sent for NEOLINK ONLY
        mock_update.assert_called_once_with("NEOLINK", "DOWN")
        mock_send_msg.assert_called_once()
