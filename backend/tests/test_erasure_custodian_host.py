"""Offline host-input/artifact contracts, not proof of Linux identity isolation.

ACL tests model descriptor metadata and xattrs only. They neither impersonate a
real UID nor demonstrate OS permissions; separate Linux child tests own that proof.
"""
import configparser
from copy import deepcopy
import json
from pathlib import Path
import re
import shlex
import socket
import stat
import struct
import sys
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from ops.backup import erasure_custodian_host as host

ARTIFACTS = ROOT / "ops/backup/custodian"
PACKAGES = {"anyio", "certifi", "h11", "httpcore", "httpx", "idna", "typing-extensions"}
SERVICE, WRITER = 46001, 46002
GENERATION = "1e534b17-05e7-4529-8ffa-c32162c67ad5"
UNDEFINED = 0xffffffff
PROJECTED_ACL = [(1, 4, UNDEFINED), (2, 4, SERVICE), (4, 0, UNDEFINED),
                 (16, 4, UNDEFINED), (32, 0, UNDEFINED)]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("offline host unit test attempted network IO")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "bind", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture
def config(tmp_path):
    return {
        "format": host.FORMAT, "scope": "synthetic", "generation": GENERATION,
        "custodian_uid": SERVICE, "writer_uid": WRITER, "socket_gid": WRITER,
        "state_file": str(tmp_path / "private/anchor.sqlite"),
        "socket_file": str(tmp_path / "run/custodian.sock"),
        "s3": {"endpoint": "https://s3.ru-3.storage.selcloud.ru", "region": "ru-3",
               "bucket": "synthetic-bucket", "key": "pult/erasure-test/synthetic/journal.json"},
    }


def _encode(value):
    return json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")


def test_valid_config_preserves_explicit_inputs_without_touching_paths(config):
    before = deepcopy(config)
    assert host.parse_config(_encode(config)) == before
    assert config == before
    assert not Path(config["state_file"]).exists()
    assert not Path(config["socket_file"]).exists()


@pytest.mark.parametrize("field", ["format", "scope", "generation", "custodian_uid", "writer_uid",
                                   "socket_gid", "state_file", "socket_file", "s3"])
def test_config_requires_every_field(config, field):
    del config[field]
    with pytest.raises(host.HostError, match="unsupported host configuration"):
        host.parse_config(_encode(config))


@pytest.mark.parametrize("version", [None, False, 1, "pult-erasure-custodian-host-v2"])
def test_config_rejects_wrong_format_version(config, version):
    config["format"] = version
    with pytest.raises(host.HostError, match="unsupported host configuration"):
        host.parse_config(_encode(config))


def test_config_rejects_extra_top_level_fields(config):
    config["bootstrap"] = True
    with pytest.raises(host.HostError, match="unsupported host configuration"):
        host.parse_config(_encode(config))


@pytest.mark.parametrize("scope", [None, True, "", "x" * 65, "../other", "a/b", "a.b", "space scope"])
def test_config_rejects_invalid_scopes(config, scope):
    config["scope"] = scope
    with pytest.raises(host.HostError, match="invalid host scope"):
        host.parse_config(_encode(config))


@pytest.mark.parametrize("generation", [None, True, "", GENERATION.upper(), GENERATION.replace("-", ""),
                                       "00000000-0000-0000-0000-000000000000"])
def test_config_requires_canonical_uuid4_generation(config, generation):
    config["generation"] = generation
    with pytest.raises(host.HostError, match="invalid host generation"):
        host.parse_config(_encode(config))


@pytest.mark.parametrize("field", ["custodian_uid", "writer_uid", "socket_gid"])
@pytest.mark.parametrize("value", [None, False, True, 0, -1, 2**31, "46001", 46001.0, []])
def test_all_config_identities_are_positive_plain_bounded_integers(config, field, value):
    config[field] = value
    with pytest.raises(host.HostError, match="invalid dedicated identity"):
        host.parse_config(_encode(config))


def test_config_rejects_shared_service_writer_uid(config):
    config["writer_uid"] = config["custodian_uid"]
    with pytest.raises(host.HostError, match="separate host principals required"):
        host.parse_config(_encode(config))


@pytest.mark.parametrize("relation", ["same", "socket_inside_state", "state_inside_socket"])
def test_config_requires_disjoint_state_and_socket_directories(config, tmp_path, relation):
    state, runtime = tmp_path / "private", tmp_path / "run"
    if relation == "same":
        runtime = state
    elif relation == "socket_inside_state":
        runtime = state / "run"
    else:
        state = runtime / "private"
    config.update(state_file=str(state / "anchor.sqlite"), socket_file=str(runtime / "custodian.sock"))
    with pytest.raises(host.HostError, match="separate state and socket directories required"):
        host.parse_config(_encode(config))


@pytest.mark.parametrize("field", ["state_file", "socket_file"])
@pytest.mark.parametrize("value", [None, 1, "relative/file", "../file", "bad\0path"])
def test_config_rejects_non_absolute_or_invalid_paths(config, field, value):
    config[field] = value
    with pytest.raises(host.HostError, match="invalid host path"):
        host.parse_config(_encode(config))


def test_path_rejects_parent_traversal_without_normalizing_it_away(tmp_path):
    with pytest.raises(host.HostError, match="invalid host path"):
        host._path(str(tmp_path / "private/../run/file"))
    assert host._path(str(tmp_path / "private/file")) == tmp_path / "private/file"


