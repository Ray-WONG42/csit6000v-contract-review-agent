import pytest
import socket
from tacc.client import resolve_endpoint


def test_native_node_resolution_keeps_service_port(monkeypatch):
    monkeypatch.setattr(resolve_endpoint.socket, 'gethostbyname', lambda name: '192.0.2.7')
    assert resolve_endpoint.resolve_base_url('http://course-node:30074/v1') == 'http://192.0.2.7:30074/v1'


def test_numeric_service_address_needs_no_dns(monkeypatch):
    monkeypatch.setattr(resolve_endpoint.socket, 'gethostbyname', lambda name: pytest.fail('unexpected DNS'))
    assert resolve_endpoint.resolve_base_url('http://192.0.2.7:30074/v1') == 'http://192.0.2.7:30074/v1'


def test_course_encoded_node_without_dns(monkeypatch):
    def no_dns(name):
        raise socket.gaierror('no node alias')
    monkeypatch.setattr(resolve_endpoint.socket, 'gethostbyname', no_dns)
    assert resolve_endpoint.resolve_base_url('http://10-0-1-10:30074/v1') == 'http://10.0.1.10:30074/v1'


def test_unresolved_ordinary_hostname_is_not_guessed(monkeypatch):
    def no_dns(name):
        raise socket.gaierror('no host')
    monkeypatch.setattr(resolve_endpoint.socket, 'gethostbyname', no_dns)
    with pytest.raises(socket.gaierror):
        resolve_endpoint.resolve_base_url('http://unknown-node:30074/v1')


@pytest.mark.parametrize('url', ['https://course-node:80/v1', 'http://user:pass@course-node:80/v1', 'http://course-node:80/v1?x=1'])
def test_rejects_wrong_course_address(url):
    with pytest.raises(ValueError):
        resolve_endpoint.resolve_base_url(url)
