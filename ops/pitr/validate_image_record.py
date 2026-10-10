"""Offline structure/binding checks only; does NOT authenticate provenance.

Usage: python validate_image_record.py FILE --source-repository OWNER/REPO
  --source-commit SHA --image-repository ghcr.io/OWNER/IMAGE --platform linux/amd64
Expectations must come from separately reviewed policy, not the input record.
"""

import argparse
import json
import re
import sys

LIMIT = 64 * 1024
SUCCESS = "STRUCTURE_AND_BINDING_VALID"
TOP = {"schema_version", "source_repository", "source_commit", "image_repository",
       "image_digest", "platform", "evidence"}
EVIDENCE = {"source_commit", "image_digest", "phase", "run_id", "run_attempt", "conclusion"}


class InvalidRecord(ValueError):
    """Safe category only; never contains supplied values."""


def require(condition):
    if not condition:
        raise InvalidRecord("INVALID_RECORD")


def matches(value, pattern):
    return type(value) is str and re.fullmatch(pattern, value) is not None


def object_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def reject_constant(_value):
    raise InvalidRecord("INVALID_RECORD")


def keys(value, expected):
    require(type(value) is dict and set(value) == expected)


def validate(raw, *, source_repository, source_commit, image_repository, platform):
    """Pure function. A consistent forged record CAN pass; trust is a separate gate."""
    require(type(raw) is bytes and len(raw) <= LIMIT)
    require(matches(source_repository, r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"))
    require(matches(source_commit, r"[0-9a-f]{40}"))
    require(matches(image_repository, r"ghcr\.io/[a-z0-9]+(?:[._-][a-z0-9]+)*/[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*"))
    require(platform == "linux/amd64")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=object_pairs,
                          parse_constant=reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise InvalidRecord("INVALID_RECORD") from None
    keys(data, TOP)
    require(type(data["schema_version"]) is int and data["schema_version"] == 1)
    for field, expected in (("source_repository", source_repository),
                            ("source_commit", source_commit),
                            ("image_repository", image_repository), ("platform", platform)):
        require(type(data[field]) is str and data[field] == expected)
    digest = data["image_digest"]
    require(matches(digest, r"sha256:[0-9a-f]{64}") and digest != "sha256:" + "0" * 64)
    keys(data["evidence"], {"synthetic", "extended"})
    for item in data["evidence"].values():
        keys(item, EVIDENCE)
        require(item["source_commit"] == source_commit and item["image_digest"] == digest)
        require(item["phase"] == "post_publication" and item["conclusion"] == "success")
        for field in ("run_id", "run_attempt"):
            require(type(item[field]) is int and item[field] > 0)
    return SUCCESS


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise InvalidRecord("INVALID_ARGUMENTS")


def main(argv=None):
    parser = SafeParser(description=__doc__)
    parser.add_argument("file")
    for name in ("source-repository", "source-commit", "image-repository", "platform"):
        parser.add_argument("--" + name, required=True)
    try:
        args = vars(parser.parse_args(argv))
        path = args.pop("file")
        with open(path, "rb") as stream:
            raw = stream.read(LIMIT + 1)
        print(validate(raw, **args))
        return 0
    except (OSError, ValueError, RecursionError):
        print("INVALID_INPUT", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
