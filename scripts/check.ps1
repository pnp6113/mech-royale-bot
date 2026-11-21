# Usage: .\scripts\check.ps1 [-Mode local|docker|both]   (default: both)
# local  = run the bot directly through the conformance check
# docker = build the image and check it inside the exact match sandbox the scorer uses
param([ValidateSet('local','docker','both')][string]$Mode = 'both')
# Exit codes are checked by hand: Windows PowerShell 5.1 treats docker's progress output on stderr as an
# error under ErrorActionPreference=Stop, so that is deliberately not set here.
Set-Location (Join-Path $PSScriptRoot '..')
if ($Mode -in 'local','both') {
  python kit/check/check.py -- python -u main.py
  if ($LASTEXITCODE) { exit $LASTEXITCODE }
}
if ($Mode -in 'docker','both') {
  docker build -t mech-bot .
  if ($LASTEXITCODE) { exit $LASTEXITCODE }
  $arena = Join-Path (Get-Location) 'kit\arena'
  python kit/check/check.py --map-dir /arena -- docker run --rm -i --network none --cpus 1 --memory 512m `
    --read-only --tmpfs /tmp:rw,size=64m --user 65534:65534 -v "${arena}:/arena:ro" mech-bot
  exit $LASTEXITCODE
}
