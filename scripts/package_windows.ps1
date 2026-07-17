[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$')]
    [string] $Version,

    [string] $OutputDirectory = '.'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$output = [System.IO.Path]::GetFullPath((Join-Path $root $OutputDirectory))
$oneFileExecutable = Join-Path $root 'dist\mcpclient.exe'
$oneDirDirectory = Join-Path $root 'dist\mcpclient-onedir'
$readme = Join-Path $root 'README.md'

foreach ($required in @($oneFileExecutable, $oneDirDirectory, $readme)) {
    if (-not (Test-Path $required)) {
        throw "Required package input does not exist: $required"
    }
}

$validatedVersion = (& python (Join-Path $PSScriptRoot 'version.py') get).Trim()
if ($LASTEXITCODE -ne 0) {
    throw 'Unable to validate version_info.txt.'
}
if ($validatedVersion -ne $Version) {
    throw "Requested package version $Version does not match version_info.txt ($validatedVersion)."
}

New-Item -ItemType Directory -Path $output -Force | Out-Null
$oneFileArchive = Join-Path $output "mcpclient-windows-$Version.zip"
$oneDirArchive = Join-Path $output "mcpclient-windows-onedir-$Version.zip"
$checksums = Join-Path $output 'SHA256SUMS-windows.txt'
Remove-Item $oneFileArchive, $oneDirArchive, $checksums -Force -ErrorAction SilentlyContinue

Push-Location $root
try {
    Compress-Archive -Path 'dist\mcpclient.exe', 'README.md' -DestinationPath $oneFileArchive
    Compress-Archive -Path 'dist\mcpclient-onedir', 'README.md' -DestinationPath $oneDirArchive
}
finally {
    Pop-Location
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
function Assert-ArchiveEntry {
    param([string] $ArchivePath, [string] $Entry)
    $archive = [System.IO.Compression.ZipFile]::OpenRead($ArchivePath)
    try {
        if ($archive.Entries.FullName -notcontains $Entry) {
            throw "Archive $(Split-Path $ArchivePath -Leaf) does not contain required root entry: $Entry"
        }
    }
    finally {
        $archive.Dispose()
    }
}

Assert-ArchiveEntry -ArchivePath $oneFileArchive -Entry 'mcpclient.exe'
Assert-ArchiveEntry -ArchivePath $oneFileArchive -Entry 'README.md'
Assert-ArchiveEntry -ArchivePath $oneDirArchive -Entry 'mcpclient-onedir/mcpclient.exe'
Assert-ArchiveEntry -ArchivePath $oneDirArchive -Entry 'README.md'

Get-FileHash $oneFileArchive, $oneDirArchive -Algorithm SHA256 |
    ForEach-Object { "$($_.Hash.ToLowerInvariant())  $(Split-Path $_.Path -Leaf)" } |
    Set-Content -Path $checksums -Encoding ascii

Write-Host "Created Windows release archives in $output"
Get-Content $checksums
