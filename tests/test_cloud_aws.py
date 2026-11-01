"""Cloud computers on AWS: Signature Version 4, the EC2 client against a stand-in of the Query API, the
first-boot configuration of an AWS server and the provider settings."""
from __future__ import annotations

import base64
import datetime
import json
import shutil
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from hertzon.remote.client import RemoteError
from hertzon.remote.cloud import providers, watchdog
from hertzon.remote.cloud.aws import AWS, sigv4_headers

NS = "http://ec2.amazonaws.com/doc/2016-11-15/"


def test_sigv4_matches_the_aws_test_suite():
    """get-vanilla of the AWS Signature Version 4 test suite."""
    h = sigv4_headers("GET", "https://example.amazonaws.com/", b"", {}, "AKIDEXAMPLE",
                      "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "us-east-1", "service",
                      datetime.datetime(2015, 8, 30, 12, 36, tzinfo=datetime.timezone.utc))
    assert h["X-Amz-Date"] == "20150830T123600Z"
    assert h["Authorization"] == ("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request, "
                                  "SignedHeaders=host;x-amz-date, "
                                  "Signature=5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31")


def _x(tag, *children, text=None):
    return f"<{tag}>{text if text is not None else ''.join(children)}</{tag}>"


class FakeEC2(BaseHTTPRequestHandler):
    """The EC2 Query API actions and the price list call Hertzon uses."""
    instances: ClassVar[dict] = {}
    groups: ClassVar[list] = []
    runs: ClassVar[list] = []
    full_zones: ClassVar[set] = set()
    vcpu_limit: ClassVar[bool] = False
    scp_deny: ClassVar[bool] = False

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/xml"):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _ok(self, action, *inner):
        self._send(200, f'<{action}Response xmlns="{NS}">{"".join(inner)}</{action}Response>')

    def _err(self, code, msg, http=400):
        self._send(http, _x("Response", _x("Errors", _x("Error", _x("Code", text=code), _x("Message", text=msg)))))

    def _inst(self, i):
        tags = "".join(_x("item", _x("key", text=k), _x("value", text=v)) for k, v in i["tags"].items())
        return _x("item", _x("instanceId", text=i["id"]), _x("instanceType", text=i["type"]),
                  _x("instanceState", _x("name", text=i["state"])), _x("ipAddress", text=i["ip"]),
                  _x("launchTime", text="2026-11-01T12:00:00.000Z"), _x("tagSet", tags))

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        if self.scp_deny and b"Action=DescribeRegions" in raw:
            return self._err("UnauthorizedOperation", "You are not authorized to perform this operation. User: x "
                             "is not authorized to perform: ec2:DescribeRegions with an explicit deny in a "
                             "service control policy: arn:aws:organizations::1:policy/p")
        auth = self.headers.get("Authorization", "")
        if "Credential=AKIAGOOD/" not in auth:
            return self._err("AuthFailure", "AWS was not able to validate the provided access credentials", 401)
        if self.headers.get("X-Amz-Target") == "AWSPriceListService.GetProducts":
            q = json.loads(raw)
            t = next(f["Value"] for f in q["Filters"] if f["Field"] == "instanceType")
            usd = {"m7a.2xlarge": "0.4637000000", "c7a.2xlarge": "0.4100000000"}.get(t)
            items = [] if usd is None else [json.dumps({"terms": {"OnDemand": {"x": {"priceDimensions": {
                "y": {"pricePerUnit": {"USD": usd}}}}}}})]
            return self._send(200, json.dumps({"PriceList": items}), "application/x-amz-json-1.1")
        p = dict(urllib.parse.parse_qsl(raw.decode()))
        a = p["Action"]
        if a == "DescribeRegions":
            return self._ok(a, _x("regionInfo", *(_x("item", _x("regionName", text=r))
                                                   for r in ("eu-central-1", "us-east-1"))))
        if a == "DescribeInstanceTypeOfferings":
            asked = [v for k, v in p.items() if k.startswith("Filter.1.Value.")]
            have = [t for t in asked if t in ("m7a.2xlarge", "c7a.2xlarge", "m7i.2xlarge")]
            return self._ok(a, _x("instanceTypeOfferingSet", *(_x("item", _x("instanceType", text=t)) for t in have)))
        if a == "DescribeInstanceTypes":
            spec = {"m7a.2xlarge": (8, 8, 32768), "c7a.2xlarge": (8, 8, 16384), "m7i.2xlarge": (8, 4, 32768)}
            names = [v for k, v in p.items() if k.startswith("InstanceType.")]
            return self._ok(a, _x("instanceTypeSet", *(_x("item", _x("instanceType", text=n), _x(
                "vCpuInfo", _x("defaultVCpus", text=spec[n][0]), _x("defaultCores", text=spec[n][1])),
                _x("memoryInfo", _x("sizeInMiB", text=spec[n][2]))) for n in names)))
        if a == "DescribeImages":
            assert p["Owner.1"] == "099720109477"
            return self._ok(a, _x("imagesSet", *(_x("item", _x("imageId", text=i), _x("creationDate", text=d))
                                                 for i, d in (("ami-old", "2025-01-01"), ("ami-new", "2026-10-01")))))
        if a == "DescribeVpcs":
            return self._ok(a, _x("vpcSet", _x("item", _x("vpcId", text="vpc-1"))))
        if a == "DescribeSecurityGroups":
            return self._ok(a, _x("securityGroupInfo", *(_x("item", _x("groupId", text=g)) for g in self.groups)))
        if a == "CreateSecurityGroup":
            assert p["VpcId"] == "vpc-1" and p["GroupName"] == "hertzon"
            self.groups.append("sg-1")
            return self._ok(a, _x("groupId", text="sg-1"))
        if a == "AuthorizeSecurityGroupIngress":
            assert p["IpPermissions.1.FromPort"] == "22"
            return self._ok(a, _x("return", text="true"))
        if a == "DescribeSubnets":
            return self._ok(a, _x("subnetSet", *(_x("item", _x("subnetId", text=s)) for s in ("subnet-a", "subnet-b"))))
        if a == "RunInstances":
            self.runs.append(p)
            if self.vcpu_limit:
                return self._err("VcpuLimitExceeded", "You have requested more vCPU capacity than your limit")
            if p.get("SubnetId", "auto") in self.full_zones:
                return self._err("InsufficientInstanceCapacity", "We currently do not have sufficient capacity")
            iid = f"i-{len(self.instances):04d}"
            tags = {p[k]: p[k.replace(".Key", ".Value")] for k in p if k.startswith("TagSpecification.1.Tag.")
                    and k.endswith(".Key")}
            self.instances[iid] = {"id": iid, "type": p["InstanceType"], "state": "pending", "ip": "", "tags": tags}
            return self._ok(a, _x("instancesSet", self._inst(self.instances[iid])))
        if a == "DescribeInstances":
            if "InstanceId.1" in p:
                i = self.instances.get(p["InstanceId.1"])
                if i is None:
                    return self._err("InvalidInstanceID.NotFound", "The instance ID does not exist")
                found = [i]
            else:
                states = [v for k, v in p.items() if k.startswith("Filter.2.Value.")]
                found = [i for i in self.instances.values() if i["state"] in states and i["tags"].get("hertzon") == "1"]
            return self._ok(a, _x("reservationSet", _x("item", _x("instancesSet", *(self._inst(i) for i in found)))))
        if a == "TerminateInstances":
            i = self.instances.get(p["InstanceId.1"])
            if i is None:
                return self._err("InvalidInstanceID.NotFound", "The instance ID does not exist")
            i["state"] = "terminated"
            return self._ok(a, _x("instancesSet", ""))
        self._err("InvalidAction", a)