@pytest.mark.parametrize("field", ["endpoint", "region", "bucket", "key"])
def test_s3_schema_requires_all_four_explicit_string_fields(config, field):
    del config["s3"][field]
    with pytest.raises(host.HostError, match="invalid explicit journal location"):
        host.parse_config(_encode(config))


@pytest.mark.parametrize("change", ["extra", "not_object", "wrong_type"])
def test_s3_schema_has_no_extra_credential_or_implicit_input_fields(config, change):
    if change == "extra":
        config["s3"]["writer"] = "synthetic-forbidden-field"
    elif change == "not_object":
        config["s3"] = []
    else:
        config["s3"]["region"] = 3
    with pytest.raises(host.HostError, match="invalid explicit journal location"):
        host.parse_config(_encode(config))


@pytest.mark.parametrize("key", ["pult/erasure-test/other/journal.json", "pult/erasure-test/synthetic/../journal.json",
                                 "pult/erasure-test/synthetic/journal.json?extra=1", "journal.json",
                                 "pult/erasure-test/synthetic/journal.json/"])
def test_s3_key_is_exactly_bound_to_scope(config, key):
    config["s3"]["key"] = key
    with pytest.raises(host.HostError, match="invalid explicit journal location"):
        host.parse_config(_encode(config))


def test_json_rejects_duplicate_config_and_nested_fields(config):
    body = _encode(config)
    for duplicate in (b'{"scope":"fixture-private-marker",' + body[1:],
                      body.replace(b'"region":"ru-3"', b'"region":"ru-3","region":"ru-3"')):
        with pytest.raises(host.HostError, match="duplicate host field") as error:
            host.parse_config(duplicate)
        assert "fixture-private-marker" not in str(error.value)


@pytest.mark.parametrize("constant", [b"NaN", b"Infinity", b"-Infinity"])
def test_json_rejects_non_finite_constants_at_any_depth(constant):
    with pytest.raises(host.HostError, match="non-finite host value"):
        host._json(b'{"nested":{"value":' + constant + b'}}')


@pytest.mark.parametrize("body", [b"", None, "{}", bytearray(b"{}"), b"\xff", b"{", b"[]", b"null",
                                  b"true", b"1", b'"value"', b"{}{}", b"{}\0",
                                  b'{"deep":' + b'[' * 1100 + b'0' + b']' * 1099 + b'}'])
def test_json_rejects_bad_types_malformed_trailing_and_non_object_inputs(body):
    with pytest.raises(host.HostError):
        host._json(body)


def test_json_body_bound_accepts_exact_limit_and_refuses_one_more_byte(config):
    assert host._json(b"{}".ljust(host.MAX_INPUT, b" ")) == {}
    assert host.parse_config(_encode(config).ljust(host.MAX_INPUT, b" ")) == config
    with pytest.raises(host.HostError, match="invalid host input size"):
        host._json(b"{}".ljust(host.MAX_INPUT + 1, b" "))


class IntSubclass(int):
    pass


@pytest.mark.parametrize("value", [1, SERVICE, 2**31 - 1])
def test_identity_helper_accepts_bounded_plain_ints(value):
    assert host._id(value) == value


@pytest.mark.parametrize("value", [None, True, False, 0, -1, 2**31, 1.0, "1", IntSubclass(1)])
def test_identity_helper_has_no_bool_or_subclass_bypass(value):
    with pytest.raises(host.HostError, match="invalid dedicated identity"):
        host._id(value)


def _acl_bytes(entries=PROJECTED_ACL, version=2):
    return struct.pack("<I", version) + b"".join(struct.pack("<HHI", *entry) for entry in entries)


class FakeDescriptorOS:
    """Synthetic descriptor/ACL grammar model; never an actual ownership check."""
    O_RDONLY, O_NOFOLLOW, O_NONBLOCK, O_CLOEXEC = 1, 2, 4, 8

    def __init__(self, *, owner=SERVICE, mode=0o400, acl=None, xattr_error=None):
        self.body = self.remaining = b'{"fixture":"reader-only"}'
        self.acl, self.xattr_error = acl, xattr_error
        self.opened, self.closed, self.read_sizes = [], [], []
        self.info = SimpleNamespace(st_mode=stat.S_IFREG | mode, st_uid=owner, st_nlink=1,
                                    st_size=len(self.body), st_dev=1, st_ino=2,
                                    st_mtime_ns=3, st_ctime_ns=4)

    def open(self, path, flags):
        self.opened.append((path, flags))
        return 41

    def fstat(self, fd):
        assert fd == 41
        return self.info

    def getxattr(self, fd, name):
        assert fd == 41 and name == "system.posix_acl_access"
        if self.xattr_error is not None:
            raise OSError(self.xattr_error, "fixture-private-marker")
        if self.acl is None:
            raise OSError(61, "synthetic absent ACL")
        return self.acl

    def read(self, fd, size):
        assert fd == 41 and 0 < size <= 4096
        self.read_sizes.append(size)
        result, self.remaining = self.remaining[:size], self.remaining[size:]
        return result

    def close(self, fd):
        self.closed.append(fd)


