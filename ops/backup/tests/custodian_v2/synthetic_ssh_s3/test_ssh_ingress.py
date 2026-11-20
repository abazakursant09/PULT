"""Portable contract/effects tests; not Linux kernel/deployment proof.

Run: python -I -S -B test_ssh_ingress.py
Only mocks and local in-memory/anonymous descriptor work; no network or cloud.
"""

import copy
from contextlib import ExitStack, contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import stat
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location("private_ssh_ingress", Path(__file__).resolve().parents[5] / "ops/backup/custodian_v2/synthetic_ssh_s3/ssh_ingress.py")
ingress = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ingress
SPEC.loader.exec_module(ingress)


def document():
    return {"format": ingress.FORMAT, "caller_uid": 2101, "caller_gid": 2102,
            "controller": {"pid": 4321, "start_ticks": 998877,
                           "boot": "12345678-1234-5678-90ab-123456789abc"},
            "socket": {"path": "/run/pult-synthetic/ingress.sock", "device": 25, "inode": 43}}


def encoded(value):
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def fake_stat(mode, *, uid=0, gid=0, device=25, inode=43, size=100):
    return SimpleNamespace(st_mode=mode, st_uid=uid, st_gid=gid, st_dev=device,
                           st_ino=inode, st_size=size, st_mtime_ns=10, st_ctime_ns=20)


@contextmanager
def parent(_path):
    yield 91, "fixed"


def proc_stat(pid=4321, start=998877):
    # State is field 3; starttime is field 22.
    return str(pid).encode() + b" (odd ) process\nname) S " + b"0 " * 18 + str(start).encode() + b" 0\n"


@contextmanager
def reply_environment(events):
    """Supply Linux-only names to portable mocks, never fake a native proof."""
    with ExitStack() as stack:
        for name, value in (("POLLRDHUP", 0x2000), ("POLLHUP", 0x10),
                            ("POLLERR", 0x08), ("POLLNVAL", 0x20)):
            stack.enter_context(patch.object(ingress.select, name, value, create=True))
        stack.enter_context(patch.object(socket, "MSG_CMSG_CLOEXEC", 0x40000000, create=True))
        poll = stack.enter_context(patch.object(ingress.select, "poll", create=True))
        poll.return_value.poll.return_value = events
        yield poll


class SchemaTests(unittest.TestCase):
    def test_valid_portable_config(self):
        config = ingress.parse_config(encoded(document()))
        self.assertEqual(config.caller_uid, 2101)
        self.assertEqual(config.socket.path, document()["socket"]["path"])
        self.assertEqual(config.controller.start_ticks, 998877)

    def test_missing_extra_nested_keys(self):
        for where in (None, "controller", "socket"):
            for operation in ("missing", "extra"):
                value = document()
                target = value if where is None else value[where]
                if operation == "missing":
                    target.pop(next(iter(target)))
                else:
                    target["profile"] = "other"
                with self.subTest(where=where, operation=operation), self.assertRaises(ingress.Denied):
                    ingress.parse_config(encoded(value))

    def test_numeric_schema_rejects_bool_float_root_and_bounds(self):
        fields = ((None, "caller_uid"), (None, "caller_gid"), ("controller", "pid"),
                  ("controller", "start_ticks"), ("socket", "inode"), ("socket", "device"))
        for where, field in fields:
            for invalid in (True, False, 1.0, "1", None, -1, 1 << 65):
                value = document()
                (value if where is None else value[where])[field] = invalid
                with self.subTest(field=field, value=invalid), self.assertRaises(ingress.Denied):
                    ingress.parse_config(encoded(value))
        for field in ("caller_uid", "caller_gid"):
            value = document()
            value[field] = 0
            with self.assertRaises(ingress.Denied):
                ingress.parse_config(encoded(value))

    def test_dedicated_caller_id_range(self):
        for field in ("caller_uid", "caller_gid"):
            for valid in (1000, (1 << 31) - 1):
                value = document()
                value[field] = valid
                with self.subTest(field=field, valid=valid):
                    self.assertEqual(getattr(ingress.parse_config(encoded(value)), field), valid)
            for invalid in (0, 1, 999, 1 << 31, (1 << 32) - 2):
                value = document()
                value[field] = invalid
                with self.subTest(field=field, invalid=invalid), self.assertRaises(ingress.Denied):
                    ingress.parse_config(encoded(value))

    def test_format_boot_and_objects(self):
        cases = [("format", None, "other"), ("format", None, [ingress.FORMAT]),
                 ("controller", None, []), ("socket", None, None),
                 ("boot", "controller", "12345678-1234-5678-90AB-123456789abc"),
                 ("boot", "controller", "12345678-1234-5678-90ab-123456789abc\n")]
        for field, where, invalid in cases:
            value = document()
            (value if where is None else value[where])[field] = invalid
            with self.subTest(field=field, value=invalid), self.assertRaises(ingress.Denied):
                ingress.parse_config(encoded(value))

    def test_bad_paths(self):
        for path in ("relative", "/", "//run/x", "/run/../x", "/run/./x", "/run//x", "/run/x/",
                     "/run/x\x00", "/run/a\n", "/" + "x" * 107, "/run/\ud800", 12):
            value = document()
            value["socket"]["path"] = path
            with self.subTest(path=repr(path)), self.assertRaises(ingress.Denied):
                ingress.parse_config(encoded(value))

    def test_raw_json_rejections(self):
        raw = encoded(document())
        cases = [b"", b"x" * (ingress.CONFIG_LIMIT + 1), b"\xff", b"\xef\xbb\xbf" + raw,
                 b"null", b"[]", b"1", raw + b"{}", raw.replace(b'"pid":4321', b'"pid":NaN'),
                 raw.replace(b'"pid":4321', b'"pid":Infinity'),
                 raw.replace(b'"pid":4321', b'"pid":4321,"pid":4321'),
                 raw.replace(b'"caller_uid":2101', b'"caller_uid":2101,"caller_uid":2101')]
        for value in cases:
            with self.subTest(value=value[:60]), self.assertRaises(ingress.Denied):
                ingress.parse_config(value)