@pytest.fixture
def ec2():
    FakeEC2.instances, FakeEC2.groups, FakeEC2.runs, FakeEC2.full_zones, FakeEC2.vcpu_limit = {}, [], [], set(), False
    FakeEC2.scp_deny = False
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeEC2)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    yield lambda key="AKIAGOOD": AWS(key, "secret", "eu-central-1", endpoint=url, pricing_endpoint=url)
    srv.shutdown()
    srv.server_close()


def test_aws_catalogue(ec2):
    with pytest.raises(RemoteError, match="access key is wrong"):
        ec2("AKIABAD").locations()
    a = ec2()
    assert [r["name"] for r in a.locations()] == ["eu-central-1", "us-east-1"]
    assert a.locations()[0]["city"] == "Frankfurt"
    types = {t["name"]: t for t in a.server_types()}
    assert set(types) == {"m7a.2xlarge", "c7a.2xlarge", "m7i.2xlarge"}
    assert types["m7a.2xlarge"]["price"] == pytest.approx(0.4637) and types["m7a.2xlarge"]["memory"] == 32
    assert types["m7i.2xlarge"]["physical"] == 4 and types["m7i.2xlarge"]["price"] is None   # no price: shown so
    assert list(types["c7a.2xlarge"]["prices"]) == ["eu-central-1"]
    FakeEC2.scp_deny = True                     # an organization denies listing the regions: the known ones
    regions = [r["name"] for r in a.locations()]
    assert "eu-central-1" in regions and "us-east-1" in regions and len(regions) > 10


