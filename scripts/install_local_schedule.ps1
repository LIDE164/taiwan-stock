[CmdletBinding(DefaultParameterSetName = 'Configure')]
param(
    [Parameter(ParameterSetName = 'Configure')]
    [switch]$Install,

    [Parameter(Mandatory = $true, ParameterSetName = 'Launch')]
    [ValidateSet('scan', 'prices')]
    [string]$RunJob,

    [string]$PythonPath = '',
    [string]$RepositoryPath = ''
)

# No registration occurs without -Install. The Launch parameter set is used
# only by the two installed tasks and never registers or starts another task.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-AbsoluteFile([string]$Value) {
    if (-not (Test-Path -LiteralPath $Value -PathType Leaf)) {
        throw 'A required local executable or script is missing.'
    }
    return (Resolve-Path -LiteralPath $Value).ProviderPath
}

function Quote-TaskArgument([string]$Value) {
    if ($Value.Contains('"') -or $Value.Contains("`r") -or $Value.Contains("`n")) {
        throw 'A task argument contains unsupported quoting characters.'
    }
    return '"' + $Value + '"'
}

function Resolve-PythonExecutable([string]$Requested) {
    $candidate = $null
    if ($Requested) {
        if (Test-Path -LiteralPath $Requested -PathType Leaf) {
            $candidate = Get-AbsoluteFile $Requested
        } else {
            $candidate = (Get-Command -Name $Requested -CommandType Application -ErrorAction Stop).Source
        }
    } else {
        foreach ($commandName in @('py', 'python')) {
            $command = Get-Command -Name $commandName -CommandType Application -ErrorAction SilentlyContinue
            if ($command) {
                $candidate = $command.Source
                break
            }
        }
    }
    if (-not $candidate) {
        throw 'Python was not found. Supply -PythonPath with the installed Python executable or py launcher.'
    }
    $resolved = @(& $candidate -c 'import sys; print(sys.executable)' 2>$null)
    if ($LASTEXITCODE -ne 0 -or $resolved.Count -ne 1) {
        throw 'Python executable resolution failed; no scheduled tasks were changed.'
    }
    return Get-AbsoluteFile ([string]$resolved[0])
}

