# Register / remove the weekday auto-publish schedule.
#   .\schedule-install.ps1           install the Mon-Fri schedule
#   .\schedule-install.ps1 -Remove   delete all GTKWYD tasks
#
# Default weekday schedule (edit $SLOTS below to change times):
#   09:00  long video   (tries a science-news explainer first, else the bank)
#   10:00  Short
#   15:00  long video   (from the bank)
#   15:30  Short
param([switch]$Remove)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$prefix = "GTKWYD-"
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

# name  time   format  source
$SLOTS = @(
    @{ name = "video-am"; time = "09:00"; fmt = "video"; src = "news" },
    @{ name = "short-am"; time = "10:00"; fmt = "short"; src = "auto" },
    @{ name = "video-pm"; time = "15:00"; fmt = "video"; src = "auto" },
    @{ name = "short-pm"; time = "15:30"; fmt = "short"; src = "auto" }
)

# wipe existing
Get-ScheduledTask -TaskName "$prefix*" -ErrorAction SilentlyContinue | ForEach-Object {
    Unregister-ScheduledTask -TaskName $_.TaskName -Confirm:$false
    Write-Host "removed $($_.TaskName)"
}
if ($Remove) { Write-Host "All GTKWYD tasks removed." -ForegroundColor Green; return }

if (-not (Test-Path $py)) { throw "Run ./setup.ps1 and ./setup-auto.ps1 first." }

foreach ($s in $SLOTS) {
    $arg = "auto.py --slot $($s.name) --format $($s.fmt) --source $($s.src)"
    $action  = New-ScheduledTaskAction -Execute $py -Argument $arg -WorkingDirectory $PSScriptRoot
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $s.time
    # WakeToRun: if the PC is asleep (not shut down) at the scheduled time,
    # Windows wakes it to run the task. AllowStartIfOnBatteries: don't skip the
    # run just because a laptop is unplugged.
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd `
        -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew `
        -WakeToRun -AllowStartIfOnBatteries
    Register-ScheduledTask -TaskName "$prefix$($s.name)" -Action $action -Trigger $trigger `
        -Settings $settings -Description "Get To Know What You Don't - $($s.name)" | Out-Null
    Write-Host "installed $prefix$($s.name)  $($s.time) Mon-Fri  ($($s.fmt), $($s.src))"
}

Write-Host "`nDone. 4 runs per weekday = 2 videos + 2 Shorts." -ForegroundColor Green
Write-Host "Check:   Get-ScheduledTask -TaskName '$prefix*'"
Write-Host "Status:  .\.venv\Scripts\python.exe auto.py --status"
Write-Host "Logs:    Get-Content auto.log -Tail 20"