class AdmissionTests(unittest.TestCase):
    def test_exact_command_only(self):
        ingress.check_command({"SSH_ORIGINAL_COMMAND": ingress.COMMAND})
        for command in (None, "", ingress.COMMAND + "\n", " " + ingress.COMMAND,
                        ingress.COMMAND + " --profile x", ingress.COMMAND + "; id",
                        ingress.COMMAND.upper(), ingress.COMMAND + "\x00", ingress.COMMAND.encode()):
            with self.subTest(command=command), self.assertRaises(ingress.Denied):
                ingress.check_command({"SSH_ORIGINAL_COMMAND": command})
        with self.assertRaises(ingress.Denied):
            ingress.check_command({})

    def test_fragmentation_and_required_eof(self):
        for frames in ([ingress.TRIGGER, b""], [ingress.TRIGGER[:3], ingress.TRIGGER[3:], b""],
                       [bytes([byte]) for byte in ingress.TRIGGER] + [b""]):
            reader = Mock(side_effect=frames)
            ingress._collect_trigger(reader, lambda clock: clock.remaining(), ingress.Deadline(1))
            self.assertEqual(reader.call_count, len(frames))

    def test_rejects_extra_truncated_wrong_and_overlong_input(self):
        cases = [[b""], [ingress.TRIGGER[:-1], b""], [ingress.TRIGGER + b"x"],
                 [ingress.TRIGGER, b"x"], [b"run"], [b"x" * 5000],
                 [ingress.TRIGGER.replace(b"\n", b"\r\n")]]
        for frames in cases:
            with self.subTest(frames=frames), self.assertRaises(ingress.Denied):
                ingress._collect_trigger(Mock(side_effect=frames), lambda clock: clock.remaining(), ingress.Deadline(1))

    def test_stalled_eof_obeys_deadline(self):
        clock = Mock()
        clock.remaining.side_effect = [1, 1, ingress.Denied("deadline")]
        reader = Mock(side_effect=[ingress.TRIGGER])
        with self.assertRaises(ingress.Denied):
            ingress._collect_trigger(reader, lambda value: value.remaining(), clock)
        self.assertEqual(reader.call_count, 1)

    def test_deadline_is_shared_and_not_reset(self):
        with patch.object(ingress.time, "monotonic", side_effect=[100, 101, 104, 105]):
            with ingress.Deadline(5) as clock:
                self.assertEqual(clock.remaining(), 1)
                with self.assertRaises(ingress.Denied):
                    clock.remaining()

    def test_runtime_must_be_fixed_isolated_cpython312_single_main_thread(self):
        flags = SimpleNamespace(isolated=1, no_site=1, dont_write_bytecode=1,
                                ignore_environment=1, no_user_site=1, safe_path=True, optimize=0)
        main_thread = object()
        # Deliberate metadata mocks: this suite is NOT native interpreter proof.
        with patch.object(ingress.sys, "platform", "linux"), patch.object(ingress.sys, "version_info", (3, 12)), \
                patch.object(ingress.sys, "implementation", SimpleNamespace(name="cpython")), \
                patch.object(ingress.sys, "executable", "/usr/bin/python3.12"), \
                patch.object(ingress.sys, "flags", flags), \
                patch.object(ingress.threading, "main_thread", return_value=main_thread), \
                patch.object(ingress.threading, "current_thread", return_value=main_thread) as current, \
                patch.object(ingress.threading, "active_count", return_value=1) as count, \
                patch.object(ingress, "_read_proc", return_value=b"Threads:\t1\n") as read:
            ingress._check_runtime()
            for name in vars(flags):
                if name == "optimize":
                    continue
                changed = copy.copy(flags)
                setattr(changed, name, 0)
                with self.subTest(flag=name), patch.object(ingress.sys, "flags", changed), self.assertRaises(ingress.Denied):
                    ingress._check_runtime()
            for optimization in (1, 2):
                changed = copy.copy(flags)
                changed.optimize = optimization
                with self.subTest(optimize=optimization), patch.object(ingress.sys, "flags", changed), self.assertRaises(ingress.Denied):
                    ingress._check_runtime()
            with patch.object(ingress.sys, "version_info", (3, 13)), self.assertRaises(ingress.Denied):
                ingress._check_runtime()
            with patch.object(ingress.sys, "platform", "win32"), self.assertRaises(ingress.Denied):
                ingress._check_runtime()
            with patch.object(ingress.sys, "implementation", SimpleNamespace(name="pypy")), self.assertRaises(ingress.Denied):
                ingress._check_runtime()
            for executable in ("/usr/local/bin/python3.12", "/usr/bin/python3", "python3.12", "", None):
                with self.subTest(executable=executable), patch.object(ingress.sys, "executable", executable), self.assertRaises(ingress.Denied):
                    ingress._check_runtime()
            current.return_value = object()
            with self.assertRaises(ingress.Denied):
                ingress._check_runtime()
            current.return_value = main_thread
            count.return_value = 2
            with self.assertRaises(ingress.Denied):
                ingress._check_runtime()
            count.return_value = 1
            for status in (b"", b"Threads:\t2\n", b"Threads:\t01\n", b"Threads:\t1\nThreads:\t1\n"):
                read.return_value = status
                with self.subTest(status=status), self.assertRaises(ingress.Denied):
                    ingress._check_runtime()

    def test_uid_gid_exact_primary_groups_nnp_and_active_cap_sets(self):
        config = ingress.parse_config(encoded(document()))
        caps = b"".join(name + b":\t0000000000000000\n" for name in
                        (b"CapInh", b"CapPrm", b"CapEff", b"CapBnd", b"CapAmb"))
        caps += b"Uid:\t2101\t2101\t2101\t2101\nGid:\t2102\t2102\t2102\t2102\n"
        caps += b"NoNewPrivs:\t1\n"
        with patch.object(ingress.os, "getresuid", return_value=(2101,) * 3, create=True) as uid, \
                patch.object(ingress.os, "getresgid", return_value=(2102,) * 3, create=True) as gid, \
                patch.object(ingress.os, "getgroups", return_value=[], create=True) as groups, \
                patch.object(ingress, "_read_proc", return_value=caps) as read:
            ingress._check_identity(config)
            # Normal sshd may retain the pinned primary GID in this vector.
            groups.return_value = [2102]
            ingress._check_identity(config)
            groups.return_value = []
            for who, invalid in ((uid, (0,) * 3), (gid, (0,) * 3),
                                 (uid, (0, 2101, 0)), (gid, (2102, 0, 2102)),
                                 (uid, (2101, 2101, 0)), (gid, (2102, 2102, 0)),
                                 (groups, [0]), (groups, [2101]), (groups, [2102, 2103]),
                                 (groups, [2103, 2102]), (groups, [2102, 2102])):
                old = who.return_value
                who.return_value = invalid
                with self.assertRaises(ingress.Denied):
                    ingress._check_identity(config)
                who.return_value = old
            for name in (b"CapInh", b"CapPrm", b"CapEff", b"CapAmb"):
                read.return_value = caps.replace(name + b":\t0000000000000000", name + b":\t0000000000000001")
                with self.subTest(cap=name), self.assertRaises(ingress.Denied):
                    ingress._check_identity(config)
            for ceiling in (b"0000000000000000", b"000001ffffffffff", b"FFFFFFFFFFFFFFFF"):
                read.return_value = caps.replace(b"CapBnd:\t0000000000000000", b"CapBnd:\t" + ceiling)
                with self.subTest(valid_bounding=ceiling):
                    ingress._check_identity(config)
            for ceiling in (b"", b"0", b"0" * 15, b"0" * 17, b"g" * 16, b"0x00000000000000"):
                read.return_value = caps.replace(b"CapBnd:\t0000000000000000", b"CapBnd:\t" + ceiling)
                with self.subTest(invalid_bounding=ceiling), self.assertRaises(ingress.Denied):
                    ingress._check_identity(config)
            for nnp in (b"", b"0", b"01", b"2", b"true"):
                read.return_value = caps.replace(b"NoNewPrivs:\t1", b"NoNewPrivs:\t" + nnp)
                with self.subTest(nnp=nnp), self.assertRaises(ingress.Denied):
                    ingress._check_identity(config)
            for missing_or_duplicate in (caps.replace(b"NoNewPrivs:\t1\n", b""), caps + b"NoNewPrivs:\t1\n",
                                         caps.replace(b"CapBnd:\t0000000000000000\n", b"")):
                read.return_value = missing_or_duplicate
                with self.assertRaises(ingress.Denied):
                    ingress._check_identity(config)
            for old, new in ((b"2101\t2101\t2101\t2101", b"2101\t2101\t2101\t0"),
                             (b"2102\t2102\t2102\t2102", b"2102\t2102\t2102\t0")):
                read.return_value = caps.replace(old, new)
                with self.assertRaises(ingress.Denied):
                    ingress._check_identity(config)
            read.return_value = b""
            with self.assertRaises(ingress.Denied):
                ingress._check_identity(config)


