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
the provider per hour of the server and install nothing. Amazon Web Services, Google Cloud and Microsoft Azure
follow.

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

To build the image yourself: `docker build -f packaging/docker/Dockerfile -t hertzon-solver .` in the
repository folder.