def _descriptor_model(monkeypatch, **fields):
    model = FakeDescriptorOS(**fields)
    monkeypatch.setattr(host, "os", model)
    monkeypatch.setattr(host, "errno", SimpleNamespace(ENODATA=61, ENOTSUP=95))
    # Modelled ancestors only: real Linux path/UID enforcement is tested elsewhere.
    monkeypatch.setattr(host, "_ancestors", lambda path, owners: None)
    return model


def test_acl_decodes_exact_named_reader_entries_from_fake_fd(monkeypatch):
    _descriptor_model(monkeypatch, acl=_acl_bytes())
    assert host._acl(41) == {(tag, identity): permission for tag, permission, identity in PROJECTED_ACL}


@pytest.mark.parametrize("raw", [b"", b"\x02", struct.pack("<I", 1), struct.pack("<I", 2) + b"x",
                                 _acl_bytes(PROJECTED_ACL + [PROJECTED_ACL[1]])])
def test_acl_rejects_bad_version_length_and_duplicate_entries(monkeypatch, raw):
    _descriptor_model(monkeypatch, acl=raw)
    with pytest.raises(host.HostError, match="unsupported credential access"):
        host._acl(41)


@pytest.mark.parametrize("error_number", [61, 95])
def test_absent_or_unsupported_acl_is_not_a_projected_root_credential(monkeypatch, tmp_path, error_number):
    model = _descriptor_model(monkeypatch, owner=0, mode=0o440, xattr_error=error_number)
    assert host._acl(41) is None
    with pytest.raises(host.HostError, match="private reader credential required"):
        host._read_file(tmp_path / "reader.json", service_uid=SERVICE)
    assert model.closed == [41]


def test_acl_lookup_errors_are_fixed_and_fail_closed(monkeypatch):
    _descriptor_model(monkeypatch, xattr_error=13)
    with pytest.raises(host.HostError, match="credential access validation unavailable") as error:
        host._acl(41)
    assert "fixture-private-marker" not in str(error.value)
    assert error.value.__suppress_context__


@pytest.mark.parametrize("mode", [0o400, 0o600])
def test_plain_private_service_file_needs_no_acl(monkeypatch, tmp_path, mode):
    model = _descriptor_model(monkeypatch, mode=mode)
    assert host._read_file(tmp_path / "reader.json", service_uid=SERVICE) == model.body
    assert model.closed == [41] and model.opened[0][1] == 15


def test_root_projected_file_requires_exact_single_reader_acl(monkeypatch, tmp_path):
    model = _descriptor_model(monkeypatch, owner=0, mode=0o440, acl=_acl_bytes())
    assert host._read_file(tmp_path / "reader.json", service_uid=SERVICE) == model.body
    assert model.closed == [41]


@pytest.mark.parametrize("entries", [
    [], PROJECTED_ACL[:-1], PROJECTED_ACL + [(2, 4, WRITER)],
    [(1, 4, UNDEFINED), (2, 4, WRITER), *PROJECTED_ACL[2:]],
    [PROJECTED_ACL[0], (2, 6, SERVICE), *PROJECTED_ACL[2:]],
    [*PROJECTED_ACL[:2], (4, 4, UNDEFINED), *PROJECTED_ACL[3:]],
    [*PROJECTED_ACL[:3], (16, 6, UNDEFINED), PROJECTED_ACL[4]],
    [*PROJECTED_ACL[:4], (32, 4, UNDEFINED)],
    PROJECTED_ACL + [(8, 4, WRITER)], PROJECTED_ACL + [(64, 4, UNDEFINED)],
])
def test_projected_acl_has_no_extra_reader_group_write_or_other_rights(monkeypatch, tmp_path, entries):
    model = _descriptor_model(monkeypatch, owner=0, mode=0o440, acl=_acl_bytes(entries))
    with pytest.raises(host.HostError, match="private reader credential required"):
        host._read_file(tmp_path / "reader.json", service_uid=SERVICE)
    assert model.closed == [41] and model.read_sizes == []


@pytest.mark.parametrize("owner,mode,acl", [(0, 0o400, None), (SERVICE, 0o440, None),
                                         (SERVICE, 0o600, _acl_bytes()), (0, 0o640, _acl_bytes()),
                                         (WRITER, 0o400, None)])
def test_credential_owner_mode_and_acl_cannot_substitute_for_one_another(monkeypatch, tmp_path, owner, mode, acl):
    model = _descriptor_model(monkeypatch, owner=owner, mode=mode, acl=acl)
    with pytest.raises(host.HostError):
        host._read_file(tmp_path / "reader.json", service_uid=SERVICE)
    assert model.closed == [41] and model.read_sizes == []


def _lock_blocks(text):
    lines, blocks = text.splitlines(), {}
    for index, line in enumerate(lines):
        match = re.fullmatch(r"([a-z][a-z0-9-]*)==([0-9.]+) \\", line)
        if match:
            name = match[1]
            assert name not in blocks, "duplicate dependency pin"
            block = [line]
            for following in lines[index + 1:]:
                if not following.startswith("    --hash="):
                    break
                block.append(following)
            blocks[name] = tuple(block)
    return blocks


