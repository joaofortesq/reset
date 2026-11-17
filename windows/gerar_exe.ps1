# Gera o ManutencaoEpson.exe (arquivo unico) e a versao em pasta (zip).
# Uso (PowerShell, dentro da pasta windows):  .\gerar_exe.ps1
$ErrorActionPreference = "Stop"
$aqui = $PSScriptRoot
$venv = Join-Path $aqui ".venv"
if (-not (Test-Path "$venv\Scripts\python.exe")) {
    python -m venv $venv
    & "$venv\Scripts\python.exe" -m pip install -q pyinstaller==6.22.3
}
$build = Join-Path $aqui "build"
$comum = @("--noconfirm", "--windowed", "--noupx", "--icon", "$aqui\icone.ico", "--version-file", "$aqui\versao.txt",
           "--add-data", "$aqui\modelos.json;.", "--add-data", "$aqui\icone.ico;.",
           "--workpath", $build, "--specpath", $build, "--name", "ManutencaoEpson")
& "$venv\Scripts\pyinstaller.exe" @comum --onefile --distpath "$aqui\dist" "$aqui\app.py"
& "$venv\Scripts\pyinstaller.exe" @comum --onedir --distpath "$aqui\dist_pasta" "$aqui\app.py"
Compress-Archive -Path "$aqui\dist_pasta\ManutencaoEpson" -DestinationPath "$aqui\dist\ManutencaoEpson_pasta.zip" -Force
Write-Host "Pronto: $aqui\dist"