class ProtectionTests(unittest.TestCase):
    def test_directory_walk_nofollow_closes_on_body_and_walk_failure(self):
        for fail in (None, "body", "directory", "open"):
            with self.subTest(fail=fail), \
                    patch.object(ingress.os, "O_DIRECTORY", 0x10000, create=True), \
                    patch.object(ingress.os, "O_NOFOLLOW", 0x20000, create=True), \
                    patch.object(ingress.os, "O_CLOEXEC", 0x80000, create=True), \
                    patch.object(ingress.os, "open", side_effect=[10, OSError("open")] if fail == "open" else [10, 11, 12]) as opened, \
                    patch.object(ingress.os, "close") as closed, \
                    patch.object(ingress, "_protected_directory",
                                 side_effect=[None, ingress.Denied("directory")] if fail == "directory" else None):
                def walk():
                    with ingress._root_parent("/etc/pult/config") as result:
                        self.assertEqual(result, (12, "config"))
                        if fail == "body":
                            raise ingress.Denied("body")
                if fail:
                    with self.assertRaises((ingress.Denied, OSError)):
                        walk()
                else:
                    walk()
                self.assertEqual([call.args[0] for call in closed.call_args_list],
                                 [10] if fail == "open" else [11, 10] if fail == "directory" else [10, 11, 12])
                for call in opened.call_args_list:
                    self.assertTrue(call.args[1] & ingress.os.O_NOFOLLOW)
                    self.assertTrue(call.args[1] & ingress.os.O_DIRECTORY)
                    self.assertTrue(call.args[1] & ingress.os.O_CLOEXEC)

    def test_root_controlled_directories(self):
        for mode, uid in ((stat.S_IFDIR | 0o755, 0), (stat.S_IFDIR | 0o750, 0)):
            with patch.object(ingress.os, "fstat", return_value=fake_stat(mode, uid=uid)):
                ingress._protected_directory(5)
        for mode, uid in ((stat.S_IFDIR | 0o775, 0), (stat.S_IFDIR | 0o1777, 0),
                          (stat.S_IFDIR | 0o755, 2101), (stat.S_IFREG | 0o644, 0)):
            with patch.object(ingress.os, "fstat", return_value=fake_stat(mode, uid=uid)), self.assertRaises(ingress.Denied):
                ingress._protected_directory(5)

    def test_pinned_config_hash_protection_change_and_close(self):
        raw = encoded(document())
        metadata = fake_stat(stat.S_IFREG | 0o644, size=len(raw))
        with patch.object(ingress, "_root_parent", parent), patch.object(ingress.os, "open", return_value=19), \
                patch.object(ingress.os, "fstat", return_value=metadata) as fs, \
                patch.object(ingress, "_read_bounded_fd", return_value=raw) as read, \
                patch.object(ingress.os, "close") as close, \
                patch.object(ingress.os, "O_NOFOLLOW", 0x20000, create=True), \
                patch.object(ingress.os, "O_CLOEXEC", 0x80000, create=True), \
                patch.object(ingress.os, "O_NONBLOCK", 0x800, create=True):
            digest = hashlib.sha256(raw).hexdigest()
            ingress.load_pinned_config("/etc/pult/ingress.json", digest)
            close.assert_called_with(19)
            for bad in ("0" * 64, digest.upper(), "", "sha256:" + digest):
                with self.subTest(hash=bad), self.assertRaises(ingress.Denied):
                    ingress.load_pinned_config("/etc/pult/ingress.json", bad)
            for mode, uid in ((stat.S_IFREG | 0o664, 0), (stat.S_IFREG | 0o644, 2101), (stat.S_IFIFO | 0o644, 0)):
                fs.return_value = fake_stat(mode, uid=uid)
                with self.assertRaises(ingress.Denied):
                    ingress.load_pinned_config("/etc/pult/ingress.json", digest)
                close.assert_called_with(19)
            fs.side_effect = [metadata, fake_stat(stat.S_IFREG | 0o644, size=len(raw) + 1)]
            with self.assertRaises(ingress.Denied):
                ingress.load_pinned_config("/etc/pult/ingress.json", digest)
            close.assert_called_with(19)
            fs.side_effect = None
            fs.return_value = metadata
            read.side_effect = OSError("read failed")
            with self.assertRaises(OSError):
                ingress.load_pinned_config("/etc/pult/ingress.json", digest)
            close.assert_called_with(19)

    def test_socket_root_group_mode_inode_and_device(self):
        config = ingress.parse_config(encoded(document()))
        good = dict(mode=stat.S_IFSOCK | 0o660, uid=0, gid=2102, device=25, inode=43)
        with patch.object(ingress.os, "stat", return_value=fake_stat(**good)):
            ingress._check_socket(91, "fixed", config)
        for name, invalid in (("mode", stat.S_IFSOCK | 0o666), ("mode", stat.S_IFSOCK | 0o640),
                              ("mode", stat.S_IFLNK | 0o660), ("uid", 2101), ("gid", 0),
                              ("device", 26), ("inode", 44)):
            changed = dict(good, **{name: invalid})
            with self.subTest(field=name), patch.object(ingress.os, "stat", return_value=fake_stat(**changed)), \
                    self.assertRaises(ingress.Denied):
                ingress._check_socket(91, "fixed", config)


