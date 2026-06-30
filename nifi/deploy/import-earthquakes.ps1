# Deploy OSIRIS USGS earthquakes NiFi flow via REST API (curl.exe + temp JSON files).
# Usage: powershell -File nifi/deploy/import-earthquakes.ps1

$ErrorActionPreference = "Stop"
$Base = "https://localhost:8443/nifi-api"
$User = "admin"
$Pass = "osirisadmin1"
$UsgsUrl = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson"
$ScriptFile = "/opt/nifi/conf/osiris/scripts/earthquakes-ingest.groovy"
$ExportPath = Join-Path $PSScriptRoot "..\flows\osiris-earthquakes.json"

function Get-Token {
    $t = curl.exe -sk -X POST "$Base/access/token" `
        -H "Content-Type: application/x-www-form-urlencoded" `
        -d "username=$User&password=$Pass"
    if (-not $t -or $t.Length -lt 20) { throw "Failed to get NiFi token" }
    return $t.Trim()
}

function Invoke-NifiJson {
    param([string]$Method, [string]$Path, [string]$JsonFile = $null)
    $args = @("-sk", "-X", $Method, "$Base$Path", "-H", "Authorization: Bearer $Token", "-H", "Content-Type: application/json")
    if ($JsonFile) { $args += @("--data-binary", "@$JsonFile") }
    $out = (curl.exe @args 2>$null) -join ""
    if ($out -match "Unable to parse") { throw "NiFi API error: $out" }
    if (-not $out -or $out.Trim().Length -eq 0) { return $null }
    return $out | ConvertFrom-Json
}

function Write-JsonTemp($obj) {
    $f = [System.IO.Path]::GetTempFileName()
    $json = $obj | ConvertTo-Json -Depth 12 -Compress
    [System.IO.File]::WriteAllText($f, $json, [System.Text.UTF8Encoding]::new($false))
    return $f
}

Write-Host "Waiting for NiFi..."
for ($i = 0; $i -lt 40; $i++) {
    try { $script:Token = Get-Token; break } catch { Start-Sleep -Seconds 3 }
    if ($i -eq 39) { throw "NiFi not reachable" }
}
Write-Host "NiFi online."

$root = Invoke-NifiJson -Method GET -Path "/flow/process-groups/root"
$rootId = $root.processGroupFlow.id

$pgFile = Write-JsonTemp @{
    revision = @{ version = 0 }
    component = @{ name = "OSIRIS - Earthquakes (USGS)"; position = @{ x = 400; y = 200 } }
}
$pg = Invoke-NifiJson -Method POST -Path "/process-groups/$rootId/process-groups" -JsonFile $pgFile
Remove-Item $pgFile
$pgId = $pg.id
Write-Host "Process group: $pgId"

function New-Proc($name, $type, $artifact, $x, $y) {
    $f = Write-JsonTemp @{
        revision = @{ version = 0 }
        component = @{
            name = $name; type = $type
            bundle = @{ group = "org.apache.nifi"; artifact = $artifact; version = "2.0.0" }
            position = @{ x = $x; y = $y }
        }
    }
    $p = Invoke-NifiJson -Method POST -Path "/process-groups/$pgId/processors" -JsonFile $f
    Remove-Item $f
    return $p
}

function Set-Proc($proc, $config) {
    $f = Write-JsonTemp @{ revision = $proc.revision; component = @{ id = $proc.id; config = $config } }
    Invoke-NifiJson -Method PUT -Path "/processors/$($proc.id)" -JsonFile $f | Out-Null
    Remove-Item $f
}

function Connect($srcId, $rel, $dstId) {
    $f = Write-JsonTemp @{
        revision = @{ version = 0 }
        component = @{
            source = @{ id = $srcId; groupId = $pgId; type = "PROCESSOR" }
            destination = @{ id = $dstId; groupId = $pgId; type = "PROCESSOR" }
            selectedRelationships = @($rel)
        }
    }
    Invoke-NifiJson -Method POST -Path "/process-groups/$pgId/connections" -JsonFile $f | Out-Null
    Remove-Item $f
}

$gen = New-Proc "Poll USGS (5 min)" "org.apache.nifi.processors.standard.GenerateFlowFile" "nifi-standard-nar" 0 200
Set-Proc $gen @{
    schedulingStrategy = "CRON_DRIVEN"; schedulingPeriod = "0 0/5 * * * ?"; concurrentlySchedulableTaskCount = 1
    properties = @{ "Batch Size" = "1" }; autoTerminatedRelationships = @("failure")
} | Out-Null

$http = New-Proc "GET USGS GeoJSON" "org.apache.nifi.processors.standard.InvokeHTTP" "nifi-standard-nar" 400 200
Set-Proc $http @{
    autoTerminatedRelationships = @("failure", "no retry", "retry")
} | Out-Null

$scr = New-Proc "Transform to PolyBolos" "org.apache.nifi.processors.script.ExecuteScript" "nifi-scripting-nar" 800 200
Set-Proc $scr @{
    properties = @{ "Script Engine" = "Groovy"; "Script File" = $ScriptFile }
    autoTerminatedRelationships = @("failure")
} | Out-Null

$kafka = New-Proc "Publish osiris.entities" "org.apache.nifi.processors.kafka.publish.PublishKafka" "nifi-kafka-nar" 1200 200
Set-Proc $kafka @{
    properties = @{
        "Kafka Brokers" = "osiris-kafka:9092"; "Topic Name" = "osiris.entities"
        "Delivery Guarantee" = "1"; "Use Transactions" = "false"
        "Message Key Field" = "kafka.key"; "Character Set" = "UTF-8"
    }
    autoTerminatedRelationships = @("failure")
} | Out-Null

Connect $gen.id "success" $http.id
Connect $http.id "response" $scr.id
Connect $scr.id "success" $kafka.id

$pgState = Invoke-NifiJson -Method GET -Path "/process-groups/$pgId"
$startFile = Write-JsonTemp @{ revision = $pgState.revision; id = $pgId; state = "RUNNING" }
curl.exe -sk -X PUT "$Base/flow/process-groups/$pgId" -H "Authorization: Bearer $Token" -H "Content-Type: application/json" --data-binary "@$startFile" | Out-Null
Remove-Item $startFile

New-Item -ItemType Directory -Force -Path (Split-Path $ExportPath) | Out-Null
curl.exe -sk -H "Authorization: Bearer $Token" -o $ExportPath "$Base/process-groups/$pgId/download"
Write-Host "Exported: $ExportPath"
Write-Host "Done. https://localhost:8443/nifi/"