def test_minimal_runtime_lock_is_exact_seven_package_hash_complete_subset():
    text = (ARTIFACTS / "requirements.lock").read_text(encoding="ascii")
    minimal = _lock_blocks(text)
    assert set(minimal) == host.PACKAGES == PACKAGES
    meaningful = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]
    assert meaningful == [line for block in minimal.values() for line in block]
    for name, block in minimal.items():
        assert len(block) >= 2
        hashes = [line.removesuffix(" \\") for line in block[1:]]
        assert len(hashes) == len(set(hashes))
        assert all(re.fullmatch(r"    --hash=sha256:[0-9a-f]{64}", line) for line in hashes)
        assert all(line.endswith(" \\") for line in block[:-1]) and not block[-1].endswith("\\")
        for source in ("requirements.lock", "requirements-ci.lock"):
            original = _lock_blocks((ROOT / "backend" / source).read_text(encoding="ascii"))
            assert block == original[name], f"changed pin/hash block: {name}"


def _closed_source_fixture(monkeypatch, tmp_path, *, internal_runtime=False):
    """Model tree/metadata/namespace decisions only, NOT OS or import isolation."""
    root = tmp_path / "code"
    root.mkdir()
    for name in host.CODE_FILES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# synthetic source placeholder; never imported\n", encoding="ascii")
    runtime = root / "venv" if internal_runtime else tmp_path / "external-runtime"
    if internal_runtime:
        runtime.mkdir()
    environment = SimpleNamespace(
        root=root, stat_changes={}, stat_calls=[], ancestor_calls=[],
        sys=SimpleNamespace(modules={"unrelated": object(), "opshop": object()},
                            executable=str(runtime / "bin/python"), path=["unchanged-sentinel"]),
    )

    class ModelSourcePath(type(Path())):
        def lstat(self):
            environment.stat_calls.append(Path(self))
            actual = super().lstat()
            values = dict(st_mode=actual.st_mode & ~0o022, st_uid=0, st_nlink=1)
            values.update(environment.stat_changes.get(Path(self), {}))
            return SimpleNamespace(**values)

    def ancestors(path, owners):
        assert owners == {0}
        environment.ancestor_calls.append(Path(path))

    monkeypatch.setattr(host, "Path", ModelSourcePath)
    monkeypatch.setattr(host, "sys", environment.sys)
    monkeypatch.setattr(host, "__file__", str(root / "ops/backup/erasure_custodian_host.py"))
    monkeypatch.setattr(host, "_ancestors", ancestors)
    return environment


@pytest.mark.parametrize("internal_runtime", [False, True])
@pytest.mark.parametrize("file_mode", [0o444, 0o644])
def test_closed_source_tree_accepts_only_code_files_and_optional_actual_venv(monkeypatch, tmp_path, internal_runtime, file_mode):
    environment = _closed_source_fixture(monkeypatch, tmp_path, internal_runtime=internal_runtime)
    assert len(host.CODE_FILES) == 7
    for name in host.CODE_FILES:
        environment.stat_changes[environment.root / name] = {"st_mode": stat.S_IFREG | file_mode}
    assert host._trusted_code() == environment.root
    expected_directories = {environment.root, *(path for name in host.CODE_FILES
                                               for path in (environment.root / name).parents
                                               if path.is_relative_to(environment.root))}
    expected_files = {environment.root / name for name in host.CODE_FILES}
    assert set(environment.stat_calls) == expected_directories | expected_files
    assert environment.sys.path == ["unchanged-sentinel"]  # runtime validates before promoting paths


@pytest.mark.parametrize("entry", [
    "httpx.py", "httpx/__init__.py", "sitecustomize.py",
    "ops/backup/erasure_journal/__init__.py", "ops/backup/erasure_custodian_host/__init__.py",
    "ops/canary/canary/__init__.py", "ops/__init__.py", "ops/backup/__init__.py", "ops/canary/__init__.py",
    "ops/__pycache__/fixture.pyc", "ops/backup/__pycache__/fixture.pyc", "ops/canary/__pycache__/fixture.pyc",
    "ops/backup/erasure_anchor.pyc", "ops/backup/erasure_anchor.so", "ops/backup/erasure_anchor.pyd",
    "support/host_proof.py", "ops/backup/tests/custodian_host_proof.py", "docs/runbook.md",
    "ops/backup/custodian/host.example.json", "ops/backup/custodian/pult-erasure-custodian.service",
])
def test_closed_source_tree_rejects_foreign_modules_packages_caches_and_non_source_artifacts(monkeypatch, tmp_path, entry):
    environment = _closed_source_fixture(monkeypatch, tmp_path)
    path = environment.root / entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# model only; never imported\n", encoding="ascii")
    with pytest.raises(host.HostError, match="unexpected source tree entry"):
        host._trusted_code()
    assert environment.sys.path == ["unchanged-sentinel"]


@pytest.mark.parametrize("executable", ["external-runtime/bin/python", "code/other-venv/bin/python",
                                        "code/venv/bin/../bin/python"])
def test_closed_source_tree_venv_exception_requires_exact_lexical_executable_runtime(monkeypatch, tmp_path, executable):
    environment = _closed_source_fixture(monkeypatch, tmp_path, internal_runtime=True)
    environment.sys.executable = str(tmp_path / executable)
    with pytest.raises(host.HostError, match="unexpected source tree entry"):
        host._trusted_code()
    assert environment.sys.path == ["unchanged-sentinel"]


def test_closed_source_tree_rejects_missing_required_file(monkeypatch, tmp_path):
    environment = _closed_source_fixture(monkeypatch, tmp_path)
    (environment.root / "ops/backup/erasure_journal_s3.py").unlink()
    with pytest.raises(host.HostError, match="unexpected source tree entry"):
        host._trusted_code()
    assert environment.sys.path == ["unchanged-sentinel"]


