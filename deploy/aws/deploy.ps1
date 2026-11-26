# Despliega WDPDA en AWS (capa gratuita / créditos de cuenta nueva).
# Requisitos: AWS CLI v2 configurada (aws configure) con un usuario que
# pueda crear pilas de CloudFormation, IAM, EC2, S3 y DynamoDB.
#
# Uso (desde la raíz del repo):
#   .\deploy\aws\deploy.ps1
#   .\deploy\aws\deploy.ps1 -Region us-east-1 -Branch main
param(
    [string]$Stack = "wdpda",
    [string]$Region = "us-east-1",
    [string]$Branch = "main",
    [string]$InstanceType = "t3.micro"
)
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")

Write-Host "1/3 Creando o actualizando la pila '$Stack' en $Region..."
aws cloudformation deploy `
    --template-file (Join-Path $PSScriptRoot "wdpda-free-tier.yaml") `
    --stack-name $Stack --region $Region `
    --capabilities CAPABILITY_IAM `
    --parameter-overrides "Branch=$Branch" "InstanceType=$InstanceType"
if ($LASTEXITCODE -ne 0) { throw "Falló la creación de la pila" }

$out = aws cloudformation describe-stacks --stack-name $Stack --region $Region `
    --query "Stacks[0].Outputs" --output json | ConvertFrom-Json
$api = ($out | Where-Object OutputKey -eq "ApiUrl").OutputValue
$front = ($out | Where-Object OutputKey -eq "FrontendUrl").OutputValue
$bucket = ($out | Where-Object OutputKey -eq "FrontendBucketName").OutputValue
$https = ($out | Where-Object OutputKey -eq "HttpsUrl").OutputValue

Write-Host "2/3 Subiendo el frontend a s3://$bucket ..."
$cfg = Join-Path ([IO.Path]::GetTempPath()) "wdpda-config.js"
# Por HTTPS (CloudFront) la API es el mismo origen; por el sitio HTTP de S3
# se llama directo a la instancia.
Set-Content -Path $cfg -Encoding utf8 -Value "window.WDPDA_API = location.protocol === `"https:`" ? `"`" : `"$api`";"
$front_dir = Join-Path $root "web\frontend"
aws s3 cp (Join-Path $front_dir "index.html") "s3://$bucket/index.html" --region $Region --content-type "text/html; charset=utf-8"
aws s3 cp (Join-Path $front_dir "styles.css") "s3://$bucket/static/styles.css" --region $Region --content-type "text/css; charset=utf-8"
aws s3 cp (Join-Path $front_dir "app.js") "s3://$bucket/static/app.js" --region $Region --content-type "application/javascript; charset=utf-8"
aws s3 cp $cfg "s3://$bucket/static/config.js" --region $Region --content-type "application/javascript; charset=utf-8"
Remove-Item $cfg

Write-Host "3/3 Listo."
Write-Host "  HTTPS:    $https   (recomendada)"
Write-Host "  Frontend: $front"
Write-Host "  Backend:  $api   (la primera vez tarda ~10 min en construir la imagen)"
Write-Host "  Salud:    $api/api/health"