if ((Get-TimeZone).Id -ne 'Taipei Standard Time') {
    throw 'Windows time zone must be Taipei Standard Time. This script will not change system settings.'
}
if (-not $RepositoryPath) {
    # Script automatic variables are reliable here, after parameter binding.
    # Resolve the parent of scripts/, not the caller's current working folder.
    $scriptDirectory = Split-Path -Parent $PSCommandPath
    $RepositoryPath = Split-Path -Parent $scriptDirectory
}
if (-not (Test-Path -LiteralPath $RepositoryPath -PathType Container)) {
    throw 'RepositoryPath must identify an existing project directory.'
}
$repo = (Resolve-Path -LiteralPath $RepositoryPath).ProviderPath.TrimEnd('\')
$runner = Get-AbsoluteFile (Join-Path $repo 'local_schedule.py')

if ($PSCmdlet.ParameterSetName -eq 'Launch') {
    if (-not [System.IO.Path]::IsPathRooted($PythonPath)) {
        throw 'Scheduled execution requires an absolute Python executable path.'
    }
    $python = Get-AbsoluteFile $PythonPath
    $arguments = '-X utf8 ' + (Quote-TaskArgument $runner) + ' --job ' + $RunJob + ' --run'
    # The Python runner has its own shorter subprocess deadline. This outer
    # safeguard kills only the child tree we created, before Task Scheduler's
    # 40/10 minute limit could leave a detached Python process behind.
    $child = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $repo `
        -WindowStyle Hidden -PassThru
    $null = $child.Handle
    $timeoutMinutes = if ($RunJob -eq 'scan') { 38 } else { 8 }
    if (-not $child.WaitForExit($timeoutMinutes * 60 * 1000)) {
        $taskKill = Get-AbsoluteFile (Join-Path $env:SystemRoot 'System32\taskkill.exe')
        & $taskKill /PID ([string]$child.Id) /T /F 2>$null | Out-Null
        Write-Error 'The local scheduled child exceeded its time limit; no forced resend was attempted.' -ErrorAction Continue
        exit 124
    }
    $child.Refresh()
    exit $child.ExitCode
}

$python = Resolve-PythonExecutable $PythonPath
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$ownerSid = $identity.User.Value
$ownerName = $identity.Name
$ownership = 'TaiwanStock.LocalScheduler.v1|repository=' + $repo + '|user=' + $ownerSid
$powershell = Get-AbsoluteFile (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe')
$installer = Get-AbsoluteFile $PSCommandPath

# This local preflight only validates runtime/configuration; it must never
# import scanner, write Firebase, send Telegram, or print secret contents.
Push-Location -LiteralPath $repo
try {
    $null = & $python -X utf8 $runner --check
    if ($LASTEXITCODE -ne 0) {
        throw 'The read-only local_schedule.py --check failed. Fix its reported prerequisites before installing.'
    }
} finally {
    Pop-Location
}

$definitions = @(
    [pscustomobject]@{
        Name = 'TaiwanStock-DailyScan'; Job = 'scan'; LimitMinutes = 40
        Times = @('08:05', '15:17', '16:17', '22:17')
    },
    [pscustomobject]@{
        Name = 'TaiwanStock-PredictionPrices'; Job = 'prices'; LimitMinutes = 10
        Times = @('09:05', '09:20', '09:35', '10:05', '10:20', '11:05', '11:20',
                  '12:05', '12:20', '13:05', '13:20', '13:35', '13:50', '14:05', '14:20')
    }
)
$weekdays = @('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')

# Inspect ownership of BOTH target names before changing either. Enumeration
# failures stop the installer; they must not be mistaken for an absent task.
$existingRootTasks = @(Get-ScheduledTask -TaskPath '\' -ErrorAction Stop)
foreach ($definition in $definitions) {
    $existing = @($existingRootTasks | Where-Object { $_.TaskName -eq $definition.Name })
    if ($existing.Count -gt 1) {
        throw ('Ambiguous task ownership: ' + $definition.Name)
    }
    if ($existing.Count -eq 1 -and (
            $existing[0].Description -ne $ownership -or
            $existing[0].Principal.UserId -notin @($ownerSid, $ownerName))) {
        throw ('Refusing to overwrite a task not owned by this installer and user: ' + $definition.Name)
    }
}

foreach ($definition in $definitions) {
    [pscustomobject]@{
        Mode = if ($Install) { 'Install/update owned task' } else { 'Preview only' }
        TaskName = $definition.Name
        WeekdaysTaipei = ($definition.Times -join ', ')
        User = $ownerName
        Python = $python
        WorkingDirectory = $repo
        RequiresLoggedOnUser = $true
        CatchUpMissedTriggers = $false
        LimitMinutes = $definition.LimitMinutes
    }
}
if (-not $Install) {
    Write-Host 'Preview complete. No scheduled tasks were changed or started. Use -Install to register them.'
    return
}

foreach ($definition in $definitions) {
    $arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy RemoteSigned -File ' +
        (Quote-TaskArgument $installer) + ' -RunJob ' + $definition.Job + ' -PythonPath ' +
        (Quote-TaskArgument $python) + ' -RepositoryPath ' + (Quote-TaskArgument $repo)
    $action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments -WorkingDirectory $repo
    $triggers = @($definition.Times | ForEach-Object {
        New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek $weekdays -At $_
    })
    $principal = New-ScheduledTaskPrincipal -UserId $ownerName -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RunOnlyIfNetworkAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes $definition.LimitMinutes)
    $settings.StartWhenAvailable = $false
    $settings.WakeToRun = $false
    $task = New-ScheduledTask -Action $action -Trigger $triggers -Principal $principal `
        -Settings $settings -Description $ownership

    # Recheck immediately before overwriting an owned task. A new task is
    # registered without -Force so a newly appeared unrelated task is not lost.
    $existing = @(Get-ScheduledTask -TaskPath '\' -ErrorAction Stop |
        Where-Object { $_.TaskName -eq $definition.Name })
    if ($existing.Count -eq 1) {
        if ($existing[0].Description -ne $ownership -or
                $existing[0].Principal.UserId -notin @($ownerSid, $ownerName)) {
            throw ('Task ownership changed; stopping without overwrite: ' + $definition.Name)
        }
        $null = Register-ScheduledTask -TaskName $definition.Name -TaskPath '\' -InputObject $task -Force
    } elseif ($existing.Count -eq 0) {
        $null = Register-ScheduledTask -TaskName $definition.Name -TaskPath '\' -InputObject $task
    } else {
        throw ('Ambiguous task ownership: ' + $definition.Name)
    }
}
Write-Host 'Owned tasks registered. No task was run. GitHub backup schedules and system power settings are unchanged.'