@pytest.mark.parametrize("relative", [".", "ops", "ops/backup", "ops/canary", "ops/backup/custodian"])
@pytest.mark.parametrize("change", ["owner", "group_writable", "sticky_world_writable", "symlink", "not_directory"])
def test_closed_source_directories_require_direct_safe_metadata_even_if_ancestors_are_accepted(monkeypatch, tmp_path, relative, change):
    environment = _closed_source_fixture(monkeypatch, tmp_path)
    environment.stat_changes[environment.root / relative] = {
        "owner": {"st_uid": SERVICE},
        "group_writable": {"st_mode": stat.S_IFDIR | 0o775},
        "sticky_world_writable": {"st_mode": stat.S_IFDIR | stat.S_ISVTX | 0o777},
        "symlink": {"st_mode": stat.S_IFLNK | 0o700},
        "not_directory": {"st_mode": stat.S_IFREG | 0o644},
    }[change]
    with pytest.raises(host.HostError, match="root-controlled source directory required"):
        host._trusted_code()
    assert environment.sys.path == ["unchanged-sentinel"]


@pytest.mark.parametrize("name", host.CODE_FILES)
@pytest.mark.parametrize("change", ["owner", "writable", "private_mode", "hardlink", "symlink"])
def test_every_closed_source_file_retains_strict_regular_file_guards(monkeypatch, tmp_path, name, change):
    environment = _closed_source_fixture(monkeypatch, tmp_path)
    environment.stat_changes[environment.root / name] = {
        "owner": {"st_uid": SERVICE}, "writable": {"st_mode": stat.S_IFREG | 0o664},
        "private_mode": {"st_mode": stat.S_IFREG | 0o600}, "hardlink": {"st_nlink": 2},
        "symlink": {"st_mode": stat.S_IFLNK | 0o644},
    }[change]
    with pytest.raises(host.HostError, match="root-controlled service code required"):
        host._trusted_code()
    assert environment.sys.path == ["unchanged-sentinel"]


@pytest.mark.parametrize("name", ["ops", "ops.backup", "ops.canary.canary", "ops.backup.erasure_custodian_host"])
def test_preloaded_namespace_is_rejected_before_source_metadata_validation(monkeypatch, tmp_path, name):
    environment = _closed_source_fixture(monkeypatch, tmp_path)
    environment.sys.modules[name] = object()
    with pytest.raises(host.HostError, match="fresh isolated host process required"):
        host._trusted_code()
    assert environment.ancestor_calls == environment.stat_calls == []
    assert environment.sys.path == ["unchanged-sentinel"]


def _runtime_fixture(monkeypatch, tmp_path):
    """Real temporary topology, modelled flags/stat/metadata; NOT isolation proof."""
    root, runtime = tmp_path / "code", tmp_path / "venv"
    lock = root / "ops/backup/custodian/requirements.lock"
    lock.parent.mkdir(parents=True)
    text = (ARTIFACTS / "requirements.lock").read_text(encoding="ascii")
    lock.write_text(text, encoding="ascii", newline="\n")
    versions = {name: block[0].split("==", 1)[1].split()[0] for name, block in _lock_blocks(text).items()}
    site = runtime / "lib/python3.13/site-packages"
    site.mkdir(parents=True)
    for name in ("anyio", "certifi", "h11", "httpcore", "httpx", "idna"):
        (site / name).mkdir()
    (site / "typing_extensions.py").write_text("# synthetic module, never imported\n", encoding="ascii")
    for name, version in versions.items():
        (site / (name.replace("-", "_") + "-" + version + ".dist-info")).mkdir()
    environment = SimpleNamespace(
        root=root, site=site, lock=lock, stat_changes={}, children={}, config_calls=[], metadata_calls=[],
        configuration=b"include-system-site-packages = false\n",
        rows=[SimpleNamespace(metadata={"Name": name}, version=version) for name, version in versions.items()],
        sys=SimpleNamespace(flags=SimpleNamespace(isolated=1, no_site=1), dont_write_bytecode=True, version_info=(3, 13),
                            executable=str(runtime / "bin/python"), path=["unchanged-sentinel"]),
    )

    class ModelPath(type(Path())):
        def lstat(self):
            actual = super().lstat()
            values = dict(st_mode=actual.st_mode & ~0o022, st_uid=0,
                          st_nlink=1, st_size=actual.st_size)
            values.update(environment.stat_changes.get(Path(self), {}))
            return SimpleNamespace(**values)

        def iterdir(self):
            if Path(self) in environment.children:
                return iter(type(self)(value) for value in environment.children[Path(self)])
            return super().iterdir()

    def read_configuration(path):
        assert Path(path) == runtime / "pyvenv.cfg"
        environment.config_calls.append(path)
        return environment.configuration

    def distributions(*, path):
        assert path == [str(site)]  # never discover distributions on global sys.path
        environment.metadata_calls.append(path)
        return environment.rows

    def forbidden_global_lookup(*args, **kwargs):
        raise AssertionError("runtime used global metadata.version")

    monkeypatch.setattr(host, "Path", ModelPath)
    monkeypatch.setattr(host, "sys", environment.sys)
    monkeypatch.setattr(host, "_read_file", read_configuration)
    monkeypatch.setattr(host, "_ancestors", lambda path, owners: None)
    monkeypatch.setattr(host, "importlib", SimpleNamespace(metadata=SimpleNamespace(
        distributions=distributions, version=forbidden_global_lookup)))
    return environment


