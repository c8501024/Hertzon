# Solving on another computer

Build and post-process the model in the Hertzon GUI on your laptop and let a stronger computer in your
network do the solving. The other computer runs a normal Hertzon installation plus the small remote service;
the GUI sends each analysis there and gets the results back into the project's results folder, so reports,
field plots and exports work exactly as after a local solve.

## On the computer that solves

1. Install Hertzon as usual (same version as on your laptop).
2. Start the service in a terminal of Hertzon's Python environment:

   ```
   hertzon-serve
   ```

   (or `python -m hertzon.remote serve`). It prints the computer's address and a token:

   ```
   Hertzon 0.2.8 remote service, 64 cores, work folder C:\Users\me\.hertzon\remote
   Address for Tools > Options > Remote: simstation02:47820  or  192.168.1.20:47820
   Token: 3q2k...
   ```

3. Optional: `hertzon-serve autostart on` starts the service with each login (Windows: Startup folder;
   Linux: writes a systemd user service and prints the two commands that enable it).

Options: `--cores N` (default: all cores), `--sweep-workers N` (default 2), `--port` (default 47820),
`--workdir`. `hertzon-serve token` prints the token again. Windows asks once whether the service may accept
connections from the network; allow it for private networks.

## On your laptop

Tools > Options > Remote: tick **Solve on a remote computer**, enter the computer (`simstation02` or
`192.168.1.20:47820`) and the token, and press **Test Connection**. From now on *Analyze* sends the
analysis to that computer:

- the project file and the setup's results folder are uploaded (so a sweep-only run or continued adaptive
  passes start from the existing mesh, as locally);
- progress, adaptive passes and the message log show up live; the field plots of each adaptive pass work;
- *Abort* stops the solver on the other computer;
- the results folder is mirrored back, small files (S-parameters, convergence) first, then the fields.

Several users can share one service; their analyses run one after the other. Untick the option to solve
locally again.

## Notes

- The connection is plain HTTP with a token and meant for a local network. Across the internet use an SSH
  tunnel (`ssh -L 47820:localhost:47820 user@host`, then enter `localhost` as the computer).
- If both computers run different Hertzon versions, the GUI warns; results can differ.
- Closing the GUI aborts a running remote analysis, as it does locally.
- Job folders the GUI did not remove (for example after a lost connection) are deleted by the service after
  seven days.
- Cloud machines that are created for a job and removed afterwards build on this service; see the roadmap.
