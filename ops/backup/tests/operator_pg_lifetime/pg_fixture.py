"""Owned synthetic PG16 helper; Unix sockets only, no production credentials.

Only the root gate calls this API. observe() must precede close(); close never
terminates readers or deletes the native scope/data evidence. Docker output is
never included in exceptions. No operation addresses a container by prefix.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import time
from uuid import uuid4

IMAGE = "postgres:16-alpine@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777"
LABEL = "pult.operator-pg-lifetime.fixture"
DATA = "/var/lib/postgresql/data"
SOCKET = "/var/run/postgresql"
CASES = ("authorized", "close_then_raise", "blocked_close_hang", "controller_death", "wrong_peer", "restored")
ACCOUNT = "c7461350-9188-4bf1-bc6c-c4a5b9455bec"
DRAFT = "ff636b10-6ccb-4b1b-9394-df59af74b793"
ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
DOCKER = ("/usr/bin/docker", "--host=unix:///var/run/docker.sock")
# Bounded initdb/readiness preparation only; no worker is admitted yet. The
# independent operator's 15-second runtime and 2-second stop limits are unchanged.
STARTUP_TIMEOUT = 60


class Refused(RuntimeError):
    """Fixed diagnostics only; never contains Docker/PG stderr, SQL or passwords."""


def _need(value, reason):
    if not value:
        raise Refused(reason)


def _canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)+"\n").encode("ascii")


def _identity(path):
    info = path.lstat()
    return dict(device=info.st_dev, inode=info.st_ino)


def _ident(value):
    _need(type(value) is str and re.fullmatch("[a-z][a-z0-9_]{0,62}", value), "sql_identifier_refused")
    return value


class PGFixture:
    def __init__(self, root: Path, tag: str):
        root = Path(root)
        _need(os.getuid() == os.geteuid() == 0 and root.is_absolute()
              and root.parent == Path("/tmp") and root.name.startswith("pult-op-pg-")
              and root.resolve() == root and not root.is_symlink(), "native_owned_scope_required")
        info = root.lstat()
        _need(stat.S_ISDIR(info.st_mode) and info.st_uid == info.st_gid == 0
              and stat.S_IMODE(info.st_mode) == 0o755, "protected_scope_required")
        _need(type(tag) is str and re.fullmatch("[a-zA-Z0-9_-]{1,64}", tag), "fixture_tag_refused")
        self.root, self.root_id = root, _identity(root)
        self.token = uuid4().hex
        self.tag = tag
        self.cid = self.probe = self.record = self.probe_record = None
        self.image_id = self.case = self.pg = self.locker = self.locker_identity = None
        self.started = self.closed = self.observed = False
        self.observation = None
        self.data, self.socket = root/"pgdata", root/"pgsocket"
        self.ids = {}
        self.database = _ident("op_pg_"+self.token)
        self.reader = _ident("op_reader_"+self.token)
        self.application = _ident("op_review_"+self.token)
        self.locker_app = _ident("op_locker_"+self.token)
        self.password = "synthetic_"+uuid4().hex

    def __repr__(self):
        return "<OwnedSyntheticPGFixture unix-only>"

    def _root(self):
        _need(_identity(self.root) == self.root_id and self.root.resolve() == self.root,
              "scope_identity_changed")
        for path, identity in self.ids.items():
            _need(_identity(path) == identity and not path.is_symlink(), "bind_identity_changed")

    def _docker(self, args, *, data=None, timeout=8, check=True):
        try:
            inputs = {"input": data} if data is not None else {"stdin": subprocess.DEVNULL}
            result = subprocess.run([*DOCKER, *args], **inputs,
                capture_output=True, timeout=timeout, env=ENV, close_fds=True)
        except (OSError, subprocess.SubprocessError):
            raise Refused("owned_docker_command_failed") from None
        _need(len(result.stdout) <= 1048576 and len(result.stderr) <= 1048576,
              "docker_output_bound")
        if check:
            _need(result.returncode == 0, "owned_docker_command_refused")
        return result

    def _inspect(self, cid):
        _need(type(cid) is str and re.fullmatch("[0-9a-f]{64}", cid), "exact_container_id_required")
        try:
            rows = json.loads(self._docker(["inspect", "--type=container", cid]).stdout)
            _need(type(rows) is list and len(rows) == 1 and rows[0]["Id"] == cid,
                  "container_identity_refused")
            return rows[0]
        except (KeyError, TypeError, ValueError):
            raise Refused("container_inspect_refused") from None

    @staticmethod
    def _mounts(view):
        fields = ("Type", "Source", "Destination", "Mode", "RW", "Propagation")
        return sorted(({key: item.get(key) for key in fields} for item in view["Mounts"]),
                      key=lambda item: item["Destination"])

    def _verify(self, cid, record=None):
        self._root()
        view = self._inspect(cid)
        config, host = view["Config"], view["HostConfig"]
        _need(config["Labels"].get(LABEL) == self.token and view["Image"] == self.image_id
              and config["Image"] == IMAGE and config["User"] == "postgres"
              and host["NetworkMode"] == "none" and host["ReadonlyRootfs"] is True
              and not host.get("PortBindings")
              and not host.get("Privileged")
              and {v.lower() for v in host.get("CapDrop", [])} == {"all"}
              and "no-new-privileges:true" in host.get("SecurityOpt", []), "owned_container_policy_changed")
        _need(not any(view.get("NetworkSettings", {}).get("Ports", {}).values()), "published_ports_refused")
        mounts = self._mounts(view)
        if record is not None:
            _need(record == dict(cid=cid, image=self.image_id, labels=config["Labels"], mounts=mounts),
                  "container_creation_identity_changed")
        return view

    def _write(self, path, raw):
        with path.open("xb") as stream:
            os.fchmod(stream.fileno(), 0o444)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())

    def _create(self, name, mounts, script):
        args = ["create", "--pull=never", "--name="+name, "--network=none", "--read-only",
                "--cap-drop=ALL", "--security-opt=no-new-privileges:true", "--user=postgres",
                "--label="+LABEL+"="+self.token, "--label=pult.operator-pg-lifetime.tag="+self.tag,
                "--pids-limit=64", "--memory=256m", "--entrypoint=/bin/sh"]
        for source, destination, readonly in mounts:
            args += ["--mount=type=bind,source="+str(source)+",target="+destination+(",readonly" if readonly else "")]
        args += [IMAGE, "-ceu", script]
        cid = self._docker(args).stdout.decode("ascii").strip()
        _need(re.fullmatch("[0-9a-f]{64}", cid), "container_create_id_refused")
        return cid

    def _record(self, cid, expected):
        view = self._verify(cid)
        mounts = self._mounts(view)
        _need(len(mounts) == len(expected) and all(
            item["Type"] == "bind" and type(item["Source"]) is str and item["Source"]
            and (item["Destination"], item["RW"]) in expected for item in mounts), "unexpected_container_mount")
        return dict(cid=cid, image=self.image_id, labels=view["Config"]["Labels"], mounts=mounts)

    def _psql(self, sql, *, database=None, timeout=5):
        self._verify(self.cid, self.record)
        args = ["exec", "-i", "--user=postgres", "--env=PGAPPNAME=op_owner_"+self.token,
            self.cid, "psql", "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1",
            "-h", SOCKET, "-U", "postgres", "-d", _ident(database or self.database)]
        prefix = "SET statement_timeout='2s'; SET lock_timeout='1s'; SET timezone='UTC';\n"
        return self._docker(args, data=(prefix+sql+"\n").encode("ascii"), timeout=timeout).stdout.strip()

    def _remove(self, cid, record):
        view = self._verify(cid, record)
        if view["State"]["Running"]:
            self._docker(["stop", "--time=2", cid], timeout=8)
        view = self._verify(cid, record)
        _need(view["State"]["Running"] is False and not view["State"].get("Restarting"), "container_stop_uncertain")
        self._docker(["rm", cid])
        result = self._docker(["inspect", "--type=container", cid], check=False)
        _need(result.returncode != 0 and result.stdout.strip() in (b"", b"[]"), "container_removal_uncertain")

    def start(self, case: str) -> dict:
        """Create one exact pinned server and committed synthetic seed."""
        _need(not self.started and not self.closed and case in CASES, "fresh_pg_fixture_required")
        self.started, self.case = True, case
        try:
            return self._start()
        except BaseException:
            # No worker has been admitted on this failed setup. Stop only this
            # exact CID; retain data/root and all identity records for review.
            if self.cid is not None:
                self._remove(self.cid, self.record)
                self.cid = None
            if self.probe is not None:
                self._remove(self.probe, self.probe_record)
                self.probe = None
            raise Refused("owned_pg_setup_failed") from None

    def observe(self) -> dict:
        """Independent owner observation BEFORE any server teardown."""
        return self._observe()

    def hold_lock(self) -> None:
        self._hold_lock()

    def release_lock(self) -> None:
        self._release_lock()

    def close(self) -> None:
        if self.closed:
            return
        _need(self.observed, "pre_teardown_observation_required")
        _need(self.locker is None, "release_owned_locker_first")
        latest = self._observe()
        _need(not latest["reader_backends"], "reader_leak_retained_before_teardown")
        _need(latest["rows_sha256"] == self.baseline, "changed_application_rows_retained")
        if self.cid is not None:
            self._remove(self.cid, self.record)
            self.cid = None
        self.closed = True

    def _start(self):
        self._root()
        rows = json.loads(self._docker(["image", "inspect", IMAGE]).stdout)
        _need(type(rows) is list and len(rows) == 1, "cached_pinned_image_required")
        image = rows[0]
        _need(re.fullmatch(r"sha256:[0-9a-f]{64}", image["Id"])
              and any(item.endswith("@"+IMAGE.split("@", 1)[1])
                      for item in image.get("RepoDigests", [])), "cached_image_digest_refused")
        self.image_id = image["Id"]
        self._write(self.root/".pg-fixture-root", (self.token+"\n").encode("ascii"))
        self.probe = self._create("pult-op-pg-probe-"+self.token,
            [(self.root, DATA, True)],
            "cat "+DATA+"/.pg-fixture-root; id -u; id -g")
        self.probe_record = self._record(self.probe, {(DATA, False)})
        result = self._docker(["start", "--attach", self.probe], timeout=8)
        parts = result.stdout.decode("ascii").splitlines()
        _need(len(parts) == 3 and parts[0] == self.token
              and all(re.fullmatch("[0-9]{1,10}", item) for item in parts[1:]), "postgres_numeric_identity_refused")
        uid, gid = map(int, parts[1:])
        _need(0 < uid < 2**31 and 0 < gid < 2**31
              and self._verify(self.probe, self.probe_record)["State"]["ExitCode"] == 0,
              "unprivileged_postgres_identity_required")
        self._remove(self.probe, self.probe_record)
        self.probe = None
        for path, mode in ((self.data, 0o700), (self.socket, 0o755)):
            path.mkdir(mode=mode)
            os.chown(path, uid, gid)
            path.chmod(mode)
            self.ids[path] = _identity(path)
        self._write(self.socket/".fixture-identity", (self.token+"\n").encode("ascii"))
        script = (
            "initdb -D "+DATA+" --username=postgres --auth-local=trust --auth-host=reject "
            "--encoding=UTF8 --no-locale >/dev/null; "
            "printf '%s\\n' 'local all postgres trust' 'local all all scram-sha-256' > "+DATA+"/pg_hba.conf; "
            "exec postgres -D "+DATA+" -c listen_addresses='' -c unix_socket_directories="+SOCKET+
            " -c unix_socket_permissions=0777 -c logging_collector=off -c log_statement=none "
            "-c log_min_error_statement=panic -c password_encryption=scram-sha-256"
        )
        self.cid = self._create("pult-op-pg-"+self.token,
            [(self.data, DATA, False), (self.socket, SOCKET, False)], script)
        self.record = self._record(self.cid, {(DATA, True), (SOCKET, True)})
        self._write(self.root/"pg-container.json", _canonical(self.record))
        self._docker(["start", self.cid])
        until = time.monotonic()+STARTUP_TIMEOUT
        while True:
            self._verify(self.cid, self.record)
            ready = self._docker(["exec", "--user=postgres", self.cid, "pg_isready", "-q",
                "-h", SOCKET, "-U", "postgres", "-d", "postgres"], timeout=3, check=False)
            if ready.returncode == 0:
                break
            _need(time.monotonic() < until, "pg_start_deadline")
            time.sleep(.1)
        # Mark AFTER initdb (which requires empty PGDATA). Reading both markers
        # through the container proves the actual host binds despite Source path
        # translation; subsequent checks pin initial inspected mount metadata.
        self._write(self.data/".fixture-identity", (self.token+"\n").encode("ascii"))
        for destination in (DATA, SOCKET):
            actual = self._docker(["exec", "--user=postgres", self.cid,
                "/bin/cat", destination+"/.fixture-identity"]).stdout
            _need(actual == (self.token+"\n").encode("ascii"), "native_bind_identity_refused")
        self._psql("CREATE ROLE "+self.reader+" NOINHERIT LOGIN NOSUPERUSER NOCREATEDB "
            "NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '"+self.password+"';\n"
            "CREATE DATABASE "+self.database+" OWNER postgres;\n"
            "REVOKE ALL ON DATABASE "+self.database+" FROM PUBLIC;\n"
            "GRANT CONNECT ON DATABASE "+self.database+" TO "+self.reader+";", database="postgres")
        self._seed()
        oid = self._psql("SELECT oid FROM pg_database WHERE datname='"+self.database+"';").decode("ascii")
        _need(re.fullmatch("[0-9]{1,10}", oid) and 16384 <= int(oid) < 2**32, "database_oid_refused")
        self.pg = dict(socket=str(self.socket), database=self.database, database_oid=int(oid),
            reader_role=self.reader, reader_password=self.password, application_name=self.application)
        self.baseline = self._observe()["rows_sha256"]
        self.observed = False  # Setup baseline is not the gate's post-worker verdict.
        return dict(self.pg)

    def _seed(self):
        # Same permissive two-table schema and sensitive dummy columns as the
        # accepted test_erasure_operator_review_pg.py; never migrations/live data.
        sql = """