def test_runtime_adds_only_validated_root_and_site_after_scoped_metadata_discovery(monkeypatch, tmp_path):
    environment = _runtime_fixture(monkeypatch, tmp_path)
    host._runtime(environment.root)
    assert len(environment.config_calls) == 1
    assert environment.metadata_calls == [[str(environment.site)]]
    assert environment.sys.path == [str(environment.root), str(environment.site), "unchanged-sentinel"]


@pytest.mark.parametrize("change", ["missing", "extra", "duplicate", "version"])
def test_runtime_refuses_missing_extra_duplicate_or_mismatched_pins(monkeypatch, tmp_path, change):
    environment = _runtime_fixture(monkeypatch, tmp_path)
    text = environment.lock.read_text(encoding="ascii")
    blocks = _lock_blocks(text)
    first = next(iter(blocks.values()))
    if change == "missing":
        text = text.replace("\n".join(first) + "\n", "")
    elif change == "extra":
        text += "unapproved==1.0 \\\n    --hash=sha256:" + "a" * 64 + "\n"
    elif change == "duplicate":
        text += "\n".join(first) + "\n"
    else:
        environment.rows[0].version = "0.0"
    environment.lock.write_text(text, encoding="ascii", newline="\n")
    with pytest.raises(host.HostError, match="version mismatch" if change == "version" else "dependency set"):
        host._runtime(environment.root)
    assert len(environment.metadata_calls) == (1 if change == "version" else 0)
    assert environment.sys.path == ["unchanged-sentinel"]


@pytest.mark.parametrize("setting", ["not_isolated", "site_enabled", "no_bytecode", "old_python"])
def test_runtime_requires_isolation_no_site_and_supported_python_before_reading_inputs(monkeypatch, tmp_path, setting):
    environment = _runtime_fixture(monkeypatch, tmp_path)
    if setting == "not_isolated":
        environment.sys.flags.isolated = 0
    elif setting == "site_enabled":
        environment.sys.flags.no_site = 0
    elif setting == "no_bytecode":
        environment.sys.dont_write_bytecode = False
    else:
        environment.sys.version_info = (3, 11)
    with pytest.raises(host.HostError, match="isolated no-site Python runtime required"):
        host._runtime(environment.root)
    assert environment.config_calls == environment.metadata_calls == []
    assert environment.sys.path == ["unchanged-sentinel"]


@pytest.mark.parametrize("configuration", [b"", b"include-system-site-packages = true\n", b"home = /synthetic\n"])
def test_runtime_requires_explicitly_closed_virtual_environment(monkeypatch, tmp_path, configuration):
    environment = _runtime_fixture(monkeypatch, tmp_path)
    environment.configuration = configuration
    with pytest.raises(host.HostError, match="closed virtual environment required"):
        host._runtime(environment.root)
    assert environment.metadata_calls == [] and environment.sys.path == ["unchanged-sentinel"]


@pytest.mark.parametrize("entry", ["sitecustomize.py", "usercustomize.py", "foreign.py", "foreign.pth", "pip-1.0.dist-info"])
def test_runtime_rejects_foreign_top_level_entries_without_metadata_or_import_path_changes(monkeypatch, tmp_path, entry):
    environment = _runtime_fixture(monkeypatch, tmp_path)
    (environment.site / entry).write_text("# never executed\n", encoding="ascii")
    with pytest.raises(host.HostError, match="unexpected runtime entry"):
        host._runtime(environment.root)
    assert environment.metadata_calls == [] and environment.sys.path == ["unchanged-sentinel"]


@pytest.mark.parametrize("change", ["owner", "writable_file", "writable_directory", "symlink", "fifo", "hardlink", "size", "pth", "count"])
def test_runtime_tree_metadata_and_resource_bounds_fail_before_metadata_lookup(monkeypatch, tmp_path, change):
    environment = _runtime_fixture(monkeypatch, tmp_path)
    path = environment.site / "anyio/probe.py"
    path.write_text("# synthetic, never imported\n", encoding="ascii")
    changes = {
        "owner": {"st_uid": SERVICE}, "writable_file": {"st_mode": stat.S_IFREG | 0o664},
        "symlink": {"st_mode": stat.S_IFLNK | 0o777}, "fifo": {"st_mode": stat.S_IFIFO | 0o600},
        "hardlink": {"st_nlink": 2}, "size": {"st_size": 64 * 1024 * 1024 + 1},
    }
    if change == "writable_directory":
        environment.stat_changes[environment.site / "anyio"] = {"st_mode": stat.S_IFDIR | 0o775}
    elif change == "pth":
        path.with_suffix(".pth").write_text("# never executed\n", encoding="ascii")
    elif change == "count":
        # Model a bounded enumeration without creating thousands of fixture files.
        environment.children[environment.site / "anyio"] = [path] * 5001
    else:
        environment.stat_changes[path] = changes[change]
    with pytest.raises(host.HostError, match="untrusted runtime (entry|file)"):
        host._runtime(environment.root)
    assert environment.metadata_calls == [] and environment.sys.path == ["unchanged-sentinel"]


