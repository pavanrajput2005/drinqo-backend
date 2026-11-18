# UNEXECUTED TEMPLATE. Only run after the documented independent host review.
# Does not install dependencies, register services, edit project settings, or delete directories.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('Prepare','Provision','Test','Verify','DropTestDatabase')]
    [string]$Action,
    [string]$RunDirectory,
    [string]$HostApprovalFile,
    [Parameter(Mandatory=$true)]
    [ValidateSet('OFFLINE-DRINQO-VM')]
    [string]$ConfirmOfflineVM,
    [switch]$ApproveProvision,
    [switch]$ApproveTests,
    [switch]$ApproveDrop
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$source = 'D:\drinqo_backend'
$testRoot = 'D:\drinqo_otp_test_runs'
$python = Join-Path $source 'venv\Scripts\python.exe'
$worker = Join-Path $PSScriptRoot 'isolated_run.py'
$pgBin = 'C:\Program Files\PostgreSQL\18\bin'

function Require([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

function Confirm-Guest {
    $computer = Get-CimInstance Win32_ComputerSystem
    Require ($computer.Manufacturer -eq 'Microsoft Corporation' -and $computer.Model -eq 'Virtual Machine') 'Expected the approved Hyper-V guest; refusing this host.'
    $up = @(Get-NetAdapter -IncludeHidden | Where-Object Status -eq 'Up')
    $routes = @(Get-NetRoute | Where-Object { $_.DestinationPrefix -in @('0.0.0.0/0','::/0') })
    Require ($up.Count -eq 0 -and $routes.Count -eq 0) 'Guest network adapters/routes must be disconnected.'
    # These observations are corroboration; the Python worker also verifies
    # a fresh external host review tied to guest UUID and development-host UUID.
}

function Invoke-Bounded([string]$Executable, [string[]]$Arguments, [int]$Seconds, [string]$LogName) {
    Confirm-Guest
    $stdout = Join-Path $RunDirectory "$LogName.stdout.log"
    $stderr = Join-Path $RunDirectory "$LogName.stderr.log"
    Require (-not (Test-Path -LiteralPath $stdout) -and -not (Test-Path -LiteralPath $stderr)) 'Refusing to overwrite run logs.'
    $process = Start-Process -FilePath $Executable -ArgumentList $Arguments -WorkingDirectory $source -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    $started = $process.StartTime
    if (-not $process.WaitForExit($Seconds * 1000)) {
        # Kill only the process tree belonging to the child created above.
        # The Python worker's kill-on-close Job Object contains its descendants.
        $current = Get-Process -Id $process.Id -ErrorAction SilentlyContinue
        if ($null -ne $current -and $current.StartTime -eq $started) {
            & taskkill.exe /PID $process.Id /T /F | Out-Null
            if ($LASTEXITCODE -ne 0) { throw 'Unable to terminate the owned timed-out process tree; manual isolated-VM intervention required.' }
        }
        throw 'Execution deadline exceeded. Preserve the cluster; never retry against another database.'
    }
    $process.Refresh()
    Require ($process.ExitCode -eq 0) 'Approved action failed. Preserve the run; do not reuse or automatically clean it.'
}

function Worker([string]$Mode, [string]$Approval, [int]$Seconds, [string]$LogName, [string[]]$Extra=@()) {
    $arguments = @('-I', '-B', $worker, $Mode, '--run-directory', $RunDirectory, '--approval', $Approval) + $Extra
    Invoke-Bounded $python $arguments $Seconds $LogName
}

function Write-TestSupervisorSuccess {
    $runnerSuccess = Join-Path $RunDirectory 'runner-success.json'
    $supervisorSuccess = Join-Path $RunDirectory 'supervisor-success.json'
    Require (Test-Path -LiteralPath $runnerSuccess) 'Test worker exited cleanly without its success record; preserve the run.'
    Require (-not (Test-Path -LiteralPath $supervisorSuccess)) 'Terminal test success attestation already exists.'
    # This runs only after Invoke-Bounded observed the Python worker's zero exit.
    # A timeout, nonzero exit, or supervisor interruption creates no attestation.
    [ordered]@{
        worker_exit_code = 0
        attested_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
        attester = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    } | ConvertTo-Json | Set-Content -LiteralPath $supervisorSuccess -Encoding utf8 -NoNewline
}

Confirm-Guest
Require (Test-Path -LiteralPath $python) 'Approved Python virtualenv is missing.'
Require (Test-Path -LiteralPath $worker) 'Launcher worker is missing.'
# Clear inherited libpq and provider routing without displaying values.
Get-ChildItem Env: | Where-Object { $_.Name -match '^(PG|DB_|DRINQO_TEST_|DJANGO_|SIMPLE_JWT|MSG91_|GOOGLE_|AWS_|AZURE_)' } | ForEach-Object { Remove-Item -LiteralPath "Env:$($_.Name)" }
foreach ($name in @('PYTHONPATH','PYTHONHOME','PYTHONSTARTUP','PYTHONUSERBASE','PYTHONINSPECT')) {
    Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
}
$env:PYTHON_DOTENV_DISABLED = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONSAFEPATH = '1'

if ($Action -eq 'Prepare') {
    Require $ApproveProvision.IsPresent 'Preparation/provisioning approval is required.'
    Require (-not [string]::IsNullOrWhiteSpace($HostApprovalFile)) 'Independent host approval JSON is required.'
    Require ($HostApprovalFile -match '^D:\\drinqo_test_control\\[A-Za-z0-9_-]+\.json$') 'Host approval must be a local JSON file directly under D:\drinqo_test_control; UNC paths are forbidden.'
    Require (((Get-Item -LiteralPath 'D:\drinqo_test_control').Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'Approval directory must not redirect elsewhere.'
    Require (((Get-Item -LiteralPath $HostApprovalFile).Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'Approval file must not redirect elsewhere.'
    $HostApprovalFile = (Resolve-Path -LiteralPath $HostApprovalFile).Path
    Require ($HostApprovalFile -notmatch '\s') 'Use a no-space path for the external host approval artifact.'
    $approval = Get-Content -LiteralPath $HostApprovalFile -Raw | ConvertFrom-Json
    $uuid = (Get-CimInstance Win32_ComputerSystemProduct).UUID
    Require ($approval.approved -eq $true -and $approval.guest_uuid -eq $uuid -and $approval.guest_computer -eq $env:COMPUTERNAME) 'Independent host review does not identify this approved guest.'
    Require ($approval.all_vm_adapters_disconnected -eq $true -and $approval.no_host_drives_or_database_mounts -eq $true -and $approval.sanitized_source_and_dependencies -eq $true) 'Required independent isolation attestations are missing.'
    Require ($approval.development_host_uuid -and $approval.development_host_uuid -ne $uuid) 'Guest must differ from independently recorded development host.'
    $age = [DateTimeOffset]::UtcNow - [DateTimeOffset]::Parse($approval.reviewed_at_utc)
    Require ($age.TotalSeconds -ge 0 -and $age.TotalHours -le 24) 'Independent host approval must be fresh within 24 hours.'
    Require (-not (Test-Path -LiteralPath (Join-Path $source '.env'))) 'Sanitized VM source must omit .env.'
    Require (@(Get-NetTCPConnection -State Listen -LocalPort 55432 -ErrorAction SilentlyContinue).Count -eq 0) 'Port 55432 is already occupied.'
    if (-not (Test-Path -LiteralPath $testRoot)) { New-Item -ItemType Directory -Path $testRoot | Out-Null }
    Require (((Get-Item -LiteralPath $testRoot).Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'Test root must not be a junction or symbolic link.'
    $runId = [guid]::NewGuid().ToString('N').Substring(0,12)
    $RunDirectory = Join-Path $testRoot "otp_$runId"
    Require (-not (Test-Path -LiteralPath $RunDirectory)) 'Run directory already exists.'
    New-Item -ItemType Directory -Path $RunDirectory | Out-Null
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    & icacls.exe $RunDirectory /inheritance:r /grant:r "$($identity):(OI)(CI)F" 'SYSTEM:(OI)(CI)F' | Out-Null
    Require ($LASTEXITCODE -eq 0) 'Private run directory ACL failed.'
    Worker 'prepare-manifest' 'APPROVE_OFFLINE_PREPARATION' 60 '01-manifest' @('--host-approval-file',$HostApprovalFile)
    $data = Join-Path $RunDirectory 'pgdata'
    Invoke-Bounded (Join-Path $pgBin 'initdb.exe') @('-D',$data,'--username=drinqo_test_bootstrap','--auth-host=scram-sha-256','--auth-local=scram-sha-256','--encoding=UTF8','--locale=C',"--pwfile=$(Join-Path $RunDirectory 'initdb-password.txt')") 60 '02-initdb'
    Remove-Item -LiteralPath (Join-Path $RunDirectory 'initdb-password.txt')
    Worker 'record-control' 'APPROVE_OFFLINE_PREPARATION' 30 '03-control'
    $tag = "drinqo_otp_$runId"
    $options = "-h 127.0.0.1 -p 55432 -c cluster_name=$tag -c max_connections=40 -c deadlock_timeout=1s -c lock_timeout=5s -c statement_timeout=30s -c idle_in_transaction_session_timeout=60s -c log_statement=none -c log_min_error_statement=panic -c log_parameter_max_length_on_error=0"
    # Explicit quoting is necessary for pg_ctl's single -o argument.
    Invoke-Bounded (Join-Path $pgBin 'pg_ctl.exe') @('-D',$data,'-l',(Join-Path $RunDirectory 'postgres.log'),'-o',('"' + $options + '"'),'-w','-t','30','start') 45 '04-start'
    Write-Output "Prepared cluster only. Review run $RunDirectory before separately approving Provision and Test."
    exit 0
}

Require (-not [string]::IsNullOrWhiteSpace($RunDirectory)) 'Existing unique run directory is required.'
$RunDirectory = (Resolve-Path -LiteralPath $RunDirectory).Path
Require ($RunDirectory -match '^D:\\drinqo_otp_test_runs\\otp_[0-9a-f]{12}$') 'Unexpected run directory.'
Require (((Get-Item -LiteralPath $RunDirectory).Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'Run directory must not redirect elsewhere.'
switch ($Action) {
    'Provision' { Require $ApproveProvision.IsPresent 'Provisioning approval required.'; Worker 'provision' 'APPROVE_ISOLATED_PROVISIONING' 120 '05-provision' }
    'Verify' { Worker 'verify' 'APPROVE_ISOLATED_IDENTITY_READ' 30 ('verify-' + [guid]::NewGuid().ToString('N')) }
    'Test' {
        Require $ApproveTests.IsPresent 'Separate explicit test/migration approval required.'
        Worker 'test' 'APPROVE_ISOLATED_TEST_EXECUTION' 1200 '06-tests'
        Write-TestSupervisorSuccess
    }
    'DropTestDatabase' { Require $ApproveDrop.IsPresent 'Separate pinned successful-test teardown approval required.'; Worker 'drop' 'APPROVE_PINNED_SUCCESSFUL_TEST_DATABASE_DROP' 45 '07-drop' }
}