BEGIN;
CREATE TABLE public.users (
 id varchar(36) PRIMARY KEY, deleted_at timestamp,
 closure_policy_version varchar(32), closure_started_at timestamptz,
 closure_restore_requested_at timestamptz, closure_purge_started_at timestamptz,
 email text, name text, hashed_password text, verification_token text,
 reset_token text, registered_ip text);
CREATE TABLE public.erasure_preparations (
 id varchar(36) PRIMARY KEY,
 user_id varchar(36) NOT NULL UNIQUE REFERENCES public.users(id),
 closure_policy_version varchar(32) NOT NULL, closure_started_at timestamptz NOT NULL,
 prepared_at timestamptz NOT NULL, state varchar(16) NOT NULL);
INSERT INTO public.users (id, deleted_at, closure_policy_version, closure_started_at,
 email, name, hashed_password, verification_token, reset_token, registered_ip)
VALUES ('ACCOUNT', (transaction_timestamp()-interval '31 days') AT TIME ZONE 'UTC',
 'normal-close-30d-v1', transaction_timestamp()-interval '31 days',
 'SYNTHETIC_ONLY','SYNTHETIC_ONLY','SYNTHETIC_ONLY','SYNTHETIC_ONLY','SYNTHETIC_ONLY','SYNTHETIC_ONLY');