@pytest.mark.parametrize("change", ["missing", "extra", "duplicate", "foreign_name"])
def test_runtime_scoped_distributions_must_match_exactly_once_each(monkeypatch, tmp_path, change):
    environment = _runtime_fixture(monkeypatch, tmp_path)
    if change == "missing":
        environment.rows.pop()
    elif change == "extra":
        environment.rows.append(SimpleNamespace(metadata={"Name": "foreign"}, version="1.0"))
    elif change == "duplicate":
        environment.rows.append(environment.rows[0])
    else:
        environment.rows[0].metadata["Name"] = "foreign"
    with pytest.raises(host.HostError, match="service dependency version mismatch"):
        host._runtime(environment.root)
    assert environment.metadata_calls == [[str(environment.site)]]
    assert environment.sys.path == ["unchanged-sentinel"]


def test_example_is_deliberately_inactive_and_contains_no_credentials():
    body = (ARTIFACTS / "host.example.json").read_bytes()
    example = host._json(body)
    assert example["format"] == host.FORMAT
    assert example["generation"].startswith("REPLACE_")
    assert all(example[name] is None for name in ("custodian_uid", "writer_uid", "socket_gid"))
    assert set(example["s3"]) == {"endpoint", "region", "bucket", "key"}
    assert "REPLACE_" in example["s3"]["bucket"]
    assert not re.search(r'"(?:access_key|secret_key|password|token)"\s*:', body.decode())
    with pytest.raises(host.HostError):
        host.parse_config(body)


def _unit():
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    parser.read_string((ARTIFACTS / "pult-erasure-custodian.service").read_text(encoding="utf-8"))
    assert set(parser.sections()) == {"Unit", "Service"}
    return dict(parser["Service"])


def test_systemd_entrypoints_use_isolated_python_and_explicit_projected_credential():
    service = _unit()
    common = ["/opt/pult-erasure-custodian/venv/bin/python", "-I", "-S", "-B",
              "/opt/pult-erasure-custodian/ops/backup/erasure_custodian_host.py",
              "--config", "/etc/pult-erasure-custodian/host.json", "--reader-credential-file",
              "${CREDENTIALS_DIRECTORY}/journal-reader"]
    assert shlex.split(service["ExecStartPre"]) == common + ["--check"]
    assert shlex.split(service["ExecStart"]) == common + ["--serve"]
    assert service["LoadCredential"] == "journal-reader:/etc/pult-erasure-custodian/reader.json"
    assert service["WorkingDirectory"] == "/opt/pult-erasure-custodian"
    assert service["User"] == "pult-erasure-custodian" and service["Group"] == "pult-erasure-writer"
    assert service["SupplementaryGroups"] == ""


def test_systemd_has_no_bootstrap_install_restart_or_managed_runtime_cleanup():
    service = _unit()
    assert service["Type"] == "exec" and service["Restart"] == "no"
    assert {name for name in service if name.startswith("Exec")} == {"ExecStartPre", "ExecStart"}
    forbidden = {"RuntimeDirectory", "RuntimeDirectoryPreserve", "StateDirectory", "CacheDirectory",
                 "LogsDirectory", "ConfigurationDirectory", "DynamicUser", "PrivateUsers", "RemoveIPC",
                 "Environment", "EnvironmentFile", "PassEnvironment", "SetCredential", "SetCredentialEncrypted"}
    assert not (forbidden & service.keys())
    assert {name for name in service if name.startswith("LoadCredential")} == {"LoadCredential"}


def test_systemd_hardening_preserves_only_required_state_socket_and_network_rights():
    service = _unit()
    for name, expected in {
        "UMask": "0077", "NoNewPrivileges": "yes", "CapabilityBoundingSet": "", "AmbientCapabilities": "",
        "ProtectSystem": "strict", "ProtectHome": "yes", "PrivateTmp": "yes", "PrivateDevices": "yes",
        "ProtectKernelTunables": "yes", "ProtectKernelModules": "yes", "ProtectControlGroups": "yes",
        "ProtectProc": "invisible", "RestrictNamespaces": "yes", "RestrictSUIDSGID": "yes",
        "LockPersonality": "yes", "SystemCallArchitectures": "native", "LimitCORE": "0", "StandardInput": "null",
    }.items():
        assert service[name] == expected
    assert shlex.split(service["ReadWritePaths"]) == ["/var/lib/pult-erasure-custodian", "/run/pult-erasure-custodian"]
    assert set(service["RestrictAddressFamilies"].split()) == {"AF_UNIX", "AF_INET", "AF_INET6"}
    assert service["StandardOutput"] == service["StandardError"] == "journal"


def test_parser_error_is_fixed_and_never_echoes_its_diagnostic(capsys):
    with pytest.raises(host.HostError, match="^invalid host invocation$"):
        host._Parser().error("fixture-private-marker")
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("arguments", [[], ["--fixture-private-marker"],
    ["--config", "fixture-private-marker", "--reader-credential-file", "fixture-private-reader"],
    ["--config", "fixture-private-marker", "--reader-credential-file", "fixture-private-reader", "--check", "--serve"],
    ["--config", "fixture-private-marker", "--reader-credential-file", "fixture-private-reader", "--check", "--unknown"],
])
def test_invalid_cli_never_builds_host_or_echoes_argv(monkeypatch, capsys, arguments):
    calls = []
    monkeypatch.setattr(host, "build_host", lambda *args: calls.append(args))
    assert host.main(arguments) == 1
    assert calls == []
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "HOST_REFUSED\n"


