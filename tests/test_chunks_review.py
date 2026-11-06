"""분할 수신→합치기→분류→검토→선택 ZIP의 실제 바이트와 경계 조건을 검증합니다."""

import csv
import io
import struct
import time
from zipfile import ZipFile

from fastapi.testclient import TestClient

from app.main import create_app, cleanup_once
from app.services.web_jobs import inspect_zip_metadata
from test_web import TestPredictor, image_bytes, settings_for, wait_result


def send_chunks(client, files, chunk=32):
    """작은 조각으로 실제 분할 프로토콜을 실행하고 작업 ID를 반환합니다."""
    response = client.post('/api/uploads', json={'files': [{'name': n, 'size': len(b)} for n, b in files]})
    assert response.status_code == 201, response.text
    job = response.json()['id']
    for f, (_, content) in enumerate(files):
        for part, start in enumerate(range(0, len(content), chunk)):
            url = f'/api/uploads/{job}/{f}/{part}'
            response = client.put(url, content=content[start:start+chunk], headers={'Content-Type': 'application/octet-stream'})
            assert response.status_code == 200, response.text
            # 성공 응답 유실을 가정한 같은 조각 재전송이 중복 파일을 만들지 않습니다.
            if part == 0:
                assert client.put(url, content=content[:chunk], headers={'Content-Type': 'application/octet-stream'}).json()['duplicate']
    assert client.post(f'/api/uploads/{job}/complete').status_code == 202
    return job


def wait_export(client, job, selection):
    """선택 ZIP을 접수하고 백그라운드 완료를 기다립니다."""
    response = client.post(f'/api/jobs/{job}/exports', json=selection)
    assert response.status_code == 202, response.text
    data = response.json()
    for _ in range(300):
        data = client.get(f'/api/jobs/{job}/exports/{data["id"]}').json()
        if data['status'] != 'processing':
            assert data['status'] == 'completed', data
            return data
        time.sleep(.01)
    raise AssertionError('선택 ZIP 생성 시간 초과')


def test_chunks_thumbnails_selected_zip_and_range(tmp_path):
    """원본 일치, 중복 이름, 분류 선택, 썸네일, 범위 다운로드까지 확인합니다."""
    red, green, blue = image_bytes(), image_bytes((0,255,0)), image_bytes((0,0,255))
    files = [('same.png', red), ('same.png', blue), ('green.png', green)]
    with TestClient(create_app(settings_for(tmp_path, chunk_bytes=32), lambda _: TestPredictor())) as c:
        job_id = send_chunks(c, files)
        result = wait_result(c, job_id)
        assert result['counts'] == {'food': 1, 'landscape': 1, 'person': 1}
        assert c.post(f'/api/uploads/{job_id}/complete').status_code == 202
        items = c.get(f'/api/jobs/{job_id}/items?category=person').json()
        person = items['items'][0]
        assert 'destination' not in person
        thumb = c.get(person['thumbnail_url'])
        assert thumb.status_code == 200 and thumb.headers['content-type'] == 'image/jpeg'
        selection = {'categories': ['food'], 'include_ids': [person['id']], 'excluded_ids': []}
        export = wait_export(c, job_id, selection)
        download = c.get(export['download_url'])
        assert download.headers['content-disposition'].startswith('attachment;')
        with ZipFile(io.BytesIO(download.content)) as archive:
            assert archive.read('food/same.png') == red
            assert archive.read('person/same.png') == blue
            assert 'landscape/green.png' not in archive.namelist()
            report = list(csv.DictReader(io.StringIO(archive.read('classification_report.csv').decode('utf-8-sig'))))
            assert len(report) == 2
        partial = c.get(export['download_url'], headers={'Range': 'bytes=0-19'})
        assert partial.status_code == 206 and partial.content == download.content[:20]
        assert c.get(f'/api/jobs/{job_id}/items?category=bad').status_code == 400
        assert c.get(f'/api/jobs/{job_id}/items/999/thumbnail').status_code == 404
        assert c.post(f'/api/jobs/{job_id}/exports', json={'categories': []}).status_code == 400
        assert c.post(f'/api/jobs/{job_id}/exports', json={'excluded_ids': [-1]}).status_code == 400


def test_missing_oversized_chunk_expiration_and_large_manifest(tmp_path):
    """3GB 명세 허용과 누락·과대 조각 거부, 취소·만료 후 슬롯 반환을 검증합니다."""
    settings = settings_for(tmp_path, chunk_bytes=32, disk_reserve_bytes=1)
    with TestClient(create_app(settings, lambda _: TestPredictor())) as c:
        session = c.post('/api/uploads', json={'files': [{'name':'large.zip', 'size':3*1024**3}]})
        assert session.status_code == 201, session.text
        jid = session.json()['id']
        assert c.post(f'/api/uploads/{jid}/complete').status_code == 409
        assert c.put(f'/api/uploads/{jid}/0/0', content=b'x'*33, headers={'Content-Type':'application/octet-stream'}).status_code == 413
        assert c.put(f'/api/uploads/{jid}/0/0', content=b'x', headers={'Content-Type':'application/octet-stream'}).status_code == 400
        assert c.get(f'/api/uploads/{jid}').json()['received'] == []
        assert c.delete(f'/api/uploads/{jid}').status_code == 200
        assert not c.get('/api/status').json()['busy']
        session = c.post('/api/uploads', json={'files':[{'name':'a.png', 'size':10}]}).json()
        job = c.app.state.jobs[session['id']]
        job.expires_at = time.time()-1
        c.portal.call(cleanup_once, c.app)
        assert not job.directory.exists() and not c.get('/api/status').json()['busy']


def test_zip64_metadata_and_forced_zip64_photo(tmp_path):
    """ZIP64 목록 구조를 만들고 정상 입력에서 사진 분류가 가능한지 확인합니다."""
    buffer = io.BytesIO()
    with ZipFile(buffer,'w') as z:
        with z.open('a.png', 'w', force_zip64=True) as f:
            f.write(image_bytes())
    data = buffer.getvalue()
    offset = data.rfind(b'PK\x05\x06')
    end = list(struct.unpack('<4s4H2IH', data[offset:]))
    zip64 = struct.pack('<4sQ2H2I4Q', b'PK\x06\x06',44,45,45,0,0,end[4],end[4],end[5],end[6])
    locator = struct.pack('<4sIQI', b'PK\x06\x07',0,offset,1)
    end[3] = end[4] = 65535
    end[5] = end[6] = 0xffffffff
    data = data[:offset]+zip64+locator+struct.pack('<4s4H2IH',*end)
    path = tmp_path/'zip64.zip';path.write_bytes(data)
    inspect_zip_metadata(path, 100)
    with TestClient(create_app(settings_for(tmp_path,chunk_bytes=32), lambda _:TestPredictor())) as c:
        job = send_chunks(c,[('zip64.zip',data)])
        final = wait_result(c,job)
        assert final['counts']['food']==1 and final['failed_count']==0
