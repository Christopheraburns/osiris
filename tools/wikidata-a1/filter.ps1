#Requires -Version 5.1
<#
.SYNOPSIS
  A1 step 1 — class-filter the full Wikidata JSON dump down to the five classes.

.DESCRIPTION
  Streaming pass: decompress .gz -> wikibase-dump-filter -> compress .gz
  Uses .NET GZipStream (no external gzip required on Windows).

  Prereq:
    npm install -g wikibase-dump-filter
    https://dumps.wikimedia.org/wikidatawiki/entities/latest-all.json.gz

.EXAMPLE
  .\filter.ps1 latest-all.json.gz filtered.ndjson.gz
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Dump = "latest-all.json.gz",

    [Parameter(Position = 1)]
    [string]$Out = "filtered.ndjson.gz"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Country | Airline | Ship | Organization(business+org) | Human
$Classes = "Q6256,Q46970,Q11446,Q4830453,Q43229,Q5"

function Resolve-WikibaseDumpFilter {
    $cmd = Get-Command wikibase-dump-filter -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $npmBin = Join-Path $env:APPDATA "npm\wikibase-dump-filter.cmd"
    if (Test-Path -LiteralPath $npmBin) { return $npmBin }

    throw "wikibase-dump-filter not found. Run: npm install -g wikibase-dump-filter"
}

function Open-InputStream([string]$Path) {
    $fs = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
    if ($Path -match '\.gz$') {
        return [System.IO.Compression.GZipStream]::new($fs, [System.IO.Compression.CompressionMode]::Decompress)
    }
    return $fs
}

function Open-OutputStream([string]$Path) {
    $fs = [System.IO.File]::Open($Path, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    if ($Path -match '\.gz$') {
        return [System.IO.Compression.GZipStream]::new($fs, [System.IO.Compression.CompressionMode]::Compress)
    }
    return $fs
}

function Copy-Stream([System.IO.Stream]$From, [System.IO.Stream]$To) {
    $buf = New-Object byte[] 65536
    while (($n = $From.Read($buf, 0, $buf.Length)) -gt 0) {
        $To.Write($buf, 0, $n)
    }
}

if (-not (Test-Path -LiteralPath $Dump)) {
    throw "Dump file not found: $Dump"
}

$filterExe = Resolve-WikibaseDumpFilter
$claimArg = "P31:$Classes"

Write-Host "Input : $Dump"
Write-Host "Output: $Out"
Write-Host "Filter: $filterExe --claim `"$claimArg`""

$inputStream = Open-InputStream -Path $Dump
$outputStream = Open-OutputStream -Path $Out

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $filterExe
$psi.Arguments = "--claim `"$claimArg`""
$psi.UseShellExecute = $false
$psi.RedirectStandardInput = $true
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.CreateNoWindow = $true

$proc = [System.Diagnostics.Process]::Start($psi)
if (-not $proc) { throw "Failed to start wikibase-dump-filter." }

$stdin = $proc.StandardInput.BaseStream
$stdout = $proc.StandardOutput.BaseStream

$pipeState = @{
    InputStream  = $inputStream
    OutputStream = $outputStream
    Stdin        = $stdin
    Stdout       = $stdout
}

$feedStart = [System.Threading.ParameterizedThreadStart]{
    param($s)
    try { Copy-Stream -From $s.InputStream -To $s.Stdin }
    finally { $s.Stdin.Close() }
}

$drainStart = [System.Threading.ParameterizedThreadStart]{
    param($s)
    try { Copy-Stream -From $s.Stdout -To $s.OutputStream }
    finally {
        $s.OutputStream.Flush()
        $s.OutputStream.Close()
    }
}

$feedThread = [System.Threading.Thread]::new($feedStart)
$drainThread = [System.Threading.Thread]::new($drainStart)
$feedThread.Start($pipeState)
$drainThread.Start($pipeState)

$stderr = $proc.StandardError.ReadToEnd()
$proc.WaitForExit()
$feedThread.Join()
$drainThread.Join()
$inputStream.Dispose()

if ($proc.ExitCode -ne 0) {
    if ($stderr.Trim()) { Write-Error $stderr }
    throw "wikibase-dump-filter exited with code $($proc.ExitCode)."
}
if ($stderr.Trim()) { Write-Host $stderr }

Write-Host "filtered class subset -> $Out"
Write-Host "next: python transform.py $Out import   (set WIKIDATA_HUMANS=all for the unbounded variant)"