class PeerTests(unittest.TestCase):
    def test_pidfd_zero_is_owned_and_closed_on_verification_failure(self):
        config = ingress.parse_config(encoded(document()))
        for fail in ("inheritable", "verify"):
            connection = Mock()
            connection.getsockopt.side_effect = [struct.pack("=iII", 4321, 0, 0), struct.pack("i", 0)]
            with self.subTest(fail=fail), patch.object(socket, "SO_PEERCRED", 17, create=True), \
                    patch.object(ingress.os, "set_inheritable", side_effect=OSError("flags") if fail == "inheritable" else None), \
                    patch.object(ingress.os, "close") as close, \
                    patch.object(ingress, "_check_controller_live", side_effect=ingress.Denied("verify")):
                with self.assertRaises((ingress.Denied, OSError)):
                    with ingress._controller_peer(connection, config, ingress.Deadline(1)):
                        self.fail("admitted")
                close.assert_called_once_with(0)

    def test_stat_parsing_and_pid_start_binding(self):
        self.assertEqual(ingress._start_ticks(proc_stat(), 4321), 998877)
        for raw in (proc_stat(pid=123), b"4321 (x) S 0", b"4321 bad", proc_stat(start=0)):
            with self.assertRaises(ingress.Denied):
                ingress._start_ticks(raw, 4321)

    def test_peerpidfd_is_kernel_option_77_and_always_closed(self):
        config = ingress.parse_config(encoded(document()))
        connection = Mock()
        connection.getsockopt.side_effect = [struct.pack("=iII", 4321, 0, 0), struct.pack("i", 55)]
        with patch.object(socket, "SO_PEERCRED", 17, create=True), \
                patch.object(ingress.os, "set_inheritable") as inheritable, \
                patch.object(ingress.os, "close") as close, patch.object(ingress, "_check_controller_live"):
            with self.assertRaises(ingress.Denied):
                with ingress._controller_peer(connection, config, ingress.Deadline(1)) as fd:
                    self.assertEqual(fd, 55)
                    raise ingress.Denied("later failure")
            self.assertEqual(connection.getsockopt.call_args_list[1].args, (socket.SOL_SOCKET, 77, struct.calcsize("i")))
            inheritable.assert_called_once_with(55, False)
            close.assert_called_once_with(55)

    def test_wrong_root_pid_and_unsupported_pidfd_fail_before_send(self):
        config = ingress.parse_config(encoded(document()))
        for credentials in (struct.pack("=iII", 4321, 2101, 0), struct.pack("=iII", 123, 0, 0), b"short"):
            connection = Mock()
            connection.getsockopt.return_value = credentials
            with patch.object(socket, "SO_PEERCRED", 17, create=True), self.assertRaises(ingress.Denied):
                with ingress._controller_peer(connection, config, ingress.Deadline(1)):
                    self.fail("admitted")
            connection.send.assert_not_called()
            self.assertEqual(connection.getsockopt.call_count, 1)
        connection = Mock()
        connection.getsockopt.side_effect = [struct.pack("=iII", 4321, 0, 0), OSError("unsupported")]
        with patch.object(socket, "SO_PEERCRED", 17, create=True), self.assertRaises(OSError):
            with ingress._controller_peer(connection, config, ingress.Deadline(1)):
                self.fail("admitted")
        connection.send.assert_not_called()

    def test_actual_pidfd_boot_start_root_and_liveness(self):
        controller = ingress.parse_config(encoded(document())).controller
        good = {"/proc/self/fdinfo/55": b"Pid:\t4321\n",
                "/proc/sys/kernel/random/boot_id": controller.boot.encode() + b"\n",
                "/proc/4321/stat": proc_stat(), "/proc/4321/status": b"Uid:\t0\t0\t0\t0\n"}
        with patch.object(ingress, "_read_proc", side_effect=lambda path, **_kwargs: good[path]), \
                patch.object(ingress, "_pidfd_live") as live:
            ingress._check_controller_live(55, controller, ingress.Deadline(1))
            self.assertEqual(live.call_count, 2)
            for path, bad in (("/proc/self/fdinfo/55", b"Pid:\t-1\n"),
                              ("/proc/sys/kernel/random/boot_id", b"wrong\n"),
                              ("/proc/4321/stat", proc_stat(start=998878)),
                              ("/proc/4321/status", b"Uid:\t0\t2101\t0\t0\n")):
                original = good[path]
                good[path] = bad
                with self.subTest(path=path), self.assertRaises(ingress.Denied):
                    ingress._check_controller_live(55, controller, ingress.Deadline(1))
                good[path] = original
            live.side_effect = ingress.Denied("exited")
            with self.assertRaises(ingress.Denied):
                ingress._check_controller_live(55, controller, ingress.Deadline(1))


