"""The synthetic MinIO image must retain pinned, official inputs and TLS wiring."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_minio_image_inputs_are_exact_and_official():
    df = (ROOT / 'ops/pitr/minio-test/Dockerfile').read_text()
    assert 'FROM alpine:3.24@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6' in df
    for project, release, digest in (
        ('minio', 'RELEASE.2025-09-07T16-13-09Z', '7c5bd8512c6e966455b1d198209358b2d191c77a83ab377c4073281065fb855f'),
        ('mc', 'RELEASE.2025-08-13T08-35-41Z', '01f866e9c5f9b87c2b09116fa5d7c06695b106242d829a8bb32990c00312e891'),
    ):
        assert f'ADD --checksum=sha256:{digest} --chmod=0755 https://github.com/minio/{project}/releases/download/{release}/{project}.linux-amd64.{release} /usr/local/bin/{project}' in df
    assert 'ENTRYPOINT ["minio"]' in df
    assert 'apk add' not in df


def test_both_pitr_workflows_build_before_using_minio():
    for name in ('pitr_synthetic.yml', 'pitr_extended.yml'):
        workflow = (ROOT / '.github/workflows' / name).read_text()
        build = 'docker buildx build --load --platform linux/amd64 -t "$MINIO_IMAGE" ops/pitr/minio-test'
        assert workflow.index(build) < workflow.index('name: TLS MinIO')
        assert 'repo1-storage-verify-tls=y' in workflow
        assert 'repo1-storage-verify-tls=n' not in workflow
        assert '"$W/certs":/root/.minio/certs:ro' in workflow


def test_remaining_consumers_build_fixture_in_correct_job():
    build = 'docker buildx build --load --platform linux/amd64 -t "$MINIO_IMAGE" ops/pitr/minio-test'
    for name, job, use in (
        ('backup_restore_synthetic.yml', 'synthetic', 'docker run -d --name minio'),
        ('canary_offline.yml', 'minio-compat', 'docker create --name mcextract'),
    ):
        workflow = (ROOT / '.github/workflows' / name).read_text()
        data = yaml.safe_load(workflow)
        steps = data['jobs'][job]['steps']
        commands = '\n'.join(step.get('run', '') for step in steps)
        assert commands.count(build) == 1
        assert commands.index(build) < commands.index(use)
        assert 'MINIO_IMAGE: "pult-minio-test:ci"' in workflow
        triggers = data.get('on', data.get(True))
        for event in ('pull_request', 'push'):
            assert 'ops/pitr/minio-test/**' in triggers[event]['paths']
            assert 'backend/tests/test_pitr_minio_image_guard.py' in triggers[event]['paths']
        assert 'minio/minio:RELEASE.' not in workflow
        if job == 'minio-compat':
            assert 'docker cp mcextract:/usr/local/bin/mc' in commands
            assert '127.0.0.1:9000:9000' in commands
            offline = '\n'.join(s.get('run', '') for s in data['jobs']['offline']['steps'])
            assert 'docker ' not in offline
