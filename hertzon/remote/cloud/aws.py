"""Amazon Web Services (EC2): create, find and delete the servers Hertzon solves on (Query API with
Signature Version 4, standard library only).

The access key belongs to an IAM user with the policy in docs/REMOTE.md (EC2 instances, the security group
"hertzon" and, for the prices, pricing:GetProducts). The key stays on this computer: the server does not get
it, it deletes itself by shutting down (instances are started with "terminate on shutdown").
"""
from __future__ import annotations

import concurrent.futures
import datetime
import hashlib
import hmac
import json
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from ..client import RemoteError
from .hetzner import PlacementError, _label

NAME = "Amazon Web Services"
LABEL = "hertzon"                       # every instance Hertzon creates carries the tag hertzon=1
DEFAULT_TYPE = "m7a.2xlarge"            # 8 physical cores (AMD, no SMT), 32 GB
DEFAULT_REGION = "eu-central-1"
EC2_VERSION = "2016-11-15"
UBUNTU_OWNER = "099720109477"           # Canonical
UBUNTU_NAME = "ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"
DISK_GB = 30                            # the solver image needs about 4 GB, the analyses the rest
GROUP = "hertzon"                       # security group: SSH in, nothing else

# x86 types for solving, by family: c compute (2 GB per vCPU), m general (4 GB), r memory (8 GB).
# "a" (AMD) types have one core per vCPU; the Intel ("i") ones two vCPUs per core.
TYPES = [f"{fam}.{size}" for fam in ("c7a", "m7a", "r7a", "c7i", "m7i", "r7i")
         for size in ("2xlarge", "4xlarge", "8xlarge", "12xlarge", "16xlarge", "24xlarge")]

REGIONS = {"us-east-1": "N. Virginia", "us-east-2": "Ohio", "us-west-1": "N. California", "us-west-2": "Oregon",
           "ca-central-1": "Canada", "eu-central-1": "Frankfurt", "eu-central-2": "Zurich",
           "eu-west-1": "Ireland", "eu-west-2": "London", "eu-west-3": "Paris", "eu-north-1": "Stockholm",
           "eu-south-1": "Milan", "eu-south-2": "Spain", "ap-northeast-1": "Tokyo", "ap-northeast-2": "Seoul",
           "ap-southeast-1": "Singapore", "ap-southeast-2": "Sydney", "ap-south-1": "Mumbai",
           "sa-east-1": "Sao Paulo"}

class AccessDenied(RemoteError):
    """The key, or a service control policy of the account's organization, does not allow the call."""


