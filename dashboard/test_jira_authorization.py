"""Authorization tests for /api/jira/create.

The Jira integration acts with a shared credential, and the Service also
exposes the app port directly (used by the collector CronJob), so the
endpoint must refuse requests that did not come through the oauth-proxy.
"""

import os
import sys
import tempfile
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from web.server import create_app

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.yaml')
PAYLOAD = {'test_name': 'OCP-11111', 'version': '4.22', 'platform': 'aws'}


@pytest.fixture
def client():
    db_fd, db_path = tempfile.mkstemp(suffix='.db')
    application = create_app(db_path=db_path, config_file=CONFIG_PATH)
    application.config['TESTING'] = True
    yield application.test_client()
    os.close(db_fd)
    os.unlink(db_path)


LOOPBACK = {'REMOTE_ADDR': '127.0.0.1'}
OTHER_POD = {'REMOTE_ADDR': '10.128.2.44'}


class TestJiraEndpointAuthorization:
    """Only the oauth-proxy sidecar may reach Jira: it connects over
    loopback and injects X-Forwarded-User. A caller on the Service port
    from another pod cannot impersonate that."""

    def test_request_without_identity_header_is_forbidden(self, client):
        """Negative: no header, no Jira."""
        with patch('integrations.get_jira_integration') as mock_jira:
            response = client.post('/api/jira/create', json=PAYLOAD,
                                   environ_base=LOOPBACK)

        assert response.status_code == 403
        assert response.get_json() == {'error': 'Not authorized'}
        mock_jira.assert_not_called()

    def test_empty_identity_header_is_forbidden(self, client):
        """Negative: a present but blank header is not an identity."""
        with patch('integrations.get_jira_integration') as mock_jira:
            response = client.post(
                '/api/jira/create', json=PAYLOAD,
                headers={'X-Forwarded-User': '   '},
                environ_base=LOOPBACK)

        assert response.status_code == 403
        mock_jira.assert_not_called()

    def test_spoofed_header_from_another_pod_is_forbidden(self, client):
        """Negative (the CodeRabbit finding): a header set by a caller
        that reached the Service port directly must be ignored, because
        the peer address is that pod, not the loopback proxy."""
        with patch('integrations.get_jira_integration') as mock_jira:
            response = client.post(
                '/api/jira/create', json=PAYLOAD,
                headers={'X-Forwarded-User': 'attacker'},
                environ_base=OTHER_POD)

        assert response.status_code == 403
        mock_jira.assert_not_called()

    def test_spoofed_header_from_another_pod_ipv6_is_forbidden(self, client):
        """Negative: same rule for an IPv6 peer."""
        with patch('integrations.get_jira_integration') as mock_jira:
            response = client.post(
                '/api/jira/create', json=PAYLOAD,
                headers={'X-Forwarded-User': 'attacker'},
                environ_base={'REMOTE_ADDR': 'fd00::5'})

        assert response.status_code == 403
        mock_jira.assert_not_called()

    def test_request_from_proxy_is_allowed(self, client):
        """The loopback proxy with an identity reaches the handler."""
        with patch('integrations.get_jira_integration', return_value=None):
            response = client.post(
                '/api/jira/create', json=PAYLOAD,
                headers={'X-Forwarded-User': 'rrasouli'},
                environ_base=LOOPBACK)

        assert response.status_code == 200
        assert response.get_json()['status'] == 'disabled'

    def test_collector_trigger_still_open(self, client):
        """The collector CronJob calls the plain port and must keep
        working: the trigger endpoint is not gated on the identity
        header."""
        with patch('web.server.threading.Thread'):
            response = client.post('/api/trigger-collection',
                                   json={'days': 3})

        assert response.status_code in (200, 409)
        assert response.status_code != 403