INSERT INTO public.erasure_preparations
 (id,user_id,closure_policy_version,closure_started_at,prepared_at,state)
SELECT 'DRAFT_ID', id, closure_policy_version, closure_started_at,
 transaction_timestamp()-interval '1 hour','DRAFT' FROM public.users;
""".replace("ACCOUNT", ACCOUNT).replace("DRAFT_ID", DRAFT)
        if self.case == "restored":
            sql += "UPDATE public.users SET closure_restore_requested_at=transaction_timestamp();\n"
        sql += ("REVOKE ALL ON SCHEMA public FROM PUBLIC;\n"
            "GRANT USAGE ON SCHEMA public TO "+self.reader+";\n"
            "GRANT SELECT ON public.users, public.erasure_preparations TO "+self.reader+";\n"
            "ALTER ROLE "+self.reader+" SET default_transaction_read_only=on;\nCOMMIT;")
        self._psql(sql)

    def _observe(self):
        _need(self.pg is not None and not self.closed, "live_owned_pg_required")
        sql = (
            "SELECT json_build_object('reader_backends',COALESCE((SELECT json_agg(x ORDER BY x.pid) FROM "
            "(SELECT pid,usename,application_name,state,backend_start,xact_start,client_addr "
            "FROM pg_stat_activity WHERE (usename='"+self.reader+"' OR application_name='"+self.application+
            "')) x),'[]'::json),'rows',json_build_object('users',"
            "COALESCE((SELECT json_agg(u ORDER BY u.id) FROM public.users u),'[]'::json),"
            "'erasure_preparations',COALESCE((SELECT json_agg(e ORDER BY e.id) "
            "FROM public.erasure_preparations e),'[]'::json)));"
        )
        try:
            raw = json.loads(self._psql(sql))
            _need(set(raw) == {"reader_backends", "rows"} and type(raw["reader_backends"]) is list,
                  "owner_observation_shape_refused")
            answer = dict(reader_backends=raw["reader_backends"],
                rows_sha256=hashlib.sha256(_canonical(raw["rows"])).hexdigest())
        except (ValueError, TypeError, KeyError):
            raise Refused("owner_observation_refused") from None
        self.observation, self.observed = answer, True
        return answer

    def _hold_lock(self):
        _need(self.case == "blocked_close_hang" and self.pg is not None
              and self.locker is None and self.locker_identity is None, "fresh_owned_locker_required")
        self._verify(self.cid, self.record)
        args = [*DOCKER, "exec", "-i", "--user=postgres",
            "--env=PGAPPNAME="+self.locker_app, self.cid, "psql", "-X", "-q", "-A", "-t",
            "-v", "ON_ERROR_STOP=1", "-h", SOCKET, "-U", "postgres", "-d", self.database]
        self.locker = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=ENV, close_fds=True)
        sql = ("SET statement_timeout='3s'; SET timezone='UTC'; BEGIN; "
            "LOCK TABLE public.users IN ACCESS EXCLUSIVE MODE; "
            "SELECT json_build_object('pid',pid,'backend_start',backend_start::text,"
            "'application_name',application_name,'database',datname,'username',usename) "
            "FROM pg_stat_activity WHERE pid=pg_backend_pid();\n")
        self.locker.stdin.write(sql.encode("ascii"))
        self.locker.stdin.flush()
        selector = selectors.DefaultSelector()
        line = bytearray()
        try:
            selector.register(self.locker.stdout, selectors.EVENT_READ)
            until = time.monotonic()+5
            while len(line) < 2048:
                _need(selector.select(max(0, until-time.monotonic())), "locker_ready_deadline")
                part = os.read(self.locker.stdout.fileno(), 1)
                _need(part, "locker_ready_eof")
                if part == b"\n":
                    break
                line.extend(part)
            else:
                raise Refused("locker_output_bound")
            record = json.loads(line)
            _need(set(record) == {"pid", "backend_start", "application_name", "database", "username"}
                and type(record["pid"]) is int and record["pid"] > 0
                and re.fullmatch(r"[0-9 :.\-+]+", record["backend_start"])
                and record["application_name"] == self.locker_app
                and record["database"] == self.database and record["username"] == "postgres", "locker_identity_refused")
            self.locker_identity = record
            self._write(self.root/"pg-locker.json", _canonical(record))
        except (ValueError, TypeError, KeyError, OSError):
            raise Refused("owned_locker_setup_failed") from None
        finally:
            selector.close()

    def _release_lock(self):
        if self.locker is None:
            return
        record = self.locker_identity
        _need(record is not None, "unidentified_locker_retained")
        sql = ("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid="+str(record["pid"])+
            " AND backend_start='"+record["backend_start"]+"'::timestamptz"
            " AND datid="+str(self.pg["database_oid"])+" AND usename='postgres'"
            " AND application_name='"+self.locker_app+"';")
        answer = self._psql(sql)
        _need(answer == b"t", "original_owned_locker_not_confirmed")
        try:
            self.locker.communicate(timeout=5)
        except (OSError, subprocess.SubprocessError):
            raise Refused("owned_locker_reap_uncertain") from None
        self.locker = None
