"""Synthetic CI fixture construction only. Never imported by a service entry."""
import ast
import hashlib
import io
from pathlib import Path, PurePosixPath
import stat
import zipfile

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[3]
SOURCE = ROOT.parents[1] / 'custodian_v2'
WHEEL_DIR = ROOT / 'wheels'
COLD_SHA = '091133edcafb1110e13e6a79f51d336d9a152f2bf3f96d485b2c38d3899f438c'
CATALOG_SHA = '8592b67437440c5d8cbfc98ff6a663f18be40bac12cce7de847e7d149ed4fff3'


def require(condition):
    if not condition:
        raise RuntimeError('SYNTHETIC_FIXTURE_REFUSED')


def digest(body):
    return hashlib.sha256(body).hexdigest()


def wheel_pins():
    body = (SOURCE / 'cold/cold_host.py').read_bytes()
    require(digest(body) == COLD_SHA)
    rows = [node for node in ast.parse(body).body if isinstance(node, ast.Assign)
            and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == 'WHEELS']
    require(len(rows) == 1)
    pins = ast.literal_eval(rows[0].value)
    require(type(pins) is dict and len(pins) == 7)
    return pins


def frozen_source(name):
    require(name.startswith('ops/') and '..' not in PurePosixPath(name).parts)
    if name == 'ops/backup/erasure_journal_s3.py':
        return SOURCE / 'frozen' / name
    return REPO / name


def source_path(name):
    if name.startswith('frozen/'):
        return frozen_source(name.removeprefix('frozen/'))
    return SOURCE / name


def wheel_blobs():
    pins = wheel_pins()
    require({path.name for path in WHEEL_DIR.iterdir()} == set(pins))
    result = {}
    for name, expected in pins.items():
        path = WHEEL_DIR / name
        require(path.is_file() and not path.is_symlink() and path.stat().st_nlink == 1)
        require(0 < path.stat().st_size < 2 * 1024 * 1024)
        body = path.read_bytes()
        require(digest(body) == expected)
        result[name] = body
    return result


def input_hashes():
    return {'wheel:' + name: digest(body) for name, body in wheel_blobs().items()}


def unpack_verified_wheels(destination):
    """Seven exact wheel bytes; bounded member writes, no pip/import/hooks."""
    written, total, records = set(), 0, []
    for name, body in wheel_blobs().items():
        with zipfile.ZipFile(io.BytesIO(body)) as wheel:
            infos = wheel.infolist()
            require(0 < len(infos) < 2000 and len({row.filename for row in infos}) == len(infos))
            for info in infos:
                member = info.filename[:-1] if info.is_dir() else info.filename
                path = PurePosixPath(member)
                require(member and not path.is_absolute() and '\\' not in member
                        and ':' not in member and '\0' not in member
                        and all(part not in ('', '.', '..') for part in member.split('/')))
                mode = info.external_attr >> 16
                require(not stat.S_ISLNK(mode) and not info.flag_bits & 1)
                require(not path.name.lower().endswith(('.pth', '.pyc', '.so')))
                require(0 <= info.file_size <= 8 * 1024 * 1024)
                total += info.file_size
                require(total <= 32 * 1024 * 1024)
                target = destination.joinpath(*path.parts)
                require(target.resolve().is_relative_to(destination.resolve()))
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                require(stat.S_IFMT(mode) in (0, stat.S_IFREG) and member.casefold() not in written)
                written.add(member.casefold())
                payload = wheel.read(info)
                require(len(payload) == info.file_size)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as output:
                    output.write(payload)
        records.append({'package': name.split('-', 1)[0].replace('_', '-'),
                        'wheel': name, 'sha256': digest(body)})
    return sorted(records, key=lambda row: row['package'])
