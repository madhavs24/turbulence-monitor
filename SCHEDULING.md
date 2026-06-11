# Make it run by itself (Windows)

The monitor is a single command — `python run_daily.py`. To make it autonomous, have
Windows Task Scheduler run that command every morning. Two ways:

## Option A — one-line setup (PowerShell, run once)

Open PowerShell **in this folder** and paste (edit the python path if needed):

```powershell
$py  = (Get-Command python).Source
$dir = (Get-Location).Path
$action  = New-ScheduledTaskAction -Execute $py -Argument "run_daily.py" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -Daily -At 7:30am
Register-ScheduledTask -TaskName "TurbulenceMonitor" -Action $action -Trigger $trigger -Description "Daily market turbulence digest"
```

That's it — every day at 7:30am it refreshes data, reads news, and writes a fresh digest
to `results\digest_latest.html`. Open that file each morning.

Change the time by editing `-At 7:30am`. Remove the job with:
```powershell
Unregister-ScheduledTask -TaskName "TurbulenceMonitor" -Confirm:$false
```

## Option B — Task Scheduler GUI
1. Open **Task Scheduler** → **Create Basic Task**.
2. Name it `TurbulenceMonitor`, trigger **Daily**, time **7:30 AM**.
3. Action **Start a program**: Program = path to `python.exe`; Arguments = `run_daily.py`;
   "Start in" = this folder.
4. Finish. Right-click → **Run** to test it once.

## Want it on Telegram instead (MyHermes-style, optional later)
`run_daily.py` already produces a clean digest string. To push it to an always-on Telegram
agent (like myhermes.cloud), have the agent's cron call `python run_daily.py` and send the
contents of `results/digest_latest.md`. The local job stays the source of truth; the agent
is just the delivery channel. We can wire this up when you want it.