CAPACITY = ("InsufficientInstanceCapacity", "InsufficientCapacity", "Unsupported")


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def sigv4_headers(method: str, url: str, body: bytes, headers: dict, key_id: str, secret: str, region: str,
                  service: str, now: datetime.datetime | None = None) -> dict:
    """HEADERS plus X-Amz-Date and the Authorization of AWS Signature Version 4."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    amz_date, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    u = urllib.parse.urlsplit(url)
    hdr = {k.lower(): " ".join(str(v).split()) for k, v in headers.items()}
    hdr["host"] = u.netloc
    hdr["x-amz-date"] = amz_date
    names = sorted(hdr)
    query = "&".join(sorted(f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}"
                            for k, v in urllib.parse.parse_qsl(u.query, keep_blank_values=True)))
    canonical = "\n".join([method, urllib.parse.quote(u.path or "/", safe="/-_.~"), query,
                           "".join(f"{n}:{hdr[n]}\n" for n in names), ";".join(names),
                           hashlib.sha256(body).hexdigest()])
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    k = _sign(_sign(_sign(_sign(("AWS4" + secret).encode("utf-8"), day), region), service), "aws4_request")
    sig = hmac.new(k, to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    out = {k: v for k, v in headers.items()}
    out["X-Amz-Date"] = amz_date
    out["Authorization"] = (f"AWS4-HMAC-SHA256 Credential={key_id}/{scope}, SignedHeaders={';'.join(names)}, "
                            f"Signature={sig}")
    return out


def _strip(el: ET.Element) -> ET.Element:
    """Remove the XML namespaces (EC2 answers in its own)."""
    for e in el.iter():
        if isinstance(e.tag, str) and "}" in e.tag:
            e.tag = e.tag.split("}", 1)[1]
    return el


def _items(el: ET.Element | None, path: str) -> list[ET.Element]:
    return [] if el is None else el.findall(path)


class AWS:
    name = NAME
    key = "aws"
    ssh_user = "ubuntu"
    install_docker = True               # the Ubuntu image has no Docker: cloud-init installs it
    self_delete = "shutdown"            # the watchdog shuts the instance down, which terminates it

    def __init__(self, key_id: str, secret: str, region: str = DEFAULT_REGION, endpoint: str = "",
                 pricing_endpoint: str = ""):
        self.key_id, self.secret = (key_id or "").strip(), (secret or "").strip()
        self.region = (region or DEFAULT_REGION).strip()
        self.endpoint = (endpoint or f"https://ec2.{self.region}.amazonaws.com").rstrip("/") + "/"
        self.pricing_endpoint = (pricing_endpoint or "https://api.pricing.us-east-1.amazonaws.com").rstrip("/") + "/"
        self.token = ""                 # nothing for the server: it does not call the API

    # ---- requests -----------------------------------------------------------------------------------
    def _send(self, url: str, body: bytes, headers: dict, service: str, region: str) -> bytes:
        if not (self.key_id and self.secret):
            raise RemoteError("No AWS access key is set (Tools > Options > Cloud)")
        hdr = sigv4_headers("POST", url, body, headers, self.key_id, self.secret, region, service)
        req = urllib.request.Request(url, data=body, method="POST", headers=hdr)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as ex:
            raw = ex.read()
            code, msg = _error(raw)
            raise _translate(code, msg, ex.code) from None
        except (urllib.error.URLError, OSError) as ex:
            raise RemoteError(f"AWS not reachable ({getattr(ex, 'reason', ex)})") from None

    def _call(self, action: str, params: dict | None = None) -> ET.Element:
        form = {"Action": action, "Version": EC2_VERSION, **{k: str(v) for k, v in (params or {}).items()}}
        body = urllib.parse.urlencode(form).encode("utf-8")
        raw = self._send(self.endpoint, body, {"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
                         "ec2", self.region)
        return _strip(ET.fromstring(raw))

    # ---- catalogue ----------------------------------------------------------------------------------
    def locations(self) -> list[dict]:
        """The regions enabled for the account; the known ones when the account may not list them (a service
        control policy may deny DescribeRegions but allow the chosen region)."""
        try:
            names = [r.findtext("regionName", "") for r in _items(self._call("DescribeRegions"), "regionInfo/item")]
        except AccessDenied:
            names = list(REGIONS)
        return sorted(({"name": n, "city": REGIONS.get(n, ""), "country": ""} for n in names),
                      key=lambda x: x["name"])

    def server_types(self, location: str = "") -> list[dict]:
        """The instance types of TYPES offered in this region: cores, memory (GB), price per hour (USD, on
        demand, Linux) when the key may read prices."""
        if location and location != self.region:
            return AWS(self.key_id, self.secret, location).server_types()
        offered = {o.findtext("instanceType") for o in _items(self._call("DescribeInstanceTypeOfferings", {
            "LocationType": "region", "Filter.1.Name": "instance-type",
            **{f"Filter.1.Value.{i + 1}": t for i, t in enumerate(TYPES)}}), "instanceTypeOfferingSet/item")}
        names = [t for t in TYPES if t in offered]
        if not names:
            return []
        info = self._call("DescribeInstanceTypes", {f"InstanceType.{i + 1}": t for i, t in enumerate(names)})
        prices = self.prices(names)
        out = []
        for it in _items(info, "instanceTypeSet/item"):
            t = it.findtext("instanceType", "")
            vcpu = int(it.findtext("vCpuInfo/defaultVCpus", "0") or 0)
            cores = int(it.findtext("vCpuInfo/defaultCores", "0") or 0) or vcpu
            mem = int(it.findtext("memoryInfo/sizeInMiB", "0") or 0) / 1024
            price = prices.get(t)
            out.append({"name": t, "cores": vcpu, "physical": cores, "memory": round(mem, 1), "dedicated": True,
                        "price": price, "prices": {self.region: price}, "locations": [self.region],
                        "currency": "USD"})
        return sorted(out, key=lambda t: (t["name"][0], t["name"].split(".")[0][-1], t["cores"]))

    def prices(self, types: list[str]) -> dict[str, float]:
        """On-demand Linux prices per hour (USD) of TYPES in this region; {} without pricing:GetProducts."""
        def one(t):
            body = json.dumps({"ServiceCode": "AmazonEC2", "MaxResults": 10, "FormatVersion": "aws_v1", "Filters": [
                {"Type": "TERM_MATCH", "Field": f, "Value": v} for f, v in (
                    ("instanceType", t), ("regionCode", self.region), ("operatingSystem", "Linux"),
                    ("tenancy", "Shared"), ("preInstalledSw", "NA"), ("capacitystatus", "Used"),
                    ("licenseModel", "No License required"))]}).encode()
            raw = self._send(self.pricing_endpoint, body, {
                "Content-Type": "application/x-amz-json-1.1",
                "X-Amz-Target": "AWSPriceListService.GetProducts"}, "pricing", "us-east-1")
            for item in json.loads(raw).get("PriceList", []):
                p = json.loads(item) if isinstance(item, str) else item
                for term in (p.get("terms", {}).get("OnDemand") or {}).values():
                    for dim in term.get("priceDimensions", {}).values():
                        usd = float(dim.get("pricePerUnit", {}).get("USD", 0) or 0)
                        if usd > 0:
                            return t, usd
            return t, None
        try:
            with concurrent.futures.ThreadPoolExecutor(8) as ex:
                return {t: p for t, p in ex.map(one, types) if p is not None}
        except RemoteError:
            return {}                   # no permission for prices: types without prices

    def locations_for(self, server_type: str) -> list[str]:
        return [self.region]            # one region per key setting; other zones are tried in create()

    # ---- servers ------------------------------------------------------------------------------------
    def ssh_key(self, public: str, client: str) -> str:
        return ""                       # the key comes with the first-boot configuration

    def _image(self) -> str:
        imgs = _items(self._call("DescribeImages", {"Owner.1": UBUNTU_OWNER, "Filter.1.Name": "name",
                                                    "Filter.1.Value.1": UBUNTU_NAME, "Filter.2.Name": "state",
                                                    "Filter.2.Value.1": "available"}), "imagesSet/item")
        if not imgs:
            raise RemoteError(f"AWS: no Ubuntu 24.04 image found in {self.region}")
        return max(imgs, key=lambda i: i.findtext("creationDate", "")).findtext("imageId", "")

    def _vpc(self) -> str:
        for v in _items(self._call("DescribeVpcs", {"Filter.1.Name": "is-default", "Filter.1.Value.1": "true"}),
                        "vpcSet/item"):
            return v.findtext("vpcId", "")
        raise RemoteError(f"AWS: the region {self.region} has no default VPC; create one in the VPC console "
                          "(Your VPCs > Actions > Create default VPC)")

    def _group(self) -> str:
        """The security group "hertzon" of the default VPC (SSH in), created once."""
        vpc = self._vpc()
        found = _items(self._call("DescribeSecurityGroups", {"Filter.1.Name": "group-name", "Filter.1.Value.1": GROUP,
                                                             "Filter.2.Name": "vpc-id", "Filter.2.Value.1": vpc}),
                       "securityGroupInfo/item")
        for g in found:
            return g.findtext("groupId", "")
        r = self._call("CreateSecurityGroup", {"GroupName": GROUP, "VpcId": vpc,
                                               "GroupDescription": "Hertzon: SSH to the solver",
                                               "TagSpecification.1.ResourceType": "security-group",
                                               "TagSpecification.1.Tag.1.Key": LABEL,
                                               "TagSpecification.1.Tag.1.Value": "1"})
        gid = r.findtext("groupId", "")
        self._call("AuthorizeSecurityGroupIngress", {
            "GroupId": gid, "IpPermissions.1.IpProtocol": "tcp", "IpPermissions.1.FromPort": 22,
            "IpPermissions.1.ToPort": 22, "IpPermissions.1.IpRanges.1.CidrIp": "0.0.0.0/0",
            "IpPermissions.1.Ipv6Ranges.1.CidrIpv6": "::/0"})
        return gid

    def _subnets(self) -> list[str]:
        """The default subnets (one per zone) of the default VPC."""
        return [s.findtext("subnetId", "") for s in _items(self._call("DescribeSubnets", {
            "Filter.1.Name": "default-for-az", "Filter.1.Value.1": "true"}), "subnetSet/item")]

    def create(self, name: str, server_type: str, location: str, user_data: str, ssh_key, client: str) -> dict:
        import base64
        if location and location != self.region:
            raise RemoteError(f"AWS: the session is for {self.region}, not {location}")
        params = {"ImageId": self._image(), "InstanceType": server_type, "MinCount": 1, "MaxCount": 1,
                  "UserData": base64.b64encode(user_data.encode("utf-8")).decode("ascii"),
                  "InstanceInitiatedShutdownBehavior": "terminate", "SecurityGroupId.1": self._group(),
                  "BlockDeviceMapping.1.DeviceName": "/dev/sda1", "BlockDeviceMapping.1.Ebs.VolumeSize": DISK_GB,
                  "BlockDeviceMapping.1.Ebs.VolumeType": "gp3",
                  "BlockDeviceMapping.1.Ebs.DeleteOnTermination": "true",
                  "MetadataOptions.HttpTokens": "required",
                  "TagSpecification.1.ResourceType": "instance",
                  "TagSpecification.1.Tag.1.Key": "Name", "TagSpecification.1.Tag.1.Value": name,
                  "TagSpecification.1.Tag.2.Key": LABEL, "TagSpecification.1.Tag.2.Value": "1",
                  "TagSpecification.1.Tag.3.Key": "hertzon-client",
                  "TagSpecification.1.Tag.3.Value": _label(client)}
        try:
            r = self._call("RunInstances", params)
        except PlacementError:
            r = None
            for sn in self._subnets():          # the type is sold out in the zone AWS chose: other zones
                try:
                    r = self._call("RunInstances", {**params, "SubnetId": sn})
                    break
                except PlacementError:
                    continue
            if r is None:
                raise
        return self._server(_items(r, "instancesSet/item")[0])

    def get(self, sid) -> dict | None:
        try:
            r = self._call("DescribeInstances", {"InstanceId.1": sid})
        except LookupError:
            return None
        for inst in _items(r, "reservationSet/item/instancesSet/item"):
            s = self._server(inst)
            return None if s["state"] == "terminated" else s
        return None

    def delete(self, sid) -> None:
        try:
            self._call("TerminateInstances", {"InstanceId.1": sid})
        except LookupError:
            pass                                # already gone

    def servers(self) -> list[dict]:
        r = self._call("DescribeInstances", {"Filter.1.Name": f"tag:{LABEL}", "Filter.1.Value.1": "1",
                                             "Filter.2.Name": "instance-state-name",
                                             **{f"Filter.2.Value.{i + 1}": v for i, v in enumerate(
                                                 ("pending", "running", "stopping", "stopped"))}})
        return [self._server(i) for i in _items(r, "reservationSet/item/instancesSet/item")]

    @staticmethod
    def _server(i: ET.Element) -> dict:
        tags = {t.findtext("key", ""): t.findtext("value", "") for t in _items(i, "tagSet/item")}
        state = i.findtext("instanceState/name", "")
        status = {"pending": "initializing", "running": "running"}.get(state, "off")
        return {"id": i.findtext("instanceId", ""), "name": tags.get("Name", ""), "status": status, "state": state,
                "ip": i.findtext("ipAddress", "") or "", "type": i.findtext("instanceType", ""),
                "created": i.findtext("launchTime", ""), "client": tags.get("hertzon-client", "")}


def _error(raw: bytes) -> tuple[str, str]:
    """(code, message) of an EC2 (XML) or pricing (JSON) error answer."""
    try:
        el = _strip(ET.fromstring(raw))
        e = el.find(".//Error")
        if e is not None:
            return e.findtext("Code", ""), e.findtext("Message", "")
    except ET.ParseError:
        pass
    try:
        d = json.loads(raw)
        return str(d.get("__type", "")).split("#")[-1], d.get("message") or d.get("Message") or ""
    except ValueError:
        return "", raw.decode("utf-8", "replace")[:200]


def _translate(code: str, msg: str, http: int) -> Exception:
    if code in ("AuthFailure", "InvalidClientTokenId", "SignatureDoesNotMatch", "UnrecognizedClientException",
                "IncompleteSignature"):
        return RemoteError("AWS: the access key is wrong or was removed")
    if code in ("UnauthorizedOperation", "AccessDeniedException", "AccessDenied"):
        what = msg.split("Encoded")[0].strip()
        if "service control policy" in what:
            return AccessDenied(f"AWS: the organization the account belongs to forbids this ({what}). Its "
                                "administrator can allow it (AWS console > Organizations > Policies); IAM "
                                "permissions of the user cannot")
        return AccessDenied(f"AWS: the access key may not do this ({what}); see the IAM policy in the Hertzon "
                            "documentation (docs/REMOTE.md)")
    if code.startswith("InvalidInstanceID"):
        return LookupError(msg)
    if code in ("VcpuLimitExceeded", "InstanceLimitExceeded"):
        return RemoteError("AWS: the vCPU limit of the account is reached (new accounts may start few "
                           "vCPUs). Request more in the AWS console: Service Quotas > Amazon EC2 > "
                           "Running On-Demand Standard instances")
    if code in ("VPCIdNotSpecified", "MissingInput") or "default VPC" in msg:
        return RemoteError("AWS: the region has no default VPC; create one in the VPC console "
                           "(Actions > Create default VPC)")
    if code in CAPACITY:
        return PlacementError(f"AWS: {msg or code}")
    if code == "OptInRequired":
        return RemoteError(f"AWS: {msg or 'the region or service is not enabled for the account'}")
    return RemoteError(f"AWS: {msg or code or 'error'} (HTTP {http})")