class ReplyTests(unittest.TestCase):
    def test_exact_reply_and_write_eof(self):
        connection = Mock()
        connection.fileno.return_value = 70
        connection.recvmsg.side_effect = [(ingress.STORAGE_ONLY_REPLY, [], 0, None), (b"", [], 0, None)]
        with reply_environment([(70, 0x2000)]) as poll:
            self.assertEqual(ingress._receive_reply(connection, ingress.Deadline(1)), ingress.STORAGE_ONLY_REPLY)
            self.assertEqual(connection.recvmsg.call_count, 2)
            self.assertEqual(connection.recvmsg.call_args_list[0].args[0], len(ingress.STORAGE_ONLY_REPLY) + 1)
            self.assertEqual(connection.recvmsg.call_args_list[1].args[0], 1)
            poll.return_value.poll.assert_called_once_with(0)

    def test_empty_record_without_write_eof_errors_and_timeout_fail(self):
        for events in ([], [(70, 0)], [(70, 0x08)], [(70, 0x2000 | 0x20)]):
            connection = Mock()
            connection.fileno.return_value = 70
            connection.recvmsg.side_effect = [(ingress.STORAGE_ONLY_REPLY, [], 0, None), (b"", [], 0, None)]
            with self.subTest(events=events), reply_environment(events), self.assertRaises(ingress.Denied):
                ingress._receive_reply(connection, ingress.Deadline(1))
        connection = Mock()
        connection.recvmsg.side_effect = [(ingress.STORAGE_ONLY_REPLY, [], 0, None), TimeoutError("EOF")]
        with reply_environment([]), self.assertRaises(TimeoutError):
            ingress._receive_reply(connection, ingress.Deadline(1))

    def test_canonical_reply_is_storage_only_without_authority(self):
        value = json.loads(ingress.STORAGE_ONLY_REPLY)
        self.assertIs(value["purge"], False)
        self.assertIs(value["restore"], False)
        self.assertIs(value["storage_only"], True)
        self.assertEqual(ingress.STORAGE_ONLY_REPLY, encoded(value) + b"\n")

    def test_rejects_noncanonical_authority_overlong_and_ancillary(self):
        bads = [b"", ingress.STORAGE_ONLY_REPLY[:-1], ingress.STORAGE_ONLY_REPLY + b"x",
                ingress.STORAGE_ONLY_REPLY.replace(b'"purge":false', b'"purge":true'),
                ingress.STORAGE_ONLY_REPLY.replace(b'"restore":false', b'"restore":true'),
                ingress.STORAGE_ONLY_REPLY.replace(b'"storage_only":true', b'"storage_only":false'),
                b'{"format": "pult-synthetic-storage-only-reply-v1"}\n', b"PURGE_ALLOWED\n"]
        for raw in bads:
            connection = Mock()
            connection.recvmsg.return_value = (raw, [], 0, None)
            with self.subTest(raw=raw), patch.object(socket, "MSG_CMSG_CLOEXEC", 0x40000000, create=True), \
                    self.assertRaises(ingress.Denied):
                ingress._receive_reply(connection, ingress.Deadline(1))
            self.assertEqual(connection.recvmsg.call_count, 1)

    def test_ancillary_rights_are_closed_on_rejection(self):
        with patch.object(socket, "SCM_RIGHTS", 1, create=True), \
                patch.object(socket, "MSG_TRUNC", 0x20, create=True), \
                patch.object(socket, "MSG_CTRUNC", 0x08, create=True), \
                patch.object(ingress.os, "close") as close:
            with self.assertRaises(ingress.Denied):
                ingress._reject_ancillary([(socket.SOL_SOCKET, socket.SCM_RIGHTS, struct.pack("ii", 51, 52))], 0)
            self.assertEqual([call.args[0] for call in close.call_args_list], [51, 52])
            for flags in (socket.MSG_TRUNC, socket.MSG_CTRUNC):
                with self.assertRaises(ingress.Denied):
                    ingress._reject_ancillary([], flags)
            with self.assertRaises(ingress.Denied):
                ingress._reject_ancillary([(socket.SOL_SOCKET, 9999, b"x")], 0)
            close.reset_mock()
            close.side_effect = [OSError("close"), None]
            with self.assertRaises(ingress.Denied):
                ingress._reject_ancillary([(socket.SOL_SOCKET, socket.SCM_RIGHTS, struct.pack("ii", 51, 52))], 0)
            self.assertEqual([call.args[0] for call in close.call_args_list], [51, 52])

    def test_trailing_record_is_rejected(self):
        connection = Mock()
        connection.recvmsg.side_effect = [(ingress.STORAGE_ONLY_REPLY, [], 0, None), (b"x", [], 0, None)]
        with patch.object(socket, "MSG_CMSG_CLOEXEC", 0x40000000, create=True), self.assertRaises(ingress.Denied):
            ingress._receive_reply(connection, ingress.Deadline(1))


