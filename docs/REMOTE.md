# Solving on another computer

> **Experimental.** Remote solving currently works only with another computer in the local network that
> runs Hertzon. A full solution, also for servers and cloud machines, is in progress; options, behaviour
> and the connection between GUI and service may still change.

Build and post-process the model in the Hertzon GUI on your laptop and let a stronger computer in your
network do the solving. The other computer runs a normal Hertzon installation plus the small remote service;
the GUI sends each analysis there and gets the results back into the project's results folder, so reports,
field plots and exports work exactly as after a local solve.

## On the computer that solves

1. Install Hertzon as usual (same version as on your laptop).
2. Tools > Options > Remote > *Share this computer*: press **Start Service**. Hertzon shows the address
   other computers enter (`simstation02:47820`) and the token, with a Copy button. The service runs in the
   background and keeps running when Hertzon is closed; **Stop Service** ends it. Tick *Start the service at
   login* to have it running after every login, also without opening Hertzon.

Without the GUI (for example on a Linux server): run `hertzon-serve` (or `python -m hertzon.remote serve`)
in Hertzon's Python environment; it prints address and token. Options: `--cores N`, `--sweep-workers N`
(default: Tools > Options > HPC of that computer, else all cores and 2), `--port` (default 47820), `--workdir`. `hertzon-serve token` prints the
token again, `hertzon-serve autostart on` starts it at login (Linux: writes a systemd user service and
prints the commands that enable it). Windows asks once whether Python may accept connections from the
network; allow it for private networks.

## On your laptop

Tools > Options > Remote > **Find in Network ...** lists the computers that share themselves; pick one and
enter the six-digit pairing code its Options show (*Share this computer*). Hertzon fills in address and
token, ticks **Solve on a remote computer** and tests the connection. (Address and token can also be
entered by hand.) From now on *Analyze* sends the analysis to that computer; **Solve on** in the status bar
switches between *This computer* and the remote one at any time.

- the project file and the setup's results folder are uploaded (so a sweep-only run or continued adaptive
  passes start from the existing mesh, as locally);
- progress, adaptive passes and the message log show up live; the field plots of each adaptive pass work
  (their files are fetched in the background, the progress never waits for them);
- *Abort* stops the solver on the other computer;
- when the solver ends, the S-parameters, convergence, mesh and last adaptive fields arrive first and the
  analysis is finished; large field files of the sweeps follow in the background (the Message Manager says
  when they are complete), and the next queued analysis starts after them.

Several users can share one service; their analyses run one after the other. On the solving computer, the
Progress window lists the analyses other computers sent to it (who sent them, progress); select one and
press Abort to stop it.

## Notes

- Cores and parallel sweep processes are those of the **solving** computer: Tools > Options > HPC and
  Analysis there (also what its Solver Benchmark recommends). What travels with the project stays as it is:
  a setup's own *Solver threads* and its matrix solver.
  While *Solve on a remote computer* is on, the laptop's HPC tab greys out its own cores and sweep
  processes and shows the values of the remote computer instead.

- The connection is plain HTTP with a token and meant for a local network. Across the internet use an SSH
  tunnel (`ssh -L 47820:localhost:47820 user@host`, then enter `localhost` as the computer).
- If both computers run different Hertzon versions, the GUI warns; results can differ.
- Closing the GUI aborts a running remote analysis, as it does locally.
- Job folders the GUI did not remove (for example after a lost connection) are deleted by the service after
  seven days.

## Cloud computers (solver image)

Every Hertzon version is also published as a headless container image with the solver and this service,
`ghcr.io/c8501024/hertzon-solver:<version>` (`:dev` for the current development state). A cloud computer
then needs no Hertzon installation, only Docker: it pulls the image and solves at native speed (the
container adds no measurable overhead to the solver; only the first start pulls the image). The numba
kernels are compiled into the image, so the first analysis starts at full speed as well.

By hand, on any Linux computer with Docker (a cloud server of any provider, or a workstation):

```
docker run -d --name hertzon --restart unless-stopped -p 127.0.0.1:47820:47820 \
    -e HERTZON_TOKEN=<a long random token> -v hertzon-work:/work ghcr.io/c8501024/hertzon-solver:<version>
```

then on your laptop `ssh -L 47820:localhost:47820 user@server`, and in Tools > Options > Remote the computer
`localhost` with that token. The service listens only on the server's loopback here, so it is reached only
through the SSH tunnel. Cores and parallel sweep processes: all cores and 2 by default; to change them, append
the command `python -m hertzon.remote serve --cores 16 --sweep-workers 4` to `docker run`.

## Solving in the cloud (Hetzner Cloud)

