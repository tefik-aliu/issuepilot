from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / 'concurrency.db')) as client:
        yield client


def create(client):
    return client.post('/api/issues', json={'title': 'Checkout timeout'}).json()


def test_stale_update_and_delete_preserve_saved_work_and_history(client):
    original = create(client)
    url = f"/api/issues/{original['id']}"
    saved = client.patch(url, json={'status': 'in_progress'}, headers={'X-Issue-Version': '1'})
    assert saved.status_code == 200
    assert saved.json()['version'] == 2
    events = client.get(url + '/history').json()
    stale = client.patch(url, json={'status': 'resolved'}, headers={'X-Issue-Version': '1'})
    assert stale.status_code == 409
    assert stale.json()['detail']['current'] == saved.json()
    assert client.delete(url, headers={'X-Issue-Version': '1'}).status_code == 409
    assert client.get('/api/issues').json() == [saved.json()]
    assert client.get(url + '/history').json() == events
    assert events[0]['before'] == original
    assert events[0]['after'] == saved.json()
    reviewed = client.patch(url, json={'status': 'resolved'}, headers={'X-Issue-Version': '2'})
    assert reviewed.status_code == 200
    assert reviewed.json()['version'] == 3
    assert client.delete(url, headers={'X-Issue-Version': '3'}).status_code == 204
    assert client.patch(url, json={'status': 'open'}, headers={'X-Issue-Version': '3'}).status_code == 404


@pytest.mark.parametrize('method', ['patch', 'delete'])
@pytest.mark.parametrize('version,expected', [(None, 428), ('0', 422), ('-1', 422), ('1.0', 422), ('true', 422), ('9'*20, 422), ('2', 409)])
def test_write_preconditions_fail_without_mutation(client, method, version, expected):
    original = create(client)
    options = {'headers': {'X-Issue-Version': version}} if version is not None else {}
    if method == 'patch':
        options['json'] = {'status': 'resolved'}
    response = getattr(client, method)(f"/api/issues/{original['id']}", **options)
    assert response.status_code == expected
    assert client.get('/api/issues').json() == [original]
    assert len(client.get('/api/activity').json()) == 1


@pytest.mark.parametrize('second_operation', ['update', 'delete'])
def test_two_simultaneous_clients_cannot_both_save_same_version(client, second_operation):
    original = create(client)
    url = f"/api/issues/{original['id']}"
    gate = Barrier(2)

    def write(operation):
        # Separate clients / connections, same version read before either write.
        with TestClient(client.app) as other:
            gate.wait(timeout=10)
            if operation == 'delete':
                return other.delete(url, headers={'X-Issue-Version': '1'})
            return other.patch(url, json={'status': 'resolved'}, headers={'X-Issue-Version': '1'})

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(write, 'update')
        second = pool.submit(write, second_operation)
        responses = [first.result(), second.result()]
    assert sum(r.status_code in (200, 204) for r in responses) == 1
    assert sum(r.status_code in (404, 409) for r in responses) == 1
    events = client.get(url + '/history').json()
    assert len(events) == 2
    assert events[0]['before'] == original
    if events[0]['after']:
        assert events[0]['after']['version'] == 2