class EffectTests(unittest.TestCase):
    def test_input_duplicate_closes_and_restores_flags_on_failure(self):
        with patch.object(ingress.os, "dup", return_value=41), \
                patch.object(ingress.os, "set_inheritable"), \
                patch.object(ingress.os, "get_blocking", return_value=True), \
                patch.object(ingress.os, "set_blocking") as blocking, \
                patch.object(ingress.os, "fstat", return_value=fake_stat(stat.S_IFIFO | 0o600)), \
                patch.object(ingress.os, "close") as close, \
                patch.object(ingress, "_collect_trigger", side_effect=ingress.Denied("input")):
            with self.assertRaises(ingress.Denied):
                ingress.read_trigger(0, ingress.Deadline(1))
            self.assertEqual([call.args for call in blocking.call_args_list], [(41, False), (41, True)])
            close.assert_called_once_with(41)

    def test_terminal_write_fixed_once_and_duplicate_always_closed(self):
        for written in (len(ingress.STORAGE_ONLY_REPLY), 0):
            with self.subTest(written=written), patch.object(ingress.os, "dup", return_value=42), \
                    patch.object(ingress.os, "set_inheritable"), \
                    patch.object(ingress.os, "get_blocking", return_value=True), \
                    patch.object(ingress.os, "set_blocking"), \
                    patch.object(ingress.os, "write", return_value=written) as write, \
                    patch.object(ingress.os, "close") as close, patch.object(ingress, "_wait"):
                if written:
                    ingress._emit_reply(ingress.Deadline(1))
                else:
                    with self.assertRaises(ingress.Denied):
                        ingress._emit_reply(ingress.Deadline(1))
                write.assert_called_once_with(42, ingress.STORAGE_ONLY_REPLY)
                close.assert_called_once_with(42)

    def test_bad_pin_cannot_connect_or_emit(self):
        config = ingress.parse_config(encoded(document()))
        with patch.object(ingress, "_check_runtime"), patch.object(ingress, "check_command"), \
                patch.object(ingress, "load_pinned_config", return_value=config), \
                patch.object(ingress, "_check_identity"), patch.object(ingress, "read_trigger"), \
                patch.object(ingress, "_root_parent", parent), \
                patch.object(ingress, "_check_socket", side_effect=ingress.Denied("pin")), \
                patch.object(ingress.socket, "socket") as connect, patch.object(ingress, "_emit_reply") as emit:
            self.assertEqual(ingress.main(["/etc/pult/config", "0" * 64]), 1)
            connect.assert_not_called()
            emit.assert_not_called()

    def test_bad_argv_has_no_effects(self):
        with patch.object(ingress, "_run") as run:
            for arguments in ([], ["/etc/pult/config"], ["a", "b", "c"]):
                self.assertEqual(ingress.main(arguments), 1)
            run.assert_not_called()

    def test_bad_command_cannot_read_config_input_connect_or_emit(self):
        with patch.object(ingress, "_check_runtime"), patch.dict(ingress.os.environ, {"SSH_ORIGINAL_COMMAND": "id"}), \
                patch.object(ingress, "load_pinned_config") as config, \
                patch.object(ingress, "read_trigger") as read, patch.object(ingress.socket, "socket") as connect, \
                patch.object(ingress, "_emit_reply") as emit:
            self.assertEqual(ingress.main(["/etc/pult/config", "0" * 64]), 1)
            for effect in (config, read, connect, emit):
                effect.assert_not_called()

    def test_bad_input_cannot_connect_send_or_emit(self):
        config = ingress.parse_config(encoded(document()))
        with patch.object(ingress, "_check_runtime"), patch.object(ingress, "check_command"), \
                patch.object(ingress, "load_pinned_config", return_value=config), \
                patch.object(ingress, "_check_identity"), \
                patch.object(ingress, "read_trigger", side_effect=ingress.Denied("input")), \
                patch.object(ingress.socket, "socket") as connect, patch.object(ingress, "_emit_reply") as emit:
            self.assertEqual(ingress.main(["/etc/pult/config", "0" * 64]), 1)
            connect.assert_not_called()
            emit.assert_not_called()

    def test_fixed_single_send_and_no_retry_or_terminal_on_failure(self):
        config = ingress.parse_config(encoded(document()))

        @contextmanager
        def peer(*_args):
            yield 55

        for fail in (None, "connect", "send", "reply", "live"):
            connection = Mock()
            connection.__enter__ = Mock(return_value=connection)
            connection.__exit__ = Mock(return_value=False)
            connection.send.return_value = len(ingress.TRIGGER)
            if fail == "connect":
                connection.connect.side_effect = OSError("connect")
            if fail == "send":
                connection.send.return_value = len(ingress.TRIGGER) - 1
            with self.subTest(fail=fail), patch.object(ingress, "_check_runtime"), \
                    patch.object(ingress, "check_command"), patch.object(ingress, "load_pinned_config", return_value=config), \
                    patch.object(ingress, "_check_identity"), patch.object(ingress, "read_trigger"), \
                    patch.object(ingress, "_root_parent", parent), patch.object(ingress, "_check_socket"), \
                    patch.object(socket, "AF_UNIX", 1, create=True), \
                    patch.object(socket, "SOCK_SEQPACKET", 5, create=True), \
                    patch.object(socket, "SOCK_CLOEXEC", 0x80000, create=True), \
                    patch.object(ingress.socket, "socket", return_value=connection) as factory, \
                    patch.object(ingress, "_controller_peer", peer), \
                    patch.object(ingress, "_receive_reply", side_effect=ingress.Denied("reply") if fail == "reply" else None), \
                    patch.object(ingress, "_check_controller_live", side_effect=ingress.Denied("live") if fail == "live" else None), \
                    patch.object(ingress, "_emit_reply") as emit:
                self.assertEqual(ingress.main(["/etc/pult/config", "0" * 64]), 0 if fail is None else 1)
                factory.assert_called_once_with(socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC)
                connection.connect.assert_called_once_with(config.socket.path)
                if fail != "connect":
                    connection.send.assert_called_once_with(ingress.TRIGGER)
                else:
                    connection.send.assert_not_called()
                self.assertEqual(emit.call_count, int(fail is None))
                connection.__exit__.assert_called_once()


if __name__ == "__main__":
    unittest.main()
