"""Garage native bootstrap, bucket permissions and metadata/block cold recovery."""

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import urllib.error
import urllib.request
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
import pytest

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions/library/services/garage"
REQUIRED = ("GARAGE_RPC_SECRET", "GARAGE_ADMIN_TOKEN", "GARAGE_ACCESS_KEY", "GARAGE_SECRET_KEY")


def render(tmp_path, values, bind=None, port="3900", check=True):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GARAGE_") and key != "BIND_ADDRESS"}
    env.update(values, GARAGE_S3_PORT=port)
    if bind is not None:
        env["BIND_ADDRESS"] = bind
    empty = tmp_path / "empty.env"
    empty.write_text("")
    return subprocess.run(["docker", "compose", "--env-file", str(empty), "--project-directory", str(tmp_path),
                           "-f", str(EXTENSION / "compose.yaml"), "config", "--format", "json"],
                          env=env, check=check, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("bind", [None, "127.0.0.1", "0.0.0.0", "192.0.2.42"])
@pytest.mark.parametrize("port", ["3900", "13900"])
def test_plan_publishes_only_s3_and_retains_native_identity(tmp_path, bind, port):
    values = {key: secrets.token_hex(32) for key in REQUIRED}
    service = json.loads(render(tmp_path, values, bind, port).stdout)["services"]["garage"]
    assert [(item["target"], item["host_ip"], item["published"]) for item in service["ports"]] == [(3900, bind or "127.0.0.1", port)]
    assert service["user"] == "1000:1000" and service["read_only"] is True
    assert service["volumes"][0]["target"] == "/var/lib/garage"
    assert service["command"] == ["/garage", "server", "--single-node", "--default-bucket"]
    catalog = json.loads((ROOT / "config/extensions-catalog.json").read_text())
    entry = next(item for item in catalog["extensions"] if item["id"] == "garage")
    assert {item["key"] for item in entry["env_vars"] if item.get("required")} == set(REQUIRED)


@pytest.mark.parametrize("missing", REQUIRED)
@pytest.mark.parametrize("empty", [False, True])
def test_native_bootstrap_secrets_are_required(tmp_path, missing, empty):
    values = {key: secrets.token_hex(32) for key in REQUIRED if key != missing}
    if empty:
        values[missing] = ""
    result = render(tmp_path, values, check=False)
    assert result.returncode != 0 and missing in result.stderr


@pytest.mark.skipif(os.getenv("ODS_TEST_GARAGE") != "1", reason="Opt-in real Garage lifecycle")
def test_native_s3_permissions_multipart_rotation_and_cold_restore(tmp_path):
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("Qualification prepares isolated UID 1000 storage")
    values = {key: secrets.token_hex(32) for key in REQUIRED}
    values["GARAGE_ACCESS_KEY"] = "GK" + secrets.token_hex(16)
    project = "ods-q20-garage-" + uuid.uuid4().hex[:12]
    data = tmp_path / "data/garage"
    data.mkdir(parents=True)
    os.chown(data, 1000, 1000)
    plan = json.loads(render(tmp_path, values).stdout)
    service = plan["services"]["garage"]
    service.update(container_name=project, restart="no", labels={"io.ods.quality20.validation": "true"})
    service["ports"][0]["published"] = "0"
    # The production recipe does not publish administration. Only this isolated
    # qualification adds an ephemeral loopback port for native admin requests.
    service["ports"].append({"target": 3903, "published": "0", "host_ip": "127.0.0.1", "protocol": "tcp"})
    service["volumes"][1]["source"] = str(EXTENSION / "garage.toml")
    plan["networks"] = {"ods-network": {"name": project + "-network"}}
    config = tmp_path / "isolated.json"
    config.write_text(json.dumps(plan))
    config.chmod(0o600)
    command = ["docker", "compose", "-p", project, "-f", str(config)]

    def run(*args, check=True):
        result = subprocess.run(args, capture_output=True, text=True, timeout=180)
        if check:
            assert result.returncode == 0, result.stdout + result.stderr
        return result

    def endpoint(port):
        binding = json.loads(run("docker", "inspect", project).stdout)[0]["NetworkSettings"]["Ports"][str(port) + "/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        return "http://127.0.0.1:" + binding["HostPort"]

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def http(path, token=None, method="GET", payload=None, expected=200):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(endpoint(3903) + path, method=method,
                                         data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        try:
            response = opener.open(request, timeout=30)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            assert response.status == expected, f"{method} {path}: HTTP {response.status}: {body[:300]!r}"
            if not body:
                return None
            return json.loads(body) if response.headers.get_content_type() == "application/json" else body.decode()

    def client(key, secret):
        return boto3.client("s3", endpoint_url=endpoint(3900), region_name="garage", aws_access_key_id=key, aws_secret_access_key=secret,
                            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}, retries={"total_max_attempts": 1},
                                          connect_timeout=5, read_timeout=30, proxies={}, request_checksum_calculation="when_required",
                                          response_checksum_validation="when_required"))

    def denied(operation, **kwargs):
        with pytest.raises(ClientError) as error:
            operation(**kwargs)
        assert error.value.response["ResponseMetadata"]["HTTPStatusCode"] == 403

    bucket = "ods-artifacts"
    body = "Xin chào: local artifact".encode()
    large = bytes(range(256)) * (24 * 1024)
    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "120")
        http("/health")
        http("/v2/GetClusterHealth", expected=403)
        http("/v2/GetClusterHealth", "incorrect", expected=403)
        admin = values["GARAGE_ADMIN_TOKEN"]
        layout = http("/v2/GetClusterLayout", admin)
        assert layout["version"] == 1
        bootstrap = client(values["GARAGE_ACCESS_KEY"], values["GARAGE_SECRET_KEY"])
        assert bucket in [item["Name"] for item in bootstrap.list_buckets()["Buckets"]]
        denied(client(values["GARAGE_ACCESS_KEY"], "incorrect").list_buckets)
        bootstrap.put_object(Bucket=bucket, Key="nested/hello.txt", Body=body, ContentType="text/plain; charset=utf-8", Metadata={"origin": "ods"})
        assert bootstrap.get_object(Bucket=bucket, Key="nested/hello.txt", Range="bytes=0-2")["Body"].read() == body[:3]
        upload = bootstrap.create_multipart_upload(Bucket=bucket, Key="large.bin")
        parts = []
        for number, segment in enumerate((large[:5 * 1024 * 1024], large[5 * 1024 * 1024:]), 1):
            result = bootstrap.upload_part(Bucket=bucket, Key="large.bin", UploadId=upload["UploadId"], PartNumber=number, Body=segment)
            parts.append({"ETag": result["ETag"], "PartNumber": number})
        bootstrap.complete_multipart_upload(Bucket=bucket, Key="large.bin", UploadId=upload["UploadId"], MultipartUpload={"Parts": parts})
        bucket_id = http("/v2/GetBucketInfo?globalAlias=" + bucket, admin)["id"]
        reader = http("/v2/CreateKey", admin, "POST", {"name": "ODS reader"})
        http("/v2/AllowBucketKey", admin, "POST", {"bucketId": bucket_id, "accessKeyId": reader["accessKeyId"], "permissions": {"read": True}})

        def verify_objects():
            current = client(reader["accessKeyId"], reader["secretAccessKey"])
            obj = current.get_object(Bucket=bucket, Key="nested/hello.txt")
            assert obj["Body"].read() == body and obj["Metadata"]["origin"] == "ods"
            assert hashlib.sha256(current.get_object(Bucket=bucket, Key="large.bin")["Body"].read()).digest() == hashlib.sha256(large).digest()
            denied(current.put_object, Bucket=bucket, Key="denied", Body=b"x")
            denied(current.delete_object, Bucket=bucket, Key="nested/hello.txt")

        verify_objects()
        signed = client(reader["accessKeyId"], reader["secretAccessKey"]).generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": "nested/hello.txt"}, ExpiresIn=60)
        with opener.open(signed, timeout=30) as response:
            assert response.read() == body
        print("Native layout bootstrap, S3 multipart/range/presigned reads and read-only bucket grants verified", flush=True)
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
        verify_objects()
        service["environment"]["GARAGE_DEFAULT_SECRET_KEY"] = secrets.token_hex(32)
        config.write_text(json.dumps(plan))
        mismatch = run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "60", check=False)
        assert mismatch.returncode != 0
        assert "associated with a secret key different" in run(*command, "logs", "--tail", "100").stdout
        service["environment"]["GARAGE_DEFAULT_SECRET_KEY"] = values["GARAGE_SECRET_KEY"]
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
        verify_objects()
        rotated_admin, rotated_id, rotated_secret = secrets.token_hex(32), "GK" + secrets.token_hex(16), secrets.token_hex(32)
        service["environment"].update(GARAGE_ADMIN_TOKEN=rotated_admin, GARAGE_DEFAULT_ACCESS_KEY=rotated_id, GARAGE_DEFAULT_SECRET_KEY=rotated_secret)
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
        http("/v2/GetClusterHealth", admin, expected=403)
        admin = rotated_admin
        http("/v2/DeleteKey?id=" + values["GARAGE_ACCESS_KEY"], admin, "POST")
        denied(client(values["GARAGE_ACCESS_KEY"], values["GARAGE_SECRET_KEY"]).list_buckets)
        assert client(rotated_id, rotated_secret).get_object(Bucket=bucket, Key="nested/hello.txt")["Body"].read() == body
        print("Recreation, rejected bootstrap mismatch, recovery and explicit key/token rotation verified", flush=True)
        run(*command, "stop", "--timeout", "45")
        restored = tmp_path / "restored"
        shutil.copytree(data, restored)
        for path in [restored, *restored.rglob("*")]:
            os.chown(path, 1000, 1000)
        service["volumes"][0]["source"] = str(restored)
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
        verify_objects()
        assert http("/v2/GetClusterLayout", admin)["version"] == 1
        http("/v2/DeleteKey?id=" + reader["accessKeyId"], admin, "POST")
        denied(client(reader["accessKeyId"], reader["secretAccessKey"]).get_object, Bucket=bucket, Key="nested/hello.txt")
        current = client(rotated_id, rotated_secret)
        current.delete_object(Bucket=bucket, Key="nested/hello.txt")
        current.delete_object(Bucket=bucket, Key="large.bin")
        assert not current.list_objects_v2(Bucket=bucket).get("Contents")
        print("Cold metadata/block restore preserved objects and permissions; revocation and deletion verified", flush=True)
    finally:
        print(run(*command, "logs", "--tail", "15").stdout)
        run(*command, "down", "--volumes")
