# Build each candidate once, then run it with the official kit and strict verifier.
[CmdletBinding()]
param(
  [ValidateSet('A','B','C')][string[]]$Candidate = @('A','B','C'),
  [switch]$SkipBuild
)

$repo = Split-Path -Parent $PSScriptRoot
$kit = Join-Path $repo 'kit'
$arena = Join-Path $kit 'arena'
$specs = @{
  A = @{ Label = 'A / v8'; Image = 'mech-royale-reference-a:verify'; Commit = '7a5737c64af3ca91756f3429a804cba9c0bc1986'; Entry = @('python3','-u','/bot/main.py') }
  B = @{ Label = 'B / Giddy'; Image = 'mech-royale-reference-b:verify'; Controller = 'giddy'; Entry = @('/usr/local/bin/reference-bot') }
  C = @{ Label = 'C / Kanyaraasi'; Image = 'mech-royale-reference-c:verify'; Controller = 'kanyaraasi'; Entry = @('/usr/local/bin/reference-bot') }
}

$buildArchive = @'
import subprocess
import sys

repo, commit, image = sys.argv[1:]
context = subprocess.check_output(['git', '-C', repo, 'archive', '--format=tar', commit])
build = ['docker', 'build', '--platform', 'linux/amd64', '-t', image, '-']
raise SystemExit(subprocess.run(build, input=context).returncode)
'@

$failed = $false
foreach ($id in $Candidate) {
  $spec = $specs[$id]
  if (-not $SkipBuild) {
    if ($spec.Commit) {
      python -c $buildArchive $repo $spec.Commit $spec.Image
    } else {
      docker build --platform linux/amd64 -t $spec.Image -f (Join-Path $repo 'Dockerfile.reference') `
        --target verify --build-arg "CONTROLLER=$($spec.Controller)" $repo
    }
    if ($LASTEXITCODE -ne 0) {
      Write-Warning "Build failed for $($spec.Label)"
      $failed = $true
      continue
    }
  }

  $sandbox = @('run','--rm','--platform','linux/amd64','--network','none','--cpus','1',
    '--memory','512m','--memory-swap','512m','--pids-limit','128','--read-only',
    '--tmpfs','/tmp:rw,size=64m','--user','65534:65534','--cap-drop','ALL',
    '--security-opt','no-new-privileges','-e','PYTHONDONTWRITEBYTECODE=1',
    '-v',"${kit}:/kit:ro",'-v',"${arena}:/arena:ro",'-v',"${PSScriptRoot}:/verify:ro",
    '--entrypoint','python3',$spec.Image,'-u','/verify/reference_verify.py',
    '--kit','/kit','--arena','/arena','--label',$spec.Label,'--') + $spec.Entry
  docker @sandbox
  $failed = $failed -or ($LASTEXITCODE -ne 0)
}
exit ([int]$failed)