def test_aws_servers(ec2):
    a = ec2()
    s = a.create("hertzon-abc", "m7a.2xlarge", "eu-central-1", "#cloud-config\n{}", "", "LAPTOP 1")
    run = FakeEC2.runs[0]
    assert run["ImageId"] == "ami-new" and run["InstanceInitiatedShutdownBehavior"] == "terminate"
    assert run["SecurityGroupId.1"] == "sg-1" and base64.b64decode(run["UserData"]) == b"#cloud-config\n{}"
    assert run["BlockDeviceMapping.1.Ebs.DeleteOnTermination"] == "true"
    assert s["status"] == "initializing" and s["name"] == "hertzon-abc" and s["client"] == "LAPTOP-1"
    a.create("hertzon-def", "m7a.2xlarge", "eu-central-1", "#cloud-config\n{}", "", "x")
    assert FakeEC2.groups == ["sg-1"]                                    # the group is created once
    FakeEC2.instances[s["id"]].update(state="running", ip="198.51.100.7")
    assert a.get(s["id"])["status"] == "running" and a.get(s["id"])["ip"] == "198.51.100.7"
    assert sorted(x["name"] for x in a.servers()) == ["hertzon-abc", "hertzon-def"]
    a.delete(s["id"])
    a.delete("i-gone")                                                   # already gone: no error
    assert a.get(s["id"]) is None and [x["name"] for x in a.servers()] == ["hertzon-def"]


def test_aws_full_zone_and_limits(ec2):
    a = ec2()
    FakeEC2.full_zones = {"auto", "subnet-a"}
    a.create("hertzon-abc", "m7a.2xlarge", "eu-central-1", "#cloud-config\n{}", "", "x")
    assert [r.get("SubnetId") for r in FakeEC2.runs] == [None, "subnet-a", "subnet-b"]
    FakeEC2.full_zones = {"auto", "subnet-a", "subnet-b"}
    from hertzon.remote.cloud.hetzner import PlacementError
    with pytest.raises(PlacementError):
        a.create("hertzon-x", "m7a.2xlarge", "eu-central-1", "#cloud-config\n{}", "", "x")
    FakeEC2.vcpu_limit = True
    with pytest.raises(RemoteError, match="Service Quotas"):
        a.create("hertzon-y", "m7a.2xlarge", "eu-central-1", "#cloud-config\n{}", "", "x")


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="needs OpenSSH's ssh-keygen")
def test_aws_session_keeps_the_key_at_home(ec2, tmp_path):
    from hertzon.remote.cloud import CloudSession
    s = CloudSession(ec2(), "m7a.2xlarge", "eu-central-1", workdir=tmp_path, version="9.9.9")
    st = s._create(lambda text: None)
    assert s.tunnel.user == "ubuntu" and (tmp_path / "cloud" / "aws.json").is_file()
    ud = base64.b64decode(FakeEC2.runs[0]["UserData"]).decode()
    assert len(ud.encode()) < 16 * 1024                                  # EC2's limit
    cfg = json.loads(ud.split("\n", 1)[1])
    assert cfg["packages"] == ["docker.io"] and cfg["ssh_keys"]["ed25519_public"] == st["host_key"]
    files = {f["path"]: f["content"] for f in cfg["write_files"]}
    wcfg = json.loads(files["/etc/hertzon/watchdog.json"])
    assert wcfg["provider"] == "aws" and wcfg["api_token"] == "" and "secret" not in ud
    assert "aws" in watchdog.DELETE
    assert s.delete() and FakeEC2.instances[st["id"]]["state"] == "terminated"


def test_provider_settings():
    vals = {"cloud/provider": "aws", "cloud/aws_key_id": "AKIA1", "cloud/aws_secret": "",
            "cloud/aws_region": "us-east-1", "cloud/hetzner_token": "tok"}
    get = lambda k, d=None: vals.get(k, d)
    assert not providers.configured(get)                                 # the secret is missing
    vals["cloud/aws_secret"] = "s"
    prov, stype, loc = providers.from_settings(get)
    assert providers.configured(get) and prov.key == "aws" and prov.region == "us-east-1" == loc
    assert stype == "m7a.2xlarge"
    vals["cloud/provider"] = "hetzner"
    prov, stype, loc = providers.from_settings(get)
    assert prov.key == "hetzner" and (stype, loc) == ("ccx33", "fsn1") and providers.configured(get)
