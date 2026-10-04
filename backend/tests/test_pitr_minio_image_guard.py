"""The synthetic MinIO image must retain pinned, official inputs and TLS wiring."""
from pathlib import Path

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