Hertzon can create such a server itself, use it for the analyses of a session and delete it again; you pay
the provider for the time the server exists and install nothing. Providers: Hetzner Cloud and Amazon Web
Services ([below](#amazon-web-services-aws)) and Google Cloud ([below](#google-cloud)); Microsoft Azure follows.

1. At [console.hetzner.cloud](https://console.hetzner.cloud/) create a project only for Hertzon, then
   Security > API tokens > Generate API token with *Read & Write*.
2. Tools > Options > **Cloud**: paste the token, press **Check Account** (loads locations and server types
   with their prices), choose location and server type (for example ccx33: 8 dedicated vCPUs, 32 GB), tick
   **Solve in the cloud**. **Solve on** in the status bar switches between this computer, a remote computer
   and the cloud at any time.

The first analysis creates the server and waits for it (one to three minutes: the server starts and pulls
the solver image); the progress window shows the steps. Further analyses use it at once. The server is
deleted

- when Hertzon closes,
- by itself, when no analysis ran for *idle* minutes (30), when the solver showed no progress for some hours
  (2: hung), or when no Hertzon was connected for some hours (48: a crashed or disconnected GUI),
- with **Delete Cloud Servers** in the Cloud tab (every server Hertzon created in this project).

For the self-deletion the server gets the API token; that is why a project only for Hertzon is recommended
(the token can do nothing outside it). Hertzon keeps the token in its settings of this user.

The connection is an SSH tunnel: Hertzon creates an SSH key for this computer (in the work folder,
`cloud/id_ed25519`) and a host key for every server, which the server receives at its creation, so the
connection is encrypted and checked from the first packet. The remote service listens only inside the server.
Hertzon uses the `ssh` and `ssh-keygen` programs of the operating system (Windows 10 and later: *OpenSSH
Client*, normally installed; Linux: `openssh-client`).

### Amazon Web Services (AWS)

1. In the [IAM console](https://console.aws.amazon.com/iam/) create a user only for Hertzon (no console
   access) with the policy below, then Security credentials > **Create access key** (use case: application
   outside AWS).
2. Tools > Options > **Cloud**: Provider *Amazon Web Services*, paste the access key ID and the secret access
   key, press **Check Account**, choose the region (for example eu-central-1, Frankfurt) and the instance type.
   The list shows vCPUs, physical cores, memory and the on-demand price in USD per hour. The AMD types
   (`c7a`, `m7a`, `r7a`) have one physical core per vCPU; the Intel ones (`c7i`, `m7i`, `r7i`) two vCPUs per
   core, so an `m7a.2xlarge` (8 cores, 32 GB) solves about as fast as an `m7i.4xlarge`.

Hertzon starts an Ubuntu 24.04 instance in the default VPC of the region with a 30 GB disk, the security
group `hertzon` (SSH only, created on first use) and the tags `hertzon=1` and `hertzon-client`. The first
start also installs Docker, so it takes one to two minutes longer than at Hetzner. The access key stays on
this computer: the instance is started with *terminate on shutdown*, and its watchdog deletes it by shutting
it down. **Delete Cloud Servers** deletes the Hertzon instances of the chosen region.

New AWS accounts may start only a few vCPUs at once. If the analysis reports the vCPU limit, request more in
the AWS console: Service Quotas > Amazon EC2 > *Running On-Demand Standard (A, C, D, H, I, M, R, T, Z)
instances*. If a region has no default VPC: VPC console > Your VPCs > Actions > Create default VPC.

Policy of the Hertzon user (IAM > Policies > Create policy > JSON):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Resource": "*", "Action": [
      "ec2:DescribeRegions", "ec2:DescribeInstanceTypes", "ec2:DescribeInstanceTypeOfferings",
      "ec2:DescribeImages", "ec2:DescribeVpcs", "ec2:DescribeSubnets", "ec2:DescribeSecurityGroups",
      "ec2:DescribeInstances", "ec2:CreateSecurityGroup", "ec2:AuthorizeSecurityGroupIngress",
      "ec2:RunInstances", "ec2:CreateTags", "pricing:GetProducts"]},
    {"Effect": "Allow", "Action": "ec2:TerminateInstances", "Resource": "*",
     "Condition": {"StringEquals": {"ec2:ResourceTag/hertzon": "1"}}}
  ]
}
```

Without `pricing:GetProducts` the types are listed without prices. The user can terminate only instances
tagged `hertzon=1`.

### Google Cloud

1. In the [Google Cloud console](https://console.cloud.google.com/) create a project only for Hertzon (with
   billing) and enable the **Compute Engine API** (APIs & Services > Library).
2. IAM & Admin > Service accounts > **Create service account**, with the roles *Compute Instance Admin (v1)*
   and *Service Account User*. Then the account > Keys > Add key > **JSON**: the browser saves the key file.
3. Tools > Options > **Cloud**: Provider *Google Cloud*, choose the key file with **Browse**, press **Check
   Account**, choose the zone (for example europe-west3-a, Frankfurt) and the machine type. Google's types have
   two vCPUs per core: `c3d-standard-8` is 4 AMD cores with 32 GB, `c3d-standard-16` 8 cores with 64 GB. The
   Compute Engine API has no prices; see [cloud.google.com/compute/vm-instance-pricing](https://cloud.google.com/compute/vm-instance-pricing).

Hertzon starts an Ubuntu 24.04 instance in the project's default network with a 30 GB disk, the firewall rule
`hertzon-ssh` (SSH to instances tagged `hertzon`, created on first use) and the labels `hertzon=1` and
`hertzon-client`; the first start installs Docker. If the zone has no capacity, the other zones of the region
are tried. The key file stays on this computer. The instance runs as the project's default compute service
account, and its watchdog deletes it with that account's token from the metadata server; that is why a
project only for Hertzon is recommended. The server's SSH host key is made by Google's guest agent and read
through the API before the first connection, so the tunnel is checked as on the other providers.

New projects have small CPU quotas per region; raise them in IAM & Admin > Quotas if the analysis reports one.

To build the image yourself: `docker build -f packaging/docker/Dockerfile -t hertzon-solver .` in the
repository folder.