@pytest.mark.parametrize("mode,serve_error,close_error", [
    ("--check", False, False), ("--check", False, True),
    ("--serve", False, False), ("--serve", False, True),
    ("--serve", True, False), ("--serve", True, True),
])
def test_main_waits_for_cleanup_and_never_leaks_failure_details(monkeypatch, capsys, mode, serve_error, close_error):
    calls, output_at_close = [], []

    def serve():
        calls.append("serve")
        if serve_error:
            raise RuntimeError("fixture-private-service-secret")

    def close():
        calls.append("close")
        output_at_close.append(capsys.readouterr())
        if close_error:
            raise OSError("fixture-private-reader-secret")

    monkeypatch.setattr(host, "build_host", lambda *args: (SimpleNamespace(serve=serve), SimpleNamespace(close=close)))
    result = host.main(["--config", "fixture-private-config", "--reader-credential-file", "fixture-private-credential", mode])
    assert calls == (["serve", "close"] if mode == "--serve" else ["close"])
    assert output_at_close == [("", "")]  # no success emitted before close completes
    captured = capsys.readouterr()
    if serve_error or close_error:
        assert result == 1
        assert captured.out == "" and captured.err == "HOST_REFUSED\n"
    else:
        assert result == 0 and captured.err == ""
        assert captured.out == ("HOST_PREFLIGHT_OK_OFFLINE_ONLY\n" if mode == "--check" else "")


def _synthetic_ci():
    workflow = yaml.load((ROOT / ".github/workflows/backup_restore_synthetic.yml").read_text(encoding="utf-8"),
                         Loader=yaml.BaseLoader)
    assert set(workflow["jobs"]) == {"synthetic"}
    job = workflow["jobs"]["synthetic"]
    names = [step["name"] for step in job["steps"]]
    assert len(names) == len(set(names))
    return workflow, job, {step["name"]: step for step in job["steps"]}


def test_synthetic_ci_preserves_normal_offline_tests_and_original_real_uid_proof():
    workflow, job, steps = _synthetic_ci()
    assert workflow["permissions"] == {"contents": "read"}
    assert not ({"if", "continue-on-error"} & job.keys())
    offline = steps["Install backend deps + offline contract guard"]
    assert set(offline) == {"name", "working-directory", "run"}
    assert offline["working-directory"] == "backend"
    commands = offline["run"].splitlines()
    assert shlex.split(commands[0]) == ["pip", "install", "--require-hashes", "-r", "requirements-ci.lock"]
    tests = ["test_backup_restore_contract_guard.py", "test_backup_verifier.py", "test_restore_erasure_checkpoint.py",
             "test_erasure_journal.py", "test_erasure_journal_s3.py", "test_erasure_anchor.py",
             "test_erasure_anchor_process.py", "test_erasure_custodian.py", "test_erasure_custodian_host.py"]
    assert len(commands) == 2
    assert shlex.split(commands[1]) == ["python", "-m", "pytest", *("tests/" + name for name in tests), "-q"]
    name = "Synthetic Linux custodian isolation (real distinct unprivileged UIDs)"
    assert steps[name] == {
        "name": name,
        "run": 'sudo -n -- "$(command -v python)" -I -B ops/backup/tests/custodian_isolation_proof.py',
    }


def test_synthetic_host_ci_is_exact_bounded_preparation_without_test_bypass():
    _, _, steps = _synthetic_ci()
    name = "Synthetic custodian host preflight (minimal pinned runtime, no external IO)"
    step = steps[name]
    assert set(step) == {"name", "run"}  # no condition, alternate env or continue-on-error
    assert step["run"].splitlines() == [
        "set -euo pipefail",
        'RUNTIME="$(mktemp -d /tmp/pult-custodian-host-runtime.XXXXXX)"',
        'case "$RUNTIME" in /tmp/pult-custodian-host-runtime.*) ;; *) exit 1 ;; esac',
        'python -m venv --without-pip "$RUNTIME/venv"',
        'python -m pip --python "$RUNTIME/venv" install --ignore-installed --only-binary=:all: --no-deps --require-hashes -r ops/backup/custodian/requirements.lock',
        'sudo -n chown -hR root:root -- "$RUNTIME"',
        'sudo -n chmod 0755 "$RUNTIME"',
        'sudo -n -- "$RUNTIME/venv/bin/python" -I -B ops/backup/tests/custodian_host_proof.py',
    ]
    names = list(steps)
    assert names.index("Install backend deps + offline contract guard") < names.index(
        "Synthetic Linux custodian isolation (real distinct unprivileged UIDs)") < names.index(name)
    assert names.index(name) < names.index("Build backup runner image (pinned base + pinned rclone/age, hash-verified)")


def test_synthetic_ci_host_triggers_remain_scoped_and_cover_both_proofs():
    workflow, _, _ = _synthetic_ci()
    assert set(workflow["on"]) == {"pull_request", "push", "workflow_dispatch"}
    assert workflow["on"]["push"]["branches"] == ["master"]
    for event in ("pull_request", "push"):
        paths = set(workflow["on"][event]["paths"])
        assert {"ops/backup/**", "backend/tests/test_erasure_custodian.py",
                "backend/tests/test_erasure_custodian_host.py", "docs/erasure-custodian-deployment.md",
                ".github/workflows/backup_restore_synthetic.yml"} <= paths
        assert not ({"*", "**", "backend/**", "ops/**", "docs/**"} & paths)
