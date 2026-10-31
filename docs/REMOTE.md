# Solving on another computer

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
in Hertzon's Python environment; it prints address and token. Options: `--cores N` (default: all cores),
`--sweep-workers N` (default 2), `--port` (default 47820), `--workdir`. `hertzon-serve token` prints the
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

- The connection is plain HTTP with a token and meant for a local network. Across the internet use an SSH
  tunnel (`ssh -L 47820:localhost:47820 user@host`, then enter `localhost` as the computer).
- If both computers run different Hertzon versions, the GUI warns; results can differ.
- Closing the GUI aborts a running remote analysis, as it does locally.
- Job folders the GUI did not remove (for example after a lost connection) are deleted by the service after
  seven days.
- Cloud machines that are created for a job and removed afterwards build on this service; see the roadmap.